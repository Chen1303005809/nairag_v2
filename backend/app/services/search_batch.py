from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.knowledge_content import (
    ParentRevision,
    SearchInteraction,
    SearchInteractionType,
    SearchResultItem,
    SearchResultKind,
)
from app.services.embedding import RerankerProvider
from app.services.llm import RelevanceJudge
from app.services.search import (
    SearchCandidate,
    SearchDetails,
    SearchPipelineOptions,
    search_published_content,
)
from app.services.supplemental_retrieval import SupplementalRetriever


@dataclass(frozen=True)
class MergedSearchItem:
    """One visible result after deduplicating the query-level search events."""

    result_item: SearchResultItem
    candidate: SearchCandidate
    matched_queries: list[str] = field(default_factory=list)
    best_score: float = 0.0


@dataclass(frozen=True)
class MergedSupplementalSearchItem:
    """One global material card after deduplicating query-level snapshots."""

    result_item: SearchResultItem
    title: str
    content: str
    matched_queries: list[str] = field(default_factory=list)
    best_score: float = 0.0


@dataclass(frozen=True)
class QueryBatchSearchDetails:
    """The unified result for one user-visible multi-query quick search."""

    interaction: SearchInteraction | None
    queries: list[str]
    total_candidates: int
    no_query_guidance: str | None
    no_match: bool
    no_match_guidance: str | None
    groups: list[tuple[ParentRevision, list[MergedSearchItem]]]
    degraded: bool = False
    degradation_reasons: tuple[str, ...] = ()
    supplemental_results: list[MergedSupplementalSearchItem] = field(default_factory=list)


@dataclass(frozen=True)
class PersistedQueryResult:
    """A result reconstructed from persisted query events for an admin detail view."""

    search_event_id: UUID
    query_order: int
    query_label: str
    result_item: SearchResultItem
    parent_name: str | None
    question: str
    knowledge_base_name: str | None
    content: str = ""
    source_hash: str | None = None
    citation_metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class MergedPersistedQueryResult:
    result: PersistedQueryResult
    matched_queries: list[str] = field(default_factory=list)


def _result_is_better(left: SearchResultItem, right: SearchResultItem) -> bool:
    return (
        left.score,
        left.helpful_count_at_search,
        -left.rank,
    ) > (
        right.score,
        right.helpful_count_at_search,
        -right.rank,
    )


def _result_sort_key(result: SearchResultItem) -> tuple[float, int, int]:
    return (-result.score, -result.helpful_count_at_search, result.rank)


def merge_persisted_query_results(
    results: list[PersistedQueryResult],
) -> list[MergedPersistedQueryResult]:
    """Rebuild the same visible deduplicated results from stored query events."""

    merged_by_key: dict[tuple[str, str], MergedPersistedQueryResult] = {}
    # Result UUIDs are unrelated to the order in which a quick-search batch
    # executed its queries. Rebuild the visible matched-query labels in the
    # persisted query order, rather than relying on a database row order.
    for result in sorted(
        results,
        key=lambda item: (
            item.query_order,
            item.result_item.rank,
            str(item.result_item.id),
        ),
    ):
        if result.result_item.result_kind is SearchResultKind.SUPPLEMENT:
            key = (
                "supplement",
                result.result_item.supplement_source_hash or str(result.result_item.id),
            )
        else:
            key = (
                str(result.result_item.child_revision_id),
                str(result.result_item.knowledge_base_id),
            )
        existing = merged_by_key.get(key)
        if existing is None:
            merged_by_key[key] = MergedPersistedQueryResult(
                result=result,
                matched_queries=[result.query_label],
            )
            continue
        if result.query_label not in existing.matched_queries:
            existing.matched_queries.append(result.query_label)
        if _result_is_better(result.result_item, existing.result.result_item):
            merged_by_key[key] = MergedPersistedQueryResult(
                result=result,
                matched_queries=existing.matched_queries,
            )
    return sorted(
        merged_by_key.values(),
        key=lambda item: _result_sort_key(item.result.result_item),
    )


