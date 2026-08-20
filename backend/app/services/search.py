from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_content import (
    Child,
    ChildKnowledgeBasePublication,
    ChildPublicationStatus,
    ChildRevision,
    ChildRevisionQuestionVariant,
    HelpfulFeedbackEvent,
    ParentLexicalRule,
    ParentLexicalRuleType,
    ParentRevision,
    ReviewSubmission,
    ReviewSubmissionKind,
    ReviewSubmissionStatus,
    SearchEvent,
    SearchQueryMode,
    SearchResultItem,
)
from app.services.ocr import OcrRecognition
from app.services.retrieval import (
    IndexQuery,
    SearchIndexBackend,
    SearchIndexUnavailableError,
)


class SearchKnowledgeBaseUnavailableError(Exception):
    pass


class SearchEventNotFoundError(Exception):
    pass


class SearchResultNotFoundError(Exception):
    pass


class SearchResultStaleError(Exception):
    pass


@dataclass(frozen=True)
class SearchCandidate:
    publication: ChildKnowledgeBasePublication
    knowledge_base: KnowledgeBase
    child: Child
    child_revision: ChildRevision
    question_variants: list[ChildRevisionQuestionVariant]
    parent_revision: ParentRevision
    lexical_rules: list[ParentLexicalRule]


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: SearchCandidate
    score: float
    match_reason: str


@dataclass(frozen=True)
class SearchDetails:
    event: SearchEvent
    groups: list[tuple[ParentRevision, list[tuple[SearchResultItem, SearchCandidate]]]]
    no_match_guidance: str | None


NO_MATCH_GUIDANCE = "未找到足够相关的知识，请转研发查询。"
NO_FILTER_MATCH_GUIDANCE = "未找到符合字段条件的知识。"
SEARCH_THRESHOLD = 0.22
MAX_FALLBACK_PARENTS = 3
HELPFUL_SCORE_CAP = 0.12


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _matches_field_filters(
    candidate: SearchCandidate,
    *,
    parent_type: str | None,
    question_type: str | None,
    business_object: str | None,
    purpose: str | None,
    customer_type: str | None,
) -> bool:
    filters = (
        (candidate.parent_revision.name, parent_type),
        (candidate.child_revision.question_type, question_type),
        (candidate.child_revision.business_object, business_object),
        (candidate.child_revision.purpose, purpose),
        (candidate.child_revision.customer_type, customer_type),
    )
    return all(
        expected is None or _normalize_text(actual) == _normalize_text(expected)
        for actual, expected in filters
    )


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", value.casefold()))


def _channel_score(query: str, candidate: SearchCandidate) -> tuple[float, str]:
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return 0.0, "semantic"
    question = _normalize_text(candidate.child_revision.question)
    variants = [_normalize_text(item.question_text) for item in candidate.question_variants]
    response = _normalize_text(candidate.child_revision.response_content)
    if normalized_query == question or normalized_query in question:
        return 1.0, "question_exact"
    if any(normalized_query == variant or normalized_query in variant for variant in variants):
        return 0.92, "question_variant"

    query_tokens = _tokens(normalized_query)
    searchable_tokens = _tokens(" ".join([question, *variants, response]))
    overlap = len(query_tokens & searchable_tokens) / max(len(query_tokens), 1)
    response_hit = 0.25 if normalized_query in response else 0.0
    score = min(0.86, overlap * 0.72 + response_hit)
    return score, "semantic" if score else "semantic"


def _parent_keyword_match(query: str, candidate: SearchCandidate) -> tuple[int, str] | None:
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return None
    canonical = _normalize_text(candidate.parent_revision.canonical_keyword)
    if canonical and canonical in normalized_query:
        return 3, "canonical_keyword"
    for rule in candidate.lexical_rules:
        if rule.rule_type == ParentLexicalRuleType.ALIAS:
            if _normalize_text(rule.rule_value) in normalized_query:
                return 2, "alias_keyword"
        else:
            try:
                if re.search(rule.rule_value, normalized_query):
                    return 1, "regex_keyword"
            except re.error:
                # Validation prevents this for newly-created data; ignoring a
                # legacy malformed rule keeps search available.
                continue
    return None


