from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass, field, replace
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
    EvidenceAttachment,
    HelpfulFeedbackEvent,
    ParentLexicalRule,
    ParentLexicalRuleType,
    ParentRevision,
    ReviewSubmission,
    ReviewSubmissionKind,
    ReviewSubmissionStatus,
    SearchEvent,
    SearchInteraction,
    SearchInteractionType,
    SearchQueryMode,
    SearchResultItem,
    SearchResultKind,
    WebLink,
)
from app.services.embedding import RerankerProvider
from app.services.llm import RelevanceCandidate, RelevanceJudge
from app.services.ocr import OcrRecognition
from app.services.retrieval import (
    IndexQuery,
    SearchIndexBackend,
    SearchIndexUnavailableError,
)
from app.services.supplemental_retrieval import (
    SupplementalAvailability,
    SupplementalDocument,
    SupplementalRetriever,
    SupplementalUnavailableError,
    SupplementalUpstreamError,
)

LOGGER = logging.getLogger(__name__)


class SearchKnowledgeBaseUnavailableError(Exception):
    pass


class SearchEventNotFoundError(Exception):
    pass


class SearchResultNotFoundError(Exception):
    pass


class SearchResultStaleError(Exception):
    pass


class SearchResultNotHelpfulError(Exception):
    pass


@dataclass(frozen=True)
class SearchCandidate:
    publication: ChildKnowledgeBasePublication
    knowledge_base: KnowledgeBase
    child: Child
    child_revision: ChildRevision
    question_variants: list[ChildRevisionQuestionVariant]
    attachments: list[EvidenceAttachment]
    web_links: list[WebLink]
    parent_revision: ParentRevision
    lexical_rules: list[ParentLexicalRule]


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: SearchCandidate
    hybrid_score: float
    match_reason: str
    matched_field: str | None = None
    source_rank: int = 0


@dataclass(frozen=True)
class SelectedCandidate:
    candidate: SearchCandidate
    score: float
    hybrid_score: float | None
    rerank_score: float | None
    selection_stage: str
    helpful_count_at_search: int
    match_reason: str
    matched_field: str | None = None
    source_rank: int = 0