def normalize_batch_queries(queries: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in queries:
        query = " ".join(value.split())
        if not query:
            raise ValueError("查询语句不能为空")
        if len(query) > 4_000:
            raise ValueError("查询长度不能超过 4000 字符")
        normalized.append(query)
    if not normalized:
        raise ValueError("至少提供一条查询")
    if len(normalized) > 5:
        raise ValueError("一次最多执行 5 条查询")
    return normalized


def merge_query_search_details(
    details_by_query: list[tuple[str, SearchDetails]],
    *,
    interaction: SearchInteraction | None,
    total_candidates: int,
) -> QueryBatchSearchDetails:
    """Apply the same de-duplication and ranking rules for every batch caller."""

    merged_by_key: dict[tuple[UUID, UUID], MergedSearchItem] = {}
    merged_supplemental_by_hash: dict[str, MergedSupplementalSearchItem] = {}
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
                if _result_is_better(result_item, existing.result_item):
                    merged_by_key[key] = MergedSearchItem(
                        result_item=result_item,
                        candidate=candidate,
                        matched_queries=existing.matched_queries,
                        best_score=result_item.score,
                    )
        for supplemental in details.supplemental_results:
            result_item = supplemental.result_item
            source_hash = result_item.supplement_source_hash or str(result_item.id)
            existing_supplemental = merged_supplemental_by_hash.get(source_hash)
            if existing_supplemental is None:
                merged_supplemental_by_hash[source_hash] = MergedSupplementalSearchItem(
                    result_item=result_item,
                    title=supplemental.title,
                    content=supplemental.content,
                    matched_queries=[query],
                    best_score=result_item.score,
                )
                continue
            if query not in existing_supplemental.matched_queries:
                existing_supplemental.matched_queries.append(query)
            if _result_is_better(result_item, existing_supplemental.result_item):
                merged_supplemental_by_hash[source_hash] = MergedSupplementalSearchItem(
                    result_item=result_item,
                    title=supplemental.title,
                    content=supplemental.content,
                    matched_queries=existing_supplemental.matched_queries,
                    best_score=result_item.score,
                )

    merged_items = sorted(
        merged_by_key.values(),
        key=lambda item: _result_sort_key(item.result_item),
    )
    grouped: dict[UUID, list[MergedSearchItem]] = {}
    for item in merged_items:
        grouped.setdefault(item.candidate.child.parent_id, []).append(item)

    groups = [
        (parent_revisions[parent_id], items)
        for parent_id, items in sorted(
            grouped.items(),
            key=lambda pair: min(
                _result_sort_key(item.result_item)
                for item in pair[1]
            ),
        )
    ]
    degradation_reasons = tuple(
        dict.fromkeys(
            reason
            for _query, details in details_by_query
            for reason in details.degradation_reasons
        )
    )
    supplemental_results = sorted(
        merged_supplemental_by_hash.values(),
        key=lambda item: _result_sort_key(item.result_item),
    )
    no_match = not merged_items and not supplemental_results
    return QueryBatchSearchDetails(
        interaction=interaction,
        queries=[query for query, _details in details_by_query],
        total_candidates=total_candidates,
        no_query_guidance=None,
        no_match=no_match,
        no_match_guidance=(
            details_by_query[0][1].no_match_guidance if no_match and details_by_query else None
        ),
        groups=groups,
        supplemental_results=supplemental_results,
        degraded=bool(degradation_reasons),
        degradation_reasons=degradation_reasons,
    )


async def execute_query_batch(
    session: AsyncSession,
    *,
    user_id: UUID,
    queries: list[str],
    knowledge_base_id: UUID | None,
    limit: int,
    settings: Settings,
    index_backend,
    reranker: RerankerProvider | None = None,
    relevance_judge: RelevanceJudge | None = None,
    total_candidates: int | None = None,
    supplemental_retriever: SupplementalRetriever | None = None,
) -> QueryBatchSearchDetails:
    """Persist and merge a complete quick-search interaction in query order."""

    normalized_queries = normalize_batch_queries(queries)
    interaction = SearchInteraction(
        user_id=user_id,
        interaction_type=SearchInteractionType.QUICK_SEARCH,
        knowledge_base_id=knowledge_base_id,
        no_match=False,
        degraded=False,
    )
    session.add(interaction)
    await session.flush()

    details_by_query: list[tuple[str, SearchDetails]] = []
    for query_order, query in enumerate(normalized_queries, start=1):
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
            relevance_judge=relevance_judge,
            pipeline_options=SearchPipelineOptions(
                high_confidence_threshold=settings.search_high_confidence_threshold,
                rerank_threshold=settings.search_rerank_threshold,
                fallback_threshold=settings.search_fallback_threshold,
                candidate_pool_size=settings.search_candidate_pool_size,
            ),
            search_interaction=interaction,
            query_order=query_order,
            supplemental_retriever=supplemental_retriever,
        )
        details_by_query.append((query, details))

    merged = merge_query_search_details(
        details_by_query,
        interaction=interaction,
        total_candidates=(
            total_candidates if total_candidates is not None else len(normalized_queries)
        ),
    )
    interaction.no_match = merged.no_match
    interaction.degraded = merged.degraded
    interaction.degradation_reasons = list(merged.degradation_reasons) or None
    await session.flush()
    return merged