def _ocr_controlled_keyword_match(
    recognition: OcrRecognition,
    candidate: SearchCandidate,
) -> tuple[int, str] | None:
    """Match OCR only against exact canonical keywords or aliases.

    OCR is inherently noisier than user-entered text, so its fallback path must
    never evaluate a configurable regular expression. Chinese terms have no
    reliable whitespace boundaries, while ASCII terms retain word boundaries.
    """

    normalized_text = _normalize_text(recognition.text)
    normalized_keywords = {_normalize_text(value) for value in recognition.keywords}

    def matches_controlled_term(value: str) -> bool:
        normalized_term = _normalize_text(value)
        if not normalized_term:
            return False
        if normalized_term in normalized_keywords:
            return True
        if re.fullmatch(r"[a-z0-9_]+", normalized_term):
            return re.search(
                rf"(?<![a-z0-9_]){re.escape(normalized_term)}(?![a-z0-9_])",
                normalized_text,
            ) is not None
        return normalized_term in normalized_text

    if matches_controlled_term(candidate.parent_revision.canonical_keyword):
        return 3, "canonical_keyword"
    for rule in candidate.lexical_rules:
        if rule.rule_type == ParentLexicalRuleType.ALIAS and matches_controlled_term(
            rule.rule_value
        ):
            return 2, "alias_keyword"
    return None


def _helpful_bonus(helpful_count: int) -> float:
    return min(HELPFUL_SCORE_CAP, 0.03 * math.log1p(max(helpful_count, 0)))


async def _load_candidates(
    session: AsyncSession,
    *,
    knowledge_base_id: UUID | None,
) -> list[SearchCandidate]:
    statement = (
        select(ChildKnowledgeBasePublication, KnowledgeBase, Child, ChildRevision)
        .join(KnowledgeBase, KnowledgeBase.id == ChildKnowledgeBasePublication.knowledge_base_id)
        .join(Child, Child.id == ChildKnowledgeBasePublication.child_id)
        .join(ChildRevision, ChildRevision.id == ChildKnowledgeBasePublication.active_revision_id)
        .where(
            ChildKnowledgeBasePublication.status == ChildPublicationStatus.PUBLISHED,
            ChildKnowledgeBasePublication.active_revision_id.is_not(None),
            KnowledgeBase.is_active.is_(True),
        )
        .order_by(ChildKnowledgeBasePublication.updated_at.desc())
    )
    if knowledge_base_id is not None:
        statement = statement.where(
            ChildKnowledgeBasePublication.knowledge_base_id == knowledge_base_id
        )
    rows = (await session.execute(statement)).all()
    if not rows:
        return []

    parent_ids = {child.parent_id for _publication, _kb, child, _revision in rows}
    published_parent_rows = (
        await session.execute(
            select(ReviewSubmission, ParentRevision)
            .join(ParentRevision, ParentRevision.id == ReviewSubmission.parent_revision_id)
            .where(
                ReviewSubmission.parent_id.in_(parent_ids),
                ReviewSubmission.submission_kind == ReviewSubmissionKind.PARENT_WITH_PRIMARY,
                ReviewSubmission.status == ReviewSubmissionStatus.PUBLISHED,
            )
            .order_by(ReviewSubmission.submitted_at.desc(), ReviewSubmission.id.desc())
        )
    ).all()
    parent_revisions: dict[UUID, ParentRevision] = {}
    for _submission, parent_revision in published_parent_rows:
        parent_revisions.setdefault(parent_revision.parent_id, parent_revision)

    revision_ids = {revision.id for _submission, revision in published_parent_rows}
    rules_by_revision: dict[UUID, list[ParentLexicalRule]] = {}
    if revision_ids:
        rules = await session.scalars(
            select(ParentLexicalRule)
            .where(ParentLexicalRule.parent_revision_id.in_(revision_ids))
            .order_by(ParentLexicalRule.parent_revision_id, ParentLexicalRule.sort_order)
        )
        for rule in rules:
            rules_by_revision.setdefault(rule.parent_revision_id, []).append(rule)

    child_revision_ids = {revision.id for _publication, _kb, _child, revision in rows}
    variants_by_revision: dict[UUID, list[ChildRevisionQuestionVariant]] = {}
    variants = await session.scalars(
        select(ChildRevisionQuestionVariant)
        .where(ChildRevisionQuestionVariant.child_revision_id.in_(child_revision_ids))
        .order_by(
            ChildRevisionQuestionVariant.child_revision_id,
            ChildRevisionQuestionVariant.sort_order,
        )
    )
    for variant in variants:
        variants_by_revision.setdefault(variant.child_revision_id, []).append(variant)

    primary_child_ids = {
        child.id
        for _publication, _kb, child, _revision in rows
        if child.is_primary
    }
    primary_publications = await session.scalars(
        select(ChildKnowledgeBasePublication).where(
            ChildKnowledgeBasePublication.child_id.in_(primary_child_ids),
            ChildKnowledgeBasePublication.status == ChildPublicationStatus.PUBLISHED,
            ChildKnowledgeBasePublication.active_revision_id.is_not(None),
        )
    )
    primary_by_parent_kb: set[tuple[UUID, UUID]] = set()
    primary_parent_by_child = {
        child.id: child.parent_id
        for _publication, _kb, child, _revision in rows
        if child.is_primary
    }
    for publication in primary_publications:
        parent_id = primary_parent_by_child.get(publication.child_id)
        if parent_id is not None:
            primary_by_parent_kb.add((parent_id, publication.knowledge_base_id))

    candidates: list[SearchCandidate] = []
    for publication, knowledge_base, child, child_revision in rows:
        parent_revision = parent_revisions.get(child.parent_id)
        if parent_revision is None:
            continue
        if (child.parent_id, publication.knowledge_base_id) not in primary_by_parent_kb:
            continue
        candidates.append(
            SearchCandidate(
                publication=publication,
                knowledge_base=knowledge_base,
                child=child,
                child_revision=child_revision,
                question_variants=variants_by_revision.get(child_revision.id, []),
                parent_revision=parent_revision,
                lexical_rules=rules_by_revision.get(parent_revision.id, []),
            )
        )
    return candidates


