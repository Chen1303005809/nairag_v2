from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedSession,
    get_app_settings,
    require_csrf,
    require_fully_authenticated_session,
)
from app.core.config import Settings
from app.db.session import get_db_session
from app.schemas.knowledge_content import EvidenceAttachmentResponse, WebLinkInput
from app.schemas.search import (
    ConversationSearchParentGroupResponse,
    ConversationSearchRequest,
    ConversationSearchResponse,
    ConversationSearchResultResponse,
    HelpfulFeedbackRequest,
    HelpfulFeedbackResponse,
    OcrRecognitionResponse,
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
    SearchResultNotFoundError,
    SearchResultStaleError,
    record_helpful_feedback,
    search_published_content,
)
from app.services.users import record_audit_event

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
        queries=list(details.queries),
        total_candidates=details.total_candidates,
        no_query_guidance=details.no_query_guidance,
        no_match=details.no_match,
        no_match_guidance=details.no_match_guidance,
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
