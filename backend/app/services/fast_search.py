from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services.conversation import (
    NormalizedConversationMessage,
    validate_conversation,
)
from app.services.embedding import RerankerProvider
from app.services.llm import LlmProvider
from app.services.search_batch import QueryBatchSearchDetails, execute_query_batch
from app.services.supplemental_retrieval import SupplementalRetriever


class FastSearchValidationError(ValueError):
    pass


NO_QUERY_GUIDANCE = "未发现待查询问题"

# Keep the public name used by existing route and service callers while the
# shared batch implementation lives in search_batch.py.
ConversationSearchDetails = QueryBatchSearchDetails


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
    supplemental_retriever: SupplementalRetriever | None = None,
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
            interaction=None,
            queries=[],
            total_candidates=0,
            no_query_guidance=NO_QUERY_GUIDANCE,
            no_match=False,
            no_match_guidance=None,
            groups=[],
            supplemental_results=[],
            degraded=False,
            degradation_reasons=(),
        )

    return await execute_query_batch(
        session,
        user_id=user_id,
        queries=extraction.queries,
        knowledge_base_id=knowledge_base_id,
        limit=limit,
        settings=settings,
        index_backend=index_backend,
        reranker=reranker,
        relevance_judge=provider if hasattr(provider, "judge_search_relevance") else None,
        total_candidates=extraction.total_candidates,
        supplemental_retriever=supplemental_retriever,
    )