def _score_candidate(
    candidate: SearchCandidate,
    *,
    query: str | None,
    ocr_text: str | None,
) -> ScoredCandidate:
    channels = [value for value in (query, ocr_text) if value]
    channel_scores = [_channel_score(value, candidate) for value in channels]
    if not channel_scores:
        return ScoredCandidate(candidate, 0.0, "semantic")
    if query and ocr_text:
        score = channel_scores[0][0] * 0.65 + channel_scores[1][0] * 0.35
    else:
        score = channel_scores[0][0]
    score += _helpful_bonus(candidate.publication.helpful_count)
    reason = max(channel_scores, key=lambda item: item[0])[1]
    return ScoredCandidate(candidate, min(score, 1.0), reason)


async def _score_candidates_from_index(
    candidates: list[SearchCandidate],
    *,
    query: str | None,
    ocr_text: str | None,
    knowledge_base_id: UUID | None,
    limit: int,
    index_backend: SearchIndexBackend,
) -> list[ScoredCandidate] | None:
    queries: list[IndexQuery] = []
    if query:
        queries.append(
            IndexQuery(
                text=query,
                channel="text",
                weight=0.65 if ocr_text else 1.0,
            )
        )
    if ocr_text:
        queries.append(
            IndexQuery(
                text=ocr_text,
                channel="ocr",
                weight=0.35 if query else 1.0,
            )
        )
    if not queries:
        return []

    knowledge_base_ids = (
        {knowledge_base_id}
        if knowledge_base_id is not None
        else {candidate.knowledge_base.id for candidate in candidates}
    )
    hits = []
    index_available = False
    for target_knowledge_base_id in knowledge_base_ids:
        target_collection_name = next(
            (
                candidate.knowledge_base.current_physical_collection_name
                for candidate in candidates
                if candidate.knowledge_base.id == target_knowledge_base_id
            ),
            None,
        )
        try:
            target_hits = await index_backend.search(
                knowledge_base_id=target_knowledge_base_id,
                queries=queries,
                limit=max(limit * 12, 24),
                collection_name=target_collection_name,
            )
        except SearchIndexUnavailableError:
            continue
        index_available = True
        hits.extend(target_hits)
    if not index_available:
        return None

    best_by_candidate: dict[tuple[UUID, UUID], tuple[float, str]] = {}
    for hit in hits:
        key = (hit.knowledge_base_id, hit.child_revision_id)
        previous = best_by_candidate.get(key)
        if previous is None or hit.score > previous[0]:
            best_by_candidate[key] = (hit.score, hit.match_reason)

    scored: list[ScoredCandidate] = []
    for candidate in candidates:
        score_and_reason = best_by_candidate.get(
            (candidate.knowledge_base.id, candidate.child_revision.id)
        )
        if score_and_reason is None:
            score, reason = 0.0, "hybrid_dense_bm25"
        else:
            score, reason = score_and_reason
        score += _helpful_bonus(candidate.publication.helpful_count)
        scored.append(ScoredCandidate(candidate, min(score, 1.0), reason))
    return scored


