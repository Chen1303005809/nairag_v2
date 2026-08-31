from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedSession,
    get_app_settings,
    require_csrf,
    require_fully_authenticated_session,
    require_system_administrator,
)
from app.core.config import Settings
from app.db.session import get_db_session
from app.models.knowledge_content import SearchAnnotationResultLabel
from app.schemas.knowledge_content import EvidenceAttachmentResponse, WebLinkInput
from app.schemas.search import (
    AnnotationFeedbackDetailResponse,
    AnnotationFeedbackListItemResponse,
    AnnotationFeedbackPageResponse,
    AnnotationFeedbackQueryDetailResponse,
    AnnotationFeedbackResultDetailResponse,
    AnnotationFeedbackSummaryResponse,
    AnnotationFeedbackUserResponse,
    ConversationSearchParentGroupResponse,
    ConversationSearchRequest,
    ConversationSearchResponse,
    ConversationSearchResultResponse,
    HelpfulFeedbackRequest,
    HelpfulFeedbackResponse,
    OcrRecognitionResponse,
    QueryBatchRequest,
    SearchAnnotationResultFeedbackResponse,
    SearchAnnotationReviewRequest,
    SearchAnnotationReviewResponse,
    SearchParentGroupResponse,
    SearchRequest,
    SearchResponse,
    SearchResultResponse,
)
from app.services.fast_search import (
    ConversationSearchDetails,
    FastSearchValidationError,
    conversation_assisted_search,
)
from app.services.llm import LlmConfigurationError, LlmOutputError, LlmProviderError
from app.services.ocr import (
    OcrInputError,
    OcrNoTextError,
    OcrProvider,
    OcrProviderError,
    OcrRecognitionTokenError,
    create_ocr_recognition_token,
    decode_ocr_recognition_token,
    validate_ocr_image,
)
from app.services.search import (
    SearchKnowledgeBaseUnavailableError,
    SearchPipelineOptions,
    SearchResultNotFoundError,
    SearchResultStaleError,
    record_helpful_feedback,
    search_published_content,
)
from app.services.search_annotations import (
    AnnotationFeedbackDetail,
    AnnotationFeedbackListItem,
    ResultFeedbackInput,
    SearchAnnotationFilterError,
    SearchAnnotationReviewConflictError,
    SearchAnnotationReviewInputError,
    SearchAnnotationReviewNotFoundError,
    SearchAnnotationReviewUnavailableError,
    get_annotation_feedback_detail,
    get_annotation_feedback_summary,
    list_annotation_feedback,
    record_search_annotation_review,
)
from app.services.search_batch import execute_query_batch
from app.services.users import record_audit_event

router = APIRouter(prefix="/search", tags=["search"])


def as_search_response(details) -> SearchResponse:
    return SearchResponse(
        search_event_id=details.event.id,
        search_interaction_id=(details.interaction.id if details.interaction is not None else None),
        query_mode=details.event.query_mode,
        no_match=details.event.no_match,
        no_match_guidance=details.no_match_guidance,
        degraded=details.degraded,
        degradation_reasons=list(details.degradation_reasons),
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
                        hybrid_score=(
                            round(result.hybrid_score, 6)
                            if result.hybrid_score is not None
                            else None
                        ),
                        rerank_score=(
                            round(result.rerank_score, 6)
                            if result.rerank_score is not None
                            else None
                        ),
                        selection_stage=result.selection_stage,
                        helpful_count_at_search=result.helpful_count_at_search,
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
                        attachments=[
                            EvidenceAttachmentResponse(
                                id=attachment.id,
                                name=attachment.name,
                                content_type=attachment.content_type,
                                size_bytes=attachment.size_bytes,
                            )
                            for attachment in candidate.attachments
                        ],
                        web_links=[
                            WebLinkInput(title=web_link.title, url=web_link.url)
                            for web_link in candidate.web_links
                        ],
                        helpful_count=candidate.publication.helpful_count,
                        match_reason=result.match_reason,
                        matched_field=result.matched_field,
                    )
                    for result, candidate in items
                ],
            )
            for parent_revision, items in details.groups
        ],
    )


