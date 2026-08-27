from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.knowledge_content import (
    ParentRevision,
    SearchResultItem,
)
from app.services.conversation import (
    NormalizedConversationMessage,
    validate_conversation,
)
from app.services.embedding import RerankerProvider
from app.services.llm import LlmProvider
from app.services.search import (
    SearchCandidate,
    SearchDetails,
    SearchPipelineOptions,
    search_published_content,
)


class FastSearchValidationError(ValueError):
    pass


NO_QUERY_GUIDANCE = "未发现待查询问题"


@dataclass(frozen=True)
class MergedSearchItem:
    result_item: SearchResultItem
    candidate: SearchCandidate
    matched_queries: list[str] = field(default_factory=list)
    best_score: float = 0.0


@dataclass(frozen=True)
class ConversationSearchDetails:
    queries: list[str]
    total_candidates: int
    no_query_guidance: str | None
    no_match: bool
    no_match_guidance: str | None
    groups: list[tuple[ParentRevision, list[MergedSearchItem]]]
    degraded: bool = False
    degradation_reasons: tuple[str, ...] = ()


async def conversation_assisted_search(
    session: AsyncSession,
    *,
    user_id: UUID,
    messages: list[NormalizedConversationMessage],
    knowledge_base_id: UUID | None,
    limit: int,
    settings: Settings,
    index_backend,
    provider: LlmProvider,
    reranker: RerankerProvider | None = None,
) -> ConversationSearchDetails:
    try:
        conversation = validate_conversation(
            messages,
            max_messages=settings.llm_max_conversation_messages,
            max_chars=settings.llm_max_conversation_chars,
            require_both_parties=False,
        )
    except ValueError as exc:
        raise FastSearchValidationError(str(exc)) from exc

    extraction = await provider.extract_search_queries(conversation.transcript)
    if not extraction.queries:
        return ConversationSearchDetails(
            queries=[],
            total_candidates=0,
            no_query_guidance=NO_QUERY_GUIDANCE,
            no_match=False,
            no_match_guidance=None,
            groups=[],
            degraded=False,
            degradation_reasons=(),
        )

    details_by_query: list[tuple[str, SearchDetails]] = []
    for query in extraction.queries:
        details = await search_published_content(
            session,
            user_id=user_id,
            query=query,
            ocr_text=None,
            knowledge_base_id=knowledge_base_id,
            retrieval_mode="vector",
            limit=limit,
            index_backend=index_backend,
            reranker=reranker,
            relevance_judge=(
                provider if hasattr(provider, "judge_search_relevance") else None
            ),
            pipeline_options=SearchPipelineOptions(
                high_confidence_threshold=settings.search_high_confidence_threshold,
                rerank_threshold=settings.search_rerank_threshold,
                fallback_threshold=settings.search_fallback_threshold,
                candidate_pool_size=settings.search_candidate_pool_size,
            ),
        )
        details_by_query.append((query, details))

    merged_by_key: dict[tuple[UUID, UUID], MergedSearchItem] = {}
    parent_revisions: dict[UUID, ParentRevision] = {}
    for query, details in details_by_query:
        for parent_revision, items in details.groups:
            parent_revisions.setdefault(parent_revision.parent_id, parent_revision)
            for result_item, candidate in items:
                key = (candidate.child_revision.id, candidate.knowledge_base.id)
                existing = merged_by_key.get(key)
                if existing is None:
                    merged_by_key[key] = MergedSearchItem(
                        result_item=result_item,
                        candidate=candidate,
                        matched_queries=[query],
                        best_score=result_item.score,
                    )
                    continue
                if query not in existing.matched_queries:
                    existing.matched_queries.append(query)
                if (
                    result_item.score,
                    result_item.helpful_count_at_search,
                    -result_item.rank,
                ) > (
                    existing.best_score,
                    existing.result_item.helpful_count_at_search,
                    -existing.result_item.rank,
                ):
                    merged_by_key[key] = MergedSearchItem(
                        result_item=result_item,
                        candidate=candidate,
                        matched_queries=existing.matched_queries,
                        best_score=result_item.score,
                    )

    merged_items = sorted(
        merged_by_key.values(),
        key=lambda item: (
            -item.best_score,
            -item.result_item.helpful_count_at_search,
            item.result_item.rank,
        ),
    )
    grouped: dict[UUID, list[MergedSearchItem]] = {}
    for item in merged_items:
        grouped.setdefault(item.candidate.child.parent_id, []).append(item)

    groups = [
        (parent_revisions[parent_id], items)
        for parent_id, items in sorted(
            grouped.items(),
            key=lambda pair: min(
                (
                    -item.best_score,
                    -item.result_item.helpful_count_at_search,
                    item.result_item.rank,
                )
                for item in pair[1]
            ),
        )
    ]

    return ConversationSearchDetails(
        queries=extraction.queries,
        total_candidates=extraction.total_candidates,
        no_query_guidance=None,
        no_match=not merged_items,
        no_match_guidance=(
            details_by_query[0][1].no_match_guidance if not merged_items else None
        ),
        groups=groups,
        degraded=any(details.degraded for _query, details in details_by_query),
        degradation_reasons=tuple(
            dict.fromkeys(
                reason
                for _query, details in details_by_query
                for reason in details.degradation_reasons
            )
        ),
    )