@dataclass(frozen=True)
class SearchPipelineOptions:
    high_confidence_threshold: float = 0.7
    rerank_threshold: float = 0.5
    fallback_threshold: float = 0.22
    candidate_pool_size: int = 24

    def __post_init__(self) -> None:
        for value, name in (
            (self.high_confidence_threshold, "high_confidence_threshold"),
            (self.rerank_threshold, "rerank_threshold"),
            (self.fallback_threshold, "fallback_threshold"),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.fallback_threshold > self.high_confidence_threshold:
            raise ValueError("fallback_threshold must not exceed high_confidence_threshold")
        if self.candidate_pool_size <= 0:
            raise ValueError("candidate_pool_size must be positive")


@dataclass(frozen=True)
class IndexedScoring:
    scored: list[ScoredCandidate] | None
    index_degraded: bool = False


@dataclass(frozen=True)
class SelectionDecision:
    selected: list[SelectedCandidate]
    degradation_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchDetails:
    event: SearchEvent
    interaction: SearchInteraction | None
    groups: list[tuple[ParentRevision, list[tuple[SearchResultItem, SearchCandidate]]]]
    no_match_guidance: str | None
    degraded: bool = False
    degradation_reasons: tuple[str, ...] = ()
    supplemental_results: list[SupplementalSearchResult] = field(default_factory=list)


@dataclass(frozen=True)
class SupplementalSearchResult:
    """A persisted, immutable supplemental material card for one search event."""

    result_item: SearchResultItem
    title: str
    content: str


@dataclass(frozen=True)
class SelectedSupplementalDocument:
    document: SupplementalDocument
    score: float
    rerank_score: float | None
    selection_stage: str


NO_MATCH_GUIDANCE = "未找到足够相关的知识，请转研发查询。"
NO_FILTER_MATCH_GUIDANCE = "未找到符合字段条件的知识。"
MAX_FALLBACK_PARENTS = 3
HELPFUL_SCORE_CAP = 0.12


async def _select_supplemental_documents(
    documents: list[SupplementalDocument],
    *,
    query: str | None,
    ocr_text: str | None,
    reranker: RerankerProvider | None,
) -> list[SelectedSupplementalDocument]:
    """Use the platform reranker when possible, else stable channel fusion order.

    The independent source controls retrieval only. Reusing the platform
    reranker here keeps ranking semantics in one place and avoids exposing an
    upstream reranker to the browser.
    """

    ordered = sorted(
        documents,
        key=lambda item: (-item.source_score, item.upstream_rank, item.source_hash),
    )
    if not ordered:
        return []
    ranking_query = "\n".join(value for value in (query, ocr_text) if value and value.strip())
    if reranker is not None and ranking_query:
        try:
            scores = await reranker.rerank(ranking_query, [item.content for item in ordered])
            if len(scores) != len(ordered) or not all(math.isfinite(score) for score in scores):
                raise ValueError("reranker returned invalid supplemental scores")
            selected = [
                SelectedSupplementalDocument(
                    document=document,
                    score=score,
                    rerank_score=score,
                    selection_stage="supplemental_rerank",
                )
                for document, score in zip(ordered, scores, strict=True)
            ]
            return sorted(
                selected,
                key=lambda item: (
                    -item.score,
                    -item.document.source_score,
                    item.document.upstream_rank,
                    item.document.source_hash,
                ),
            )
        except Exception:
            # Supplemental material is optional. Its reranker failure is an
            # internal fallback, not a user-visible degradation of core search.
            LOGGER.warning("supplemental reranking failed; using source fusion", exc_info=True)
    return [
        SelectedSupplementalDocument(
            document=document,
            score=document.source_score,
            rerank_score=None,
            selection_stage="supplemental_source_fusion",
        )
        for document in ordered
    ]


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


def _matched_field_for_query(query: str, candidate: SearchCandidate) -> str | None:
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return None
    question = _normalize_text(candidate.child_revision.question)
    variants = [_normalize_text(item.question_text) for item in candidate.question_variants]
    response = _normalize_text(candidate.child_revision.response_content)
    if normalized_query == question or normalized_query in question:
        return "question"
    if any(normalized_query == variant or normalized_query in variant for variant in variants):
        return "question_variant"
    if normalized_query in response:
        return "response_content"

    query_tokens = _tokens(normalized_query)
    if not query_tokens:
        return None
    field_scores = [
        (len(query_tokens & _tokens(question)) / len(query_tokens), "question"),
        (
            max(
                (len(query_tokens & _tokens(variant)) / len(query_tokens) for variant in variants),
                default=0.0,
            ),
            "question_variant",
        ),
        (len(query_tokens & _tokens(response)) / len(query_tokens), "response_content"),
    ]
    score, field = max(field_scores, key=lambda item: item[0])
    return field if score > 0 else None


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
            return (
                re.search(
                    rf"(?<![a-z0-9_]){re.escape(normalized_term)}(?![a-z0-9_])",
                    normalized_text,
                )
                is not None
            )
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

    attachments_by_revision: dict[UUID, list[EvidenceAttachment]] = {}
    attachments = await session.scalars(
        select(EvidenceAttachment)
        .where(EvidenceAttachment.child_revision_id.in_(child_revision_ids))
        .order_by(EvidenceAttachment.child_revision_id, EvidenceAttachment.sort_order)
    )
    for attachment in attachments:
        if attachment.child_revision_id is not None:
            attachments_by_revision.setdefault(attachment.child_revision_id, []).append(attachment)

    web_links_by_revision: dict[UUID, list[WebLink]] = {}
    web_links = await session.scalars(
        select(WebLink)
        .where(WebLink.child_revision_id.in_(child_revision_ids))
        .order_by(WebLink.child_revision_id, WebLink.sort_order)
    )
    for web_link in web_links:
        web_links_by_revision.setdefault(web_link.child_revision_id, []).append(web_link)

    primary_child_ids = {
        child.id for _publication, _kb, child, _revision in rows if child.is_primary
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
                attachments=attachments_by_revision.get(child_revision.id, []),
                web_links=web_links_by_revision.get(child_revision.id, []),
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
    channel_scores = [(value, _channel_score(value, candidate)) for value in channels]
    if not channel_scores:
        return ScoredCandidate(candidate, 0.0, "semantic")
    if query and ocr_text:
        score = channel_scores[0][1][0] * 0.65 + channel_scores[1][1][0] * 0.35
    else:
        score = channel_scores[0][1][0]
    best_channel, (_best_score, reason) = max(channel_scores, key=lambda item: item[1][0])
    return ScoredCandidate(
        candidate,
        min(score, 1.0),
        reason,
        matched_field=_matched_field_for_query(best_channel, candidate),
    )


def _build_index_queries(query: str | None, ocr_text: str | None) -> list[IndexQuery]:
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
    return queries


async def _score_candidates_from_index(
    candidates: list[SearchCandidate],
    *,
    query: str | None,
    ocr_text: str | None,
    knowledge_base_id: UUID | None,
    limit: int,
    candidate_pool_size: int,
    index_backend: SearchIndexBackend,
) -> IndexedScoring:
    queries = _build_index_queries(query, ocr_text)
    if not queries:
        return IndexedScoring(scored=[])

    knowledge_base_ids = (
        {knowledge_base_id}
        if knowledge_base_id is not None
        else {candidate.knowledge_base.id for candidate in candidates}
    )
    if not candidates:
        return IndexedScoring(scored=[])
    hits = []
    index_available = False
    unavailable_knowledge_base_ids: set[UUID] = set()
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
                limit=max(limit * 12, candidate_pool_size),
                collection_name=target_collection_name,
            )
        except SearchIndexUnavailableError:
            unavailable_knowledge_base_ids.add(target_knowledge_base_id)
            continue
        except Exception:
            LOGGER.warning(
                "index backend failed; using deterministic retrieval fallback",
                exc_info=True,
            )
            unavailable_knowledge_base_ids.add(target_knowledge_base_id)
            continue
        index_available = True
        hits.extend(target_hits)
    if not index_available:
        return IndexedScoring(scored=None, index_degraded=True)

    best_by_candidate: dict[tuple[UUID, UUID], tuple[float, str, str]] = {}
    for hit in hits:
        key = (hit.knowledge_base_id, hit.child_revision_id)
        previous = best_by_candidate.get(key)
        if previous is None or hit.score > previous[0]:
            best_by_candidate[key] = (hit.score, hit.match_reason, hit.field_type)

    scored: list[ScoredCandidate] = []
    for candidate in candidates:
        if candidate.knowledge_base.id in unavailable_knowledge_base_ids:
            scored.append(
                _score_candidate(candidate, query=query, ocr_text=ocr_text)
            )
            continue
        score_and_reason = best_by_candidate.get(
            (candidate.knowledge_base.id, candidate.child_revision.id)
        )
        if score_and_reason is None:
            score, reason, matched_field = 0.0, "hybrid_dense_bm25", None
        else:
            score, reason, matched_field = score_and_reason
        scored.append(
            ScoredCandidate(
                candidate,
                min(score, 1.0),
                reason,
                matched_field=matched_field,
            )
        )
    return IndexedScoring(
        scored=scored,
        index_degraded=bool(unavailable_knowledge_base_ids),
    )


def _rank_scored_candidates(scored: list[ScoredCandidate]) -> list[ScoredCandidate]:
    """Give every raw candidate a deterministic retrieval rank before selection."""

    ordered = sorted(
        scored,
        # ``sorted`` is stable, so equal raw scores retain the adapter's
        # original candidate order for the final stable-order tie break.
        key=lambda item: -item.hybrid_score,
    )
    return [replace(item, source_rank=rank) for rank, item in enumerate(ordered, start=1)]


def _candidate_document(candidate: SearchCandidate) -> str:
    variants = "\n".join(item.question_text for item in candidate.question_variants)
    sections = [f"问题：{candidate.child_revision.question}"]
    if variants:
        sections.append(f"同义问句：{variants}")
    sections.append(f"回复内容：{candidate.child_revision.response_content}")
    return "\n".join(sections)


def _combined_judgement_query(queries: list[IndexQuery]) -> str:
    if len(queries) == 1:
        return queries[0].text
    return "\n".join(f"{query.channel} 查询：{query.text}" for query in queries)


def _make_selected(
    item: ScoredCandidate,
    *,
    base_score: float,
    selection_stage: str,
    rerank_score: float | None = None,
) -> SelectedCandidate:
    helpful_count = item.candidate.publication.helpful_count
    return SelectedCandidate(
        candidate=item.candidate,
        score=min(base_score + _helpful_bonus(helpful_count), 1.0),
        hybrid_score=item.hybrid_score,
        rerank_score=rerank_score,
        selection_stage=selection_stage,
        helpful_count_at_search=helpful_count,
        match_reason=item.match_reason,
        matched_field=item.matched_field,
        source_rank=item.source_rank,
    )


def _sort_selected_candidates(items: list[SelectedCandidate]) -> list[SelectedCandidate]:
    return sorted(
        items,
        key=lambda item: (
            -item.score,
            -item.helpful_count_at_search,
            item.source_rank,
            str(item.candidate.knowledge_base.id),
            str(item.candidate.child_revision.id),
        ),
    )


class StagedRetrievalPipeline:
    """Select trustworthy candidates without exposing provider-specific control flow."""

    def __init__(
        self,
        *,
        reranker: RerankerProvider | None,
        relevance_judge: RelevanceJudge | None,
        options: SearchPipelineOptions,
    ) -> None:
        self._reranker = reranker
        self._relevance_judge = relevance_judge
        self._options = options

    async def select(
        self,
        scored: list[ScoredCandidate],
        *,
        queries: list[IndexQuery],
        index_degraded: bool,
    ) -> SelectionDecision:
        ranked = _rank_scored_candidates(scored)
        base_degradation_reasons = ["index_unavailable"] if index_degraded else []
        has_high_confidence = any(
            item.hybrid_score >= self._options.high_confidence_threshold
            for item in ranked
        )
        if has_high_confidence:
            # A high-confidence hit makes the whole first-stage retrieval set
            # trustworthy enough to skip the expensive model stages. Zero-score
            # published rows are not retrieval hits and remain excluded.
            retrieval_hits = [item for item in ranked if item.hybrid_score > 0]
            return SelectionDecision(
                selected=[
                    _make_selected(
                        item,
                        base_score=item.hybrid_score,
                        selection_stage="hybrid",
                    )
                    for item in retrieval_hits
                ],
                degradation_reasons=tuple(base_degradation_reasons),
            )

        # Only actual hybrid hits enter expensive model stages.  Zero-score
        # published rows remain available to the independent keyword fallback,
        # but are not retrieval candidates.
        pool = [item for item in ranked if item.hybrid_score > 0][
            : self._options.candidate_pool_size
        ]
        if not pool:
            return SelectionDecision(
                selected=[],
                degradation_reasons=tuple(base_degradation_reasons),
            )

        rerank_scores: dict[tuple[UUID, UUID], float] = {}
        reranker_failed = False
        if self._reranker is not None:
            try:
                documents = [_candidate_document(item.candidate) for item in pool]
                combined_scores = [0.0] * len(pool)
                for query in queries:
                    scores = await self._reranker.rerank(query.text, documents)
                    if len(scores) != len(pool):
                        raise ValueError("reranker returned an unexpected item count")
                    for position, score in enumerate(scores):
                        numeric_score = float(score)
                        if not math.isfinite(numeric_score):
                            raise ValueError("reranker returned a non-finite score")
                        combined_scores[position] += query.weight * max(
                            0.0,
                            min(1.0, numeric_score),
                        )
                rerank_scores = {
                    (item.candidate.knowledge_base.id, item.candidate.child_revision.id): score
                    for item, score in zip(pool, combined_scores, strict=True)
                }
                reranked = [
                    (
                        item,
                        rerank_scores[
                            (
                                item.candidate.knowledge_base.id,
                                item.candidate.child_revision.id,
                            )
                        ],
                    )
                    for item in pool
                ]
                accepted = [
                    _make_selected(
                        item,
                        base_score=rerank_score,
                        rerank_score=rerank_score,
                        selection_stage="rerank",
                    )
                    for item, rerank_score in reranked
                    if rerank_score >= self._options.rerank_threshold
                ]
                if accepted:
                    return SelectionDecision(
                        selected=accepted,
                        degradation_reasons=tuple(base_degradation_reasons),
                    )
            except Exception:
                LOGGER.warning(
                    "optional reranker failed; continuing to the next stage",
                    exc_info=True,
                )
                reranker_failed = True
                rerank_scores = {}

        llm_failed = False
        if self._relevance_judge is not None:
            try:
                relevance_candidates = [
                    RelevanceCandidate(
                        candidate_id=(
                            f"{item.candidate.knowledge_base.id}:"
                            f"{item.candidate.child_revision.id}"
                        ),
                        document=_candidate_document(item.candidate),
                    )
                    for item in pool
                ]
                judgements = await self._relevance_judge.judge_search_relevance(
                    _combined_judgement_query(queries),
                    relevance_candidates,
                )
                expected_ids = [candidate.candidate_id for candidate in relevance_candidates]
                actual_ids = [judgement.candidate_id for judgement in judgements]
                if len(actual_ids) != len(expected_ids):
                    raise ValueError("relevance judge did not cover every candidate")
                if len(set(actual_ids)) != len(actual_ids):
                    raise ValueError("relevance judge returned duplicate candidate IDs")
                if set(actual_ids) != set(expected_ids):
                    raise ValueError("relevance judge returned unknown candidate IDs")
                relevant_ids = {
                    judgement.candidate_id
                    for judgement in judgements
                    if judgement.relevant
                }
                accepted = []
                for item, relevance_candidate in zip(pool, relevance_candidates, strict=True):
                    if relevance_candidate.candidate_id not in relevant_ids:
                        continue
                    candidate_key = (
                        item.candidate.knowledge_base.id,
                        item.candidate.child_revision.id,
                    )
                    rerank_score = rerank_scores.get(candidate_key)
                    accepted.append(
                        _make_selected(
                            item,
                            base_score=(
                                rerank_score if rerank_score is not None else item.hybrid_score
                            ),
                            rerank_score=rerank_score,
                            selection_stage="llm",
                        )
                    )
                return SelectionDecision(
                    selected=accepted,
                    degradation_reasons=tuple(base_degradation_reasons),
                )
            except Exception:
                LOGGER.warning(
                    "optional relevance judge failed; using score fallback",
                    exc_info=True,
                )
                llm_failed = True

        fallback_selected = [
            _make_selected(
                item,
                base_score=item.hybrid_score,
                rerank_score=rerank_scores.get(
                    (item.candidate.knowledge_base.id, item.candidate.child_revision.id)
                ),
                selection_stage="score_fallback",
            )
            for item in pool
            if item.hybrid_score >= self._options.fallback_threshold
        ]
        fallback_reasons = list(base_degradation_reasons)
        # An unavailable optional model is a visible degradation only when its
        # absence actually caused the 0.22 basic-score path to provide results.
        # A separately injected keyword fallback must remain independent.
        if fallback_selected:
            if self._reranker is None:
                fallback_reasons.append("reranker_unconfigured")
            elif reranker_failed:
                fallback_reasons.append("reranker_failed")
            if self._relevance_judge is None:
                fallback_reasons.append("llm_unconfigured")
            elif llm_failed:
                fallback_reasons.append("llm_failed")
        return SelectionDecision(
            selected=fallback_selected,
            degradation_reasons=tuple(dict.fromkeys(fallback_reasons)),
        )


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
    reranker: RerankerProvider | None = None,
    relevance_judge: RelevanceJudge | None = None,
    pipeline_options: SearchPipelineOptions | None = None,
    search_interaction: SearchInteraction | None = None,
    query_order: int | None = None,
    supplemental_retriever: SupplementalRetriever | None = None,
) -> SearchDetails:
    if ocr_recognition is not None and ocr_recognition.text != ocr_text:
        raise ValueError("OCR recognition text must match ocr_text")
    if not 0 <= ocr_keyword_fallback_min_confidence <= 1:
        raise ValueError("ocr_keyword_fallback_min_confidence must be between 0 and 1")
    if retrieval_mode == "field_filter" and search_interaction is not None:
        raise ValueError("field filter searches cannot belong to a search interaction")
    if search_interaction is None and query_order is not None:
        raise ValueError("query_order requires a search interaction")
    if search_interaction is not None:
        if search_interaction.user_id != user_id:
            raise ValueError("search interaction owner must match the search user")
        if search_interaction.interaction_type != SearchInteractionType.QUICK_SEARCH:
            raise ValueError("only quick-search interactions can contain multiple query events")
        if query_order is None or query_order < 1:
            raise ValueError("quick-search query_order must be positive")
        if search_interaction.knowledge_base_id != knowledge_base_id:
            raise ValueError("quick-search events must keep the interaction knowledge-base scope")
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
    # Snapshot the optional service before creating a task. In particular, an
    # unavailable/disabled/stale service must not receive a query or make this
    # search wait; once dispatched it runs alongside platform retrieval.
    supplemental_task: asyncio.Task[list[SupplementalDocument]] | None = None
    supplemental_retrieval_status = "not_applicable"
    if retrieval_mode == "vector" and supplemental_retriever is not None:
        snapshot = supplemental_retriever.availability_snapshot()
        if snapshot.is_available:
            supplemental_task = asyncio.create_task(
                supplemental_retriever.retrieve(query=query, ocr_text=ocr_text),
                name="lightrag-supplemental-retrieval",
            )
        elif snapshot.state is SupplementalAvailability.DISABLED:
            supplemental_retrieval_status = "skipped_disabled"
        else:
            supplemental_retrieval_status = "skipped_unavailable"
    elif retrieval_mode == "vector":
        supplemental_retrieval_status = "skipped_disabled"
    candidates = await _load_candidates(session, knowledge_base_id=knowledge_base_id)
    options = pipeline_options or SearchPipelineOptions()
    degradation_reasons: tuple[str, ...] = ()
    if retrieval_mode == "field_filter":
        selected = [
            SelectedCandidate(
                candidate=candidate,
                score=1.0,
                hybrid_score=None,
                rerank_score=None,
                selection_stage="field_filter",
                helpful_count_at_search=candidate.publication.helpful_count,
                match_reason="field_filter",
                source_rank=rank,
            )
            for rank, candidate in enumerate(candidates, start=1)
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
        index_degraded = False
        if index_backend is not None:
            indexed = await _score_candidates_from_index(
                candidates,
                query=query,
                ocr_text=ocr_text,
                knowledge_base_id=knowledge_base_id,
                limit=limit,
                candidate_pool_size=options.candidate_pool_size,
                index_backend=index_backend,
            )
            scored = indexed.scored
            index_degraded = indexed.index_degraded
        if scored is None:
            scored = [_score_candidate(item, query=query, ocr_text=ocr_text) for item in candidates]
            index_degraded = index_backend is not None

        decision = await StagedRetrievalPipeline(
            reranker=reranker,
            relevance_judge=relevance_judge,
            options=options,
        ).select(
            scored,
            queries=_build_index_queries(query, ocr_text),
            index_degraded=index_degraded,
        )
        selected = list(decision.selected)
        degradation_reasons = decision.degradation_reasons

        fallback_candidates: list[SelectedCandidate] = []
        fallback_parent_ids: set[UUID] = set()
        selected_keys = {
            (item.candidate.knowledge_base.id, item.candidate.child_revision.id)
            for item in selected
        }
        for item in _rank_scored_candidates(scored):
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
            key = (item.candidate.knowledge_base.id, item.candidate.child_revision.id)
            if key not in selected_keys:
                quality, match_reason = max(matches, key=lambda match: match[0])
                fallback_candidates.append(
                    _make_selected(
                        replace(
                            item,
                            match_reason=match_reason,
                            matched_field="parent.canonical_keyword",
                        ),
                        base_score=min(0.18 + quality * 0.01, 0.25),
                        selection_stage="keyword_fallback",
                    )
                )
                fallback_parent_ids.add(item.candidate.parent_revision.parent_id)
            if len(fallback_parent_ids) >= MAX_FALLBACK_PARENTS:
                break

        selected.extend(fallback_candidates)
        selected = _sort_selected_candidates(selected)[:limit]

    supplemental_documents: list[SupplementalDocument] = []
    if supplemental_task is not None:
        try:
            supplemental_documents = await supplemental_task
            supplemental_retrieval_status = "success"
        except (SupplementalUpstreamError, SupplementalUnavailableError):
            # The retriever already closes its availability gate immediately.
            # Never turn this optional failure into degraded core search output.
            LOGGER.info("supplemental retrieval unavailable after dispatch")
            supplemental_retrieval_status = "failed_after_dispatch"
    selected_supplemental = await _select_supplemental_documents(
        supplemental_documents,
        query=query,
        ocr_text=ocr_text,
        reranker=reranker,
    )
    has_any_result = bool(selected or selected_supplemental)

    interaction = search_interaction
    effective_query_order: int | None = None
    if retrieval_mode == "vector":
        if interaction is None:
            interaction = SearchInteraction(
                user_id=user_id,
                interaction_type=SearchInteractionType.VECTOR,
                knowledge_base_id=knowledge_base_id,
                no_match=not has_any_result,
                degraded=bool(degradation_reasons),
                degradation_reasons=list(degradation_reasons) or None,
            )
            session.add(interaction)
            await session.flush()
            effective_query_order = 1
        else:
            effective_query_order = query_order

    event = SearchEvent(
        user_id=user_id,
        query_text=query,
        ocr_text=ocr_text,
        ocr_keywords=list(ocr_recognition.keywords) if ocr_recognition is not None else None,
        ocr_confidence=ocr_recognition.confidence if ocr_recognition is not None else None,
        ocr_model_version=(ocr_recognition.model_version if ocr_recognition is not None else None),
        ocr_image_sha256=(ocr_recognition.image_sha256 if ocr_recognition is not None else None),
        query_mode=mode,
        search_interaction_id=interaction.id if interaction is not None else None,
        query_order=effective_query_order,
        knowledge_base_id=knowledge_base_id,
        no_match=not has_any_result,
        degraded=bool(degradation_reasons),
        degradation_reasons=list(degradation_reasons) or None,
        supplemental_retrieval_status=supplemental_retrieval_status,
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
            matched_field=item.matched_field,
            hybrid_score=item.hybrid_score,
            rerank_score=item.rerank_score,
            selection_stage=item.selection_stage,
            helpful_count_at_search=item.helpful_count_at_search,
        )
        session.add(result)
        result_parent_revisions[item.candidate.parent_revision.parent_id] = (
            item.candidate.parent_revision
        )
        grouped.setdefault(item.candidate.parent_revision.parent_id, []).append(
            (result, item.candidate)
        )
    supplemental_results: list[SupplementalSearchResult] = []
    for rank, item in enumerate(selected_supplemental, start=len(selected) + 1):
        result = SearchResultItem(
            search_event_id=event.id,
            rank=rank,
            score=item.score,
            hybrid_score=item.document.source_score,
            rerank_score=item.rerank_score,
            selection_stage=item.selection_stage,
            helpful_count_at_search=0,
            result_kind=SearchResultKind.SUPPLEMENT,
            supplement_source_hash=item.document.source_hash,
            supplement_title=item.document.title,
            supplement_content=item.document.content,
            supplement_citation_metadata=item.document.citation_metadata,
            match_reason="supplemental_global",
            matched_field=None,
        )
        session.add(result)
        supplemental_results.append(
            SupplementalSearchResult(
                result_item=result,
                title=item.document.title,
                content=item.document.content,
            )
        )
    await session.flush()
    groups = [(result_parent_revisions[parent_id], items) for parent_id, items in grouped.items()]
    groups.sort(
        key=lambda group: min(
            (-item.score, -item.helpful_count_at_search, item.rank)
            for item, _candidate in group[1]
        )
    )
    return SearchDetails(
        event=event,
        interaction=interaction,
        groups=groups,
        no_match_guidance=(
            (NO_FILTER_MATCH_GUIDANCE if retrieval_mode == "field_filter" else NO_MATCH_GUIDANCE)
            if not has_any_result
            else None
        ),
        supplemental_results=supplemental_results,
        degraded=bool(degradation_reasons),
        degradation_reasons=degradation_reasons,
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
    if result_item.result_kind is not SearchResultKind.KNOWLEDGE:
        raise SearchResultNotHelpfulError(result_item_id)
    if (
        result_item.child_id is None
        or result_item.knowledge_base_id is None
        or result_item.child_revision_id is None
    ):
        raise SearchResultNotHelpfulError(result_item_id)
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