def as_conversation_search_response(
    details: ConversationSearchDetails,
) -> ConversationSearchResponse:
    return ConversationSearchResponse(
        search_interaction_id=(
            details.interaction.id if details.interaction is not None else None
        ),
        queries=list(details.queries),
        total_candidates=details.total_candidates,
        no_query_guidance=details.no_query_guidance,
        no_match=details.no_match,
        no_match_guidance=details.no_match_guidance,
        degraded=details.degraded,
        degradation_reasons=list(details.degradation_reasons),
        groups=[
            ConversationSearchParentGroupResponse(
                parent_id=parent_revision.parent_id,
                parent_name=parent_revision.name,
                canonical_keyword=parent_revision.canonical_keyword,
                children=[
                    ConversationSearchResultResponse(
                        result_item_id=item.result_item.id,
                        search_event_id=item.result_item.search_event_id,
                        rank=rank,
                        score=round(item.best_score, 6),
                        hybrid_score=(
                            round(item.result_item.hybrid_score, 6)
                            if item.result_item.hybrid_score is not None
                            else None
                        ),
                        rerank_score=(
                            round(item.result_item.rerank_score, 6)
                            if item.result_item.rerank_score is not None
                            else None
                        ),
                        selection_stage=item.result_item.selection_stage,
                        helpful_count_at_search=item.result_item.helpful_count_at_search,
                        child_id=item.candidate.child.id,
                        knowledge_base_id=item.candidate.knowledge_base.id,
                        knowledge_base_name=item.candidate.knowledge_base.name,
                        child_revision_id=item.candidate.child_revision.id,
                        question=item.candidate.child_revision.question,
                        response_content=item.candidate.child_revision.response_content,
                        question_variants=[
                            variant.question_text for variant in item.candidate.question_variants
                        ],
                        follow_up_guidance=item.candidate.child_revision.follow_up_guidance,
                        question_type=item.candidate.child_revision.question_type,
                        business_object=item.candidate.child_revision.business_object,
                        purpose=item.candidate.child_revision.purpose,
                        customer_type=item.candidate.child_revision.customer_type,
                        feature_explanation=item.candidate.child_revision.feature_explanation,
                        example=item.candidate.child_revision.example,
                        attachments=[
                            EvidenceAttachmentResponse(
                                id=attachment.id,
                                name=attachment.name,
                                content_type=attachment.content_type,
                                size_bytes=attachment.size_bytes,
                            )
                            for attachment in item.candidate.attachments
                        ],
                        web_links=[
                            WebLinkInput(title=web_link.title, url=web_link.url)
                            for web_link in item.candidate.web_links
                        ],
                        helpful_count=item.candidate.publication.helpful_count,
                        match_reason=item.result_item.match_reason,
                        matched_field=item.result_item.matched_field,
                        matched_queries=list(item.matched_queries),
                    )
                    for rank, item in enumerate(items, start=1)
                ],
            )
            for parent_revision, items in details.groups
        ],
    )


def as_annotation_feedback_list_item(
    item: AnnotationFeedbackListItem,
) -> AnnotationFeedbackListItemResponse:
    return AnnotationFeedbackListItemResponse(
        id=item.id,
        submitted_by=AnnotationFeedbackUserResponse(
            id=item.submitted_by.id,
            username=item.submitted_by.username,
            display_name=item.submitted_by.display_name,
        ),
        interaction_type=item.interaction_type,
        queries=list(item.queries),
        target_knowledge_base_id=item.target_knowledge_base_id,
        target_knowledge_base_name=item.target_knowledge_base_name,
        high_score_irrelevant_count=item.high_score_irrelevant_count,
        low_score_relevant_count=item.low_score_relevant_count,
        normal_count=item.normal_count,
        other_count=item.other_count,
        searched_at=item.searched_at,
        submitted_at=item.submitted_at,
        result_count=item.result_count,
    )