async def search_published_content(
    session: AsyncSession,
    *,
    user_id: UUID,
    query: str | None,
    ocr_text: str | None,
    knowledge_base_id: UUID | None,
    retrieval_mode: Literal["vector", "field_filter"],
    limit: int,
    index_backend: SearchIndexBackend | None = None,
    parent_type: str | None = None,
    question_type: str | None = None,
    business_object: str | None = None,
    purpose: str | None = None,
    customer_type: str | None = None,
    ocr_recognition: OcrRecognition | None = None,
    ocr_keyword_fallback_min_confidence: float = 0.9,
) -> SearchDetails:
    if ocr_recognition is not None and ocr_recognition.text != ocr_text:
        raise ValueError("OCR recognition text must match ocr_text")
    if not 0 <= ocr_keyword_fallback_min_confidence <= 1:
        raise ValueError("ocr_keyword_fallback_min_confidence must be between 0 and 1")
    if knowledge_base_id is not None:
        knowledge_base = await session.get(KnowledgeBase, knowledge_base_id)
        if knowledge_base is None or not knowledge_base.is_active:
            raise SearchKnowledgeBaseUnavailableError(knowledge_base_id)
    mode = (
        SearchQueryMode.MIXED
        if query and ocr_text
        else SearchQueryMode.IMAGE
        if ocr_text
        else SearchQueryMode.TEXT
    )
    candidates = await _load_candidates(session, knowledge_base_id=knowledge_base_id)
    if retrieval_mode == "field_filter":
        selected = [
            ScoredCandidate(candidate, 1.0, "field_filter")
            for candidate in candidates
            if _matches_field_filters(
                candidate,
                parent_type=parent_type,
                question_type=question_type,
                business_object=business_object,
                purpose=purpose,
                customer_type=customer_type,
            )
        ]
        # Field filtering is a browse operation: return every matching published
        # entry, not just the top-N items selected by relevance scoring.
        selected.sort(
            key=lambda item: (
                item.candidate.parent_revision.name,
                item.candidate.parent_revision.canonical_keyword,
                item.candidate.child_revision.question,
                item.candidate.knowledge_base.name,
            )
        )
    else:
        scored: list[ScoredCandidate] | None = None
        if index_backend is not None:
            scored = await _score_candidates_from_index(
                candidates,
                query=query,
                ocr_text=ocr_text,
                knowledge_base_id=knowledge_base_id,
                limit=limit,
                index_backend=index_backend,
            )
        if scored is None:
            scored = [_score_candidate(item, query=query, ocr_text=ocr_text) for item in candidates]
        selected = [item for item in scored if item.score >= SEARCH_THRESHOLD]

        fallback_candidates: list[ScoredCandidate] = []
        fallback_parent_ids: set[UUID] = set()
        for item in scored:
            if not item.candidate.child.is_primary:
                continue
            matches: list[tuple[int, str]] = []
            if query:
                query_match = _parent_keyword_match(query, item.candidate)
                if query_match is not None:
                    matches.append((query_match[0], "parent_keyword_fallback"))
            if (
                ocr_recognition is not None
                and ocr_recognition.confidence >= ocr_keyword_fallback_min_confidence
            ):
                ocr_match = _ocr_controlled_keyword_match(ocr_recognition, item.candidate)
                if ocr_match is not None:
                    matches.append((ocr_match[0], "ocr_keyword_fallback"))
            if not matches or item.candidate.parent_revision.parent_id in fallback_parent_ids:
                continue
            if item not in selected:
                quality, match_reason = max(matches, key=lambda match: match[0])
                fallback_candidates.append(
                    ScoredCandidate(
                        item.candidate,
                        min(
                            0.18
                            + quality * 0.01
                            + _helpful_bonus(item.candidate.publication.helpful_count),
                            0.25,
                        ),
                        match_reason,
                    )
                )
                fallback_parent_ids.add(item.candidate.parent_revision.parent_id)
            if len(fallback_parent_ids) >= MAX_FALLBACK_PARENTS:
                break

        selected.extend(fallback_candidates)
        selected.sort(
            key=lambda item: (
                item.candidate.parent_revision.parent_id,
                -item.score,
                item.candidate.knowledge_base.name,
            )
        )
        # Keep semantic results ahead of fallback results while retaining all
        # knowledge-base variants of the selected parent groups.
        selected.sort(key=lambda item: item.score, reverse=True)
        selected = selected[:limit]

    event = SearchEvent(
        user_id=user_id,
        query_text=query,
        ocr_text=ocr_text,
        ocr_keywords=list(ocr_recognition.keywords) if ocr_recognition is not None else None,
        ocr_confidence=ocr_recognition.confidence if ocr_recognition is not None else None,
        ocr_model_version=(
            ocr_recognition.model_version if ocr_recognition is not None else None
        ),
        ocr_image_sha256=(
            ocr_recognition.image_sha256 if ocr_recognition is not None else None
        ),
        query_mode=mode,
        knowledge_base_id=knowledge_base_id,
        no_match=not selected,
    )
    session.add(event)
    await session.flush()

    grouped: dict[UUID, list[tuple[SearchResultItem, SearchCandidate]]] = {}
    result_parent_revisions: dict[UUID, ParentRevision] = {}
    for rank, item in enumerate(selected, start=1):
        result = SearchResultItem(
            search_event_id=event.id,
            rank=rank,
            score=item.score,
            child_id=item.candidate.child.id,
            knowledge_base_id=item.candidate.knowledge_base.id,
            child_revision_id=item.candidate.child_revision.id,
            parent_id=item.candidate.child.parent_id,
            parent_revision_id=item.candidate.parent_revision.id,
            match_reason=item.match_reason,
        )
        session.add(result)
        result_parent_revisions[item.candidate.parent_revision.parent_id] = (
            item.candidate.parent_revision
        )
        grouped.setdefault(item.candidate.parent_revision.parent_id, []).append(
            (result, item.candidate)
        )
    await session.flush()
    groups = [
        (result_parent_revisions[parent_id], items)
        for parent_id, items in grouped.items()
    ]
    groups.sort(key=lambda group: max(item.score for item, _candidate in group[1]), reverse=True)
    return SearchDetails(
        event=event,
        groups=groups,
        no_match_guidance=(
            (NO_FILTER_MATCH_GUIDANCE if retrieval_mode == "field_filter" else NO_MATCH_GUIDANCE)
            if not selected
            else None
        ),
    )


