from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedSession,
    require_csrf,
    require_fully_authenticated_session,
)
from app.db.session import get_db_session
from app.schemas.search import (
    HelpfulFeedbackRequest,
    HelpfulFeedbackResponse,
    SearchParentGroupResponse,
    SearchRequest,
    SearchResponse,
    SearchResultResponse,
)
from app.services.search import (
    SearchKnowledgeBaseUnavailableError,
    SearchResultNotFoundError,
    SearchResultStaleError,
    record_helpful_feedback,
    search_published_content,
)

router = APIRouter(prefix="/search", tags=["search"])


def as_search_response(details) -> SearchResponse:
    return SearchResponse(
        search_event_id=details.event.id,
        query_mode=details.event.query_mode,
        no_match=details.event.no_match,
        no_match_guidance=details.no_match_guidance,
        groups=[
            SearchParentGroupResponse(
                parent_id=parent_revision.parent_id,
                parent_name=parent_revision.name,
                canonical_keyword=parent_revision.canonical_keyword,
                children=[
                    SearchResultResponse(
                        result_item_id=result.id,
                        rank=result.rank,
                        score=round(result.score, 6),
                        child_id=candidate.child.id,
                        knowledge_base_id=candidate.knowledge_base.id,
                        knowledge_base_name=candidate.knowledge_base.name,
                        child_revision_id=candidate.child_revision.id,
                        question=candidate.child_revision.question,
                        response_content=candidate.child_revision.response_content,
                        question_variants=[
                            variant.question_text for variant in candidate.question_variants
                        ],
                        follow_up_guidance=candidate.child_revision.follow_up_guidance,
                        question_type=candidate.child_revision.question_type,
                        business_object=candidate.child_revision.business_object,
                        purpose=candidate.child_revision.purpose,
                        customer_type=candidate.child_revision.customer_type,
                        feature_explanation=candidate.child_revision.feature_explanation,
                        example=candidate.child_revision.example,
                        helpful_count=candidate.publication.helpful_count,
                        match_reason=result.match_reason,
                    )
                    for result, candidate in items
                ],
            )
            for parent_revision, items in details.groups
        ],
    )


@router.post("", response_model=SearchResponse)
async def search_content(
    body: SearchRequest,
    request: Request,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SearchResponse:
    try:
        details = await search_published_content(
            session,
            user_id=user.user.id,
            query=body.query,
            ocr_text=body.ocr_text,
            knowledge_base_id=body.knowledge_base_id,
            limit=body.limit,
            index_backend=request.app.state.search_index_backend,
        )
    except SearchKnowledgeBaseUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="目标知识库不存在或未启用",
        ) from exc
    await session.commit()
    return as_search_response(details)


@router.post(
    "/events/{search_event_id}/feedback",
    response_model=HelpfulFeedbackResponse,
)
async def submit_helpful_feedback(
    search_event_id: UUID,
    body: HelpfulFeedbackRequest,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> HelpfulFeedbackResponse:
    try:
        _feedback, helpful_count, already_recorded = await record_helpful_feedback(
            session,
            user_id=user.user.id,
            search_event_id=search_event_id,
            result_item_id=body.result_item_id,
        )
    except SearchResultNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="检索结果不存在") from exc
    except SearchResultStaleError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该结果已不是当前发布版本，不能提交反馈",
        ) from exc
    await session.commit()
    return HelpfulFeedbackResponse(
        accepted=True,
        already_recorded=already_recorded,
        helpful_count=helpful_count,
    )