def as_annotation_feedback_detail(
    detail: AnnotationFeedbackDetail,
) -> AnnotationFeedbackDetailResponse:
    return AnnotationFeedbackDetailResponse(
        **as_annotation_feedback_list_item(detail).model_dump(),
        no_match=detail.no_match,
        degraded=detail.degraded,
        degradation_reasons=list(detail.degradation_reasons),
        query_details=[
            AnnotationFeedbackQueryDetailResponse(
                search_event_id=query.search_event_id,
                query_order=query.query_order,
                query_text=query.query_text,
                ocr_text=query.ocr_text,
                no_match=query.no_match,
                results=[
                    AnnotationFeedbackResultDetailResponse(
                        result_item_id=result.result_item_id,
                        rank=result.rank,
                        score=result.score,
                        hybrid_score=result.hybrid_score,
                        rerank_score=result.rerank_score,
                        selection_stage=result.selection_stage,
                        matched_field=result.matched_field,
                        parent_name=result.parent_name,
                        question=result.question,
                        knowledge_base_id=result.knowledge_base_id,
                        knowledge_base_name=result.knowledge_base_name,
                        matched_queries=list(result.matched_queries),
                        feedback_type=result.feedback_type,
                        other_note=result.other_note,
                    )
                    for result in query.results
                ],
            )
            for query in detail.query_details
        ],
    )


@router.post("/ocr", response_model=OcrRecognitionResponse)
async def recognize_search_image(
    image: Annotated[UploadFile, File(description="PNG、JPEG 或 WebP 查询图片")],
    request: Request,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    purpose: Literal["search", "conversation"] = "search",
) -> OcrRecognitionResponse:
    """Recognize a transient query image and return a short-lived trusted result."""

    try:
        try:
            image_bytes = await image.read(settings.ocr_max_image_bytes + 1)
        finally:
            await image.close()
        media_type = validate_ocr_image(image_bytes, max_bytes=settings.ocr_max_image_bytes)
    except OcrInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    provider: OcrProvider | None = getattr(request.app.state, "ocr_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OCR 服务尚未配置，请联系管理员",
        )
    try:
        recognition = await provider.recognize(image_bytes, media_type)
    except OcrInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except OcrNoTextError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except OcrProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OCR 服务暂时不可用，请稍后重试",
        ) from exc

    if purpose == "conversation":
        # Conversation OCR is only an in-browser preprocessing step. Keeping
        # its text in an audit payload would retain a fragment of the raw chat,
        # which fast upload and fast search deliberately avoid.
        record_audit_event(
            session,
            event_type="conversation.ocr_recognized",
            actor_user_id=user.user.id,
            target_type="conversation_ocr",
            payload={
                "model_version": recognition.model_version,
                "image_sha256": recognition.image_sha256,
            },
        )
    else:
        record_audit_event(
            session,
            event_type="search.ocr_recognized",
            actor_user_id=user.user.id,
            target_type="search_ocr",
            payload={
                "ocr_text": recognition.text,
                "keywords": list(recognition.keywords),
                "confidence": recognition.confidence,
                "model_version": recognition.model_version,
                "image_sha256": recognition.image_sha256,
            },
        )
    await session.commit()
    return OcrRecognitionResponse(
        text=recognition.text,
        keywords=list(recognition.keywords),
        confidence=recognition.confidence,
        model_version=recognition.model_version,
        recognition_token=create_ocr_recognition_token(
            recognition,
            user_id=user.user.id,
            settings=settings,
        ),
    )


