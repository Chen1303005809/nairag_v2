from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedSession,
    require_csrf,
    require_fully_authenticated_session,
)
from app.db.session import get_db_session
from app.models.knowledge_content import EvidenceAttachment
from app.schemas.drafts import KnowledgeDraftResponse
from app.schemas.intelligent_ingestion import (
    CreateIngestionBatchRequest,
    IngestionBatchDetailResponse,
    IngestionBatchResponse,
)
from app.schemas.knowledge_content import EvidenceAttachmentResponse, WebLinkInput
from app.services.drafts import format_draft_datetime
from app.services.intelligent_ingestion import (
    ConversationValidationError,
    IngestionBatchNotFoundError,
    create_ingestion_batch,
    get_ingestion_batch_details,
    list_ingestion_batches,
)
from app.services.users import record_audit_event

router = APIRouter(prefix="/intelligent-ingestion", tags=["intelligent ingestion"])


async def resolve_draft_attachments(
    session: AsyncSession,
    drafts: list,
) -> dict[UUID, EvidenceAttachment]:
    attachment_ids = {
        UUID(value) for draft in drafts for value in draft.attachments
    }
    if not attachment_ids:
        return {}
    attachments = list(
        (
            await session.scalars(
                select(EvidenceAttachment).where(EvidenceAttachment.id.in_(attachment_ids))
            )
        ).all()
    )
    return {attachment.id: attachment for attachment in attachments}


def _batch_created_at(batch) -> str:
    return batch.created_at.isoformat()


def _batch_completed_at(batch) -> str | None:
    return batch.completed_at.isoformat() if batch.completed_at is not None else None


def as_ingestion_batch_response(batch) -> IngestionBatchResponse:
    return IngestionBatchResponse(
        id=batch.id,
        status=batch.status.value,
        message_count=batch.message_count,
        source_hash=batch.source_hash,
        generated_count=batch.generated_count,
        rejected_count=batch.rejected_count,
        rejection_reasons=list(batch.rejection_reasons),
        model_version=batch.model_version,
        last_error=batch.last_error,
        created_at=_batch_created_at(batch),
        completed_at=_batch_completed_at(batch),
    )


def as_knowledge_draft_response(
    draft,
    attachments_by_id: dict[UUID, EvidenceAttachment] | None = None,
) -> KnowledgeDraftResponse:
    resolved_attachments = attachments_by_id or {}
    draft_attachments = [
        resolved_attachments.get(UUID(value)) for value in draft.attachments
    ]
    return KnowledgeDraftResponse(
        id=draft.id,
        source=draft.source.value,
        parent_id=draft.parent_id,
        ingestion_batch_id=draft.ingestion_batch_id,
        question=draft.question,
        response_content=draft.response_content,
        question_variants=list(draft.question_variants),
        follow_up_guidance=draft.follow_up_guidance,
        question_type=draft.question_type,
        business_object=draft.business_object,
        purpose=draft.purpose,
        customer_type=draft.customer_type,
        feature_explanation=draft.feature_explanation,
        example=draft.example,
        internal_notes=draft.internal_notes,
        attachments=[
            EvidenceAttachmentResponse(
                id=attachment.id,
                name=attachment.name,
                content_type=attachment.content_type,
                size_bytes=attachment.size_bytes,
            )
            for attachment in draft_attachments
            if attachment is not None
        ],
        web_links=[WebLinkInput(**web_link) for web_link in draft.web_links],
        knowledge_base_ids=[UUID(value) for value in draft.knowledge_base_ids],
        source_hash=draft.source_hash,
        extracted_at=format_draft_datetime(draft.extracted_at),
        model_version=draft.model_version,
        created_at=draft.created_at.isoformat(),
        updated_at=draft.updated_at.isoformat(),
    )


@router.post("/batches", response_model=IngestionBatchResponse, status_code=status.HTTP_201_CREATED)
async def create_fast_upload_batch(
    body: CreateIngestionBatchRequest,
    request: Request,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> IngestionBatchResponse:
    provider = getattr(request.app.state, "llm_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="智能处理服务尚未配置，请联系管理员",
        )
    settings = request.app.state.settings
    try:
        batch = await create_ingestion_batch(
            session,
            owner_user_id=user.user.id,
            messages=body.messages,
            settings=settings,
        )
    except ConversationValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    record_audit_event(
        session,
        event_type="intelligent_ingestion.batch_created",
        actor_user_id=user.user.id,
        target_type="intelligent_ingestion_batch",
        target_id=batch.id,
        payload={
            "message_count": batch.message_count,
            "source_hash": batch.source_hash,
        },
    )
    await session.commit()
    return as_ingestion_batch_response(batch)


@router.get("/batches", response_model=list[IngestionBatchResponse])
async def list_my_ingestion_batches(
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[IngestionBatchResponse]:
    batches = await list_ingestion_batches(session, owner_user_id=user.user.id)
    return [as_ingestion_batch_response(batch) for batch in batches]


@router.get("/batches/{batch_id}", response_model=IngestionBatchDetailResponse)
async def get_my_ingestion_batch(
    batch_id: UUID,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> IngestionBatchDetailResponse:
    try:
        details = await get_ingestion_batch_details(
            session,
            owner_user_id=user.user.id,
            batch_id=batch_id,
        )
    except IngestionBatchNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="批次不存在") from exc
    attachments_by_id = await resolve_draft_attachments(session, details.drafts)
    response = as_ingestion_batch_response(details.batch)
    return IngestionBatchDetailResponse(
        **response.model_dump(),
        drafts=[
            as_knowledge_draft_response(draft, attachments_by_id)
            for draft in details.drafts
        ],
    )