async def record_helpful_feedback(
    session: AsyncSession,
    *,
    user_id: UUID,
    search_event_id: UUID,
    result_item_id: UUID,
) -> tuple[HelpfulFeedbackEvent, int, bool]:
    row = (
        await session.execute(
            select(SearchResultItem, SearchEvent)
            .join(SearchEvent, SearchEvent.id == SearchResultItem.search_event_id)
            .where(
                SearchResultItem.id == result_item_id,
                SearchResultItem.search_event_id == search_event_id,
                SearchEvent.user_id == user_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise SearchResultNotFoundError(result_item_id)
    result_item, _event = row
    publication = await session.scalar(
        select(ChildKnowledgeBasePublication)
        .where(
            ChildKnowledgeBasePublication.child_id == result_item.child_id,
            ChildKnowledgeBasePublication.knowledge_base_id == result_item.knowledge_base_id,
        )
        .with_for_update()
    )
    if (
        publication is None
        or publication.status != ChildPublicationStatus.PUBLISHED
        or publication.active_revision_id != result_item.child_revision_id
    ):
        raise SearchResultStaleError(result_item_id)
    existing = await session.scalar(
        select(HelpfulFeedbackEvent).where(
            HelpfulFeedbackEvent.user_id == user_id,
            HelpfulFeedbackEvent.search_event_id == search_event_id,
            HelpfulFeedbackEvent.child_id == result_item.child_id,
            HelpfulFeedbackEvent.knowledge_base_id == result_item.knowledge_base_id,
            HelpfulFeedbackEvent.child_revision_id == result_item.child_revision_id,
        )
    )
    if existing is not None:
        return existing, publication.helpful_count, True
    feedback = HelpfulFeedbackEvent(
        user_id=user_id,
        search_event_id=search_event_id,
        search_result_item_id=result_item.id,
        child_id=result_item.child_id,
        knowledge_base_id=result_item.knowledge_base_id,
        child_revision_id=result_item.child_revision_id,
    )
    session.add(feedback)
    publication.helpful_count += 1
    await session.flush()
    return feedback, publication.helpful_count, False