@router.post("", response_model=SearchResponse)
async def search_content(
    body: SearchRequest,
    request: Request,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> SearchResponse:
    ocr_text = body.ocr_text
    ocr_recognition = None
    if body.ocr_recognition_token is not None:
        try:
            ocr_recognition = decode_ocr_recognition_token(
                body.ocr_recognition_token,
                user_id=user.user.id,
                settings=settings,
            )
        except OcrRecognitionTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="OCR 识别凭据无效或已过期，请重新上传图片",
            ) from exc
        ocr_text = ocr_recognition.text
    llm_provider = getattr(request.app.state, "llm_provider", None)
    try:
        details = await search_published_content(
            session,
            user_id=user.user.id,
            query=body.query,
            ocr_text=ocr_text,
            knowledge_base_id=body.knowledge_base_id,
            retrieval_mode=body.retrieval_mode,
            parent_type=body.parent_type,
            question_type=body.question_type,
            business_object=body.business_object,
            purpose=body.purpose,
            customer_type=body.customer_type,
            limit=body.limit,
            index_backend=request.app.state.search_index_backend,
            ocr_recognition=ocr_recognition,
            ocr_keyword_fallback_min_confidence=settings.ocr_keyword_fallback_min_confidence,
            reranker=getattr(request.app.state, "reranker_provider", None),
            relevance_judge=(
                llm_provider
                if hasattr(llm_provider, "judge_search_relevance")
                else None
            ),
            pipeline_options=SearchPipelineOptions(
                high_confidence_threshold=settings.search_high_confidence_threshold,
                rerank_threshold=settings.search_rerank_threshold,
                fallback_threshold=settings.search_fallback_threshold,
                candidate_pool_size=settings.search_candidate_pool_size,
            ),
        )
    except SearchKnowledgeBaseUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="目标知识库不存在或未启用",
        ) from exc
    await session.commit()
    return as_search_response(details)


@router.post("/conversation-assist", response_model=ConversationSearchResponse)
async def conversation_assisted_search_content(
    body: ConversationSearchRequest,
    request: Request,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ConversationSearchResponse:
    """Extract de-identified queries from one conversation and search them."""

    provider = getattr(request.app.state, "llm_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="智能处理服务尚未配置，请联系管理员",
        )
    try:
        details = await conversation_assisted_search(
            session,
            user_id=user.user.id,
            messages=body.messages,
            knowledge_base_id=body.knowledge_base_id,
            limit=body.limit,
            settings=settings,
            index_backend=request.app.state.search_index_backend,
            provider=provider,
            reranker=getattr(request.app.state, "reranker_provider", None),
        )
    except FastSearchValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except LlmConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="智能处理服务配置错误，请联系管理员",
        ) from exc
    except LlmProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="智能处理服务暂时不可用，请稍后重试",
        ) from exc
    except LlmOutputError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="智能处理服务返回无效结果，请稍后重试",
        ) from exc
    except SearchKnowledgeBaseUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="目标知识库不存在或未启用",
        ) from exc
    await session.commit()
    return as_conversation_search_response(details)


@router.post("/query-batch", response_model=ConversationSearchResponse)
async def query_batch_search_content(
    body: QueryBatchRequest,
    request: Request,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ConversationSearchResponse:
    """Run edited quick-search queries as one persisted user-visible interaction."""

    llm_provider = getattr(request.app.state, "llm_provider", None)
    try:
        details = await execute_query_batch(
            session,
            user_id=user.user.id,
            queries=body.queries,
            knowledge_base_id=body.knowledge_base_id,
            limit=body.limit,
            settings=settings,
            index_backend=request.app.state.search_index_backend,
            reranker=getattr(request.app.state, "reranker_provider", None),
            relevance_judge=(
                llm_provider
                if hasattr(llm_provider, "judge_search_relevance")
                else None
            ),
        )
    except SearchKnowledgeBaseUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="目标知识库不存在或未启用",
        ) from exc
    await session.commit()
    return as_conversation_search_response(details)


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


@router.post(
    "/interactions/{interaction_id}/annotation-feedback",
    response_model=SearchAnnotationReviewResponse,
)
async def submit_search_annotation_review(
    interaction_id: UUID,
    body: SearchAnnotationReviewRequest,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SearchAnnotationReviewResponse:
    try:
        review, result_feedbacks, already_recorded = await record_search_annotation_review(
            session,
            user_id=user.user.id,
            interaction_id=interaction_id,
            result_feedbacks=[
                ResultFeedbackInput(
                    search_result_item_id=item.search_result_item_id,
                    feedback_type=item.feedback_type,
                    other_note=item.other_note,
                )
                for item in body.result_feedbacks
            ],
        )
    except SearchAnnotationReviewUnavailableError as exc:
        # The same response covers absent, non-owned, and non-annotatable IDs.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="检索记录不存在") from exc
    except SearchAnnotationReviewInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except SearchAnnotationReviewConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该检索已完成过不同内容的标注 Review，标注不可修改",
        ) from exc
    await session.commit()
    return SearchAnnotationReviewResponse(
        accepted=True,
        already_recorded=already_recorded,
        reviewed_result_count=review.reviewed_result_count,
        submitted_at=review.submitted_at,
        result_feedbacks=[
            SearchAnnotationResultFeedbackResponse(
                search_result_item_id=feedback.search_result_item_id,
                feedback_type=feedback.feedback_type,
                other_note=feedback.other_note,
            )
            for feedback in result_feedbacks
        ],
    )


@router.get(
    "/admin/annotation-feedback/summary",
    response_model=AnnotationFeedbackSummaryResponse,
)
async def get_search_annotation_feedback_summary(
    _actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    annotated_from: datetime | None = None,
    annotated_to: datetime | None = None,
    knowledge_base_id: UUID | None = None,
    query_keyword: str | None = Query(default=None, max_length=4_000),
) -> AnnotationFeedbackSummaryResponse:
    try:
        summary = await get_annotation_feedback_summary(
            session,
            annotated_from=annotated_from,
            annotated_to=annotated_to,
            knowledge_base_id=knowledge_base_id,
            query_keyword=query_keyword,
        )
    except SearchAnnotationFilterError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return AnnotationFeedbackSummaryResponse(
        completed_review_count=summary.completed_review_count,
        annotated_result_count=summary.annotated_result_count,
        high_score_irrelevant_count=summary.high_score_irrelevant_count,
        low_score_relevant_count=summary.low_score_relevant_count,
        normal_count=summary.normal_count,
        other_count=summary.other_count,
    )


@router.get(
    "/admin/annotation-feedback",
    response_model=AnnotationFeedbackPageResponse,
)
async def list_search_annotation_feedback(
    _actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    feedback_type: SearchAnnotationResultLabel | None = None,
    annotated_from: datetime | None = None,
    annotated_to: datetime | None = None,
    knowledge_base_id: UUID | None = None,
    query_keyword: str | None = Query(default=None, max_length=4_000),
) -> AnnotationFeedbackPageResponse:
    try:
        feedback_page = await list_annotation_feedback(
            session,
            page=page,
            page_size=page_size,
            feedback_type=feedback_type,
            annotated_from=annotated_from,
            annotated_to=annotated_to,
            knowledge_base_id=knowledge_base_id,
            query_keyword=query_keyword,
        )
    except SearchAnnotationFilterError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return AnnotationFeedbackPageResponse(
        items=[as_annotation_feedback_list_item(item) for item in feedback_page.items],
        total=feedback_page.total,
        page=feedback_page.page,
        page_size=feedback_page.page_size,
    )


@router.get(
    "/admin/annotation-feedback/{feedback_id}",
    response_model=AnnotationFeedbackDetailResponse,
)
async def get_search_annotation_feedback_detail(
    feedback_id: UUID,
    _actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AnnotationFeedbackDetailResponse:
    try:
        detail = await get_annotation_feedback_detail(session, feedback_id=feedback_id)
    except SearchAnnotationReviewNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="标注记录不存在") from exc
    return as_annotation_feedback_detail(detail)
