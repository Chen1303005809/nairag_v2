"""HTTP boundary for DOC/DOCX attachment import batches."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedSession,
    get_app_settings,
    require_csrf,
    require_fully_authenticated_session,
)
from app.api.routes.knowledge_content import as_attachment_response, as_submission_response
from app.core.config import Settings
from app.db.session import get_db_session
from app.schemas.attachment_ingestion import (
    AttachmentImportBatchDetailResponse,
    AttachmentImportBatchResponse,
    AttachmentImportConfirmResponse,
    AttachmentImportProposal,
    ConfirmAttachmentImportRequest,
)
from app.services.attachment_import import (
    AttachmentImportConfirmationError,
    AttachmentImportDetails,
    AttachmentImportExpiredError,
    AttachmentImportNotFoundError,
    AttachmentImportStateError,
    cancel_attachment_import_batch,
    confirm_attachment_import,
    create_attachment_import_batch,
    get_attachment_import_details,
    list_attachment_import_batches,
    retry_attachment_import_batch,
)
from app.services.attachment_storage import AttachmentStorage, AttachmentStorageError
from app.services.attachments import AttachmentValidationError, validate_attachment_upload
from app.services.knowledge_content import (
    AttachmentNotAllowedError,
    AttachmentNotFoundError,
    ParentNotAvailableError,
    ParentNotFoundError,
    PendingSubmissionExistsError,
    TargetKnowledgeBaseNotAllowedError,
    TargetKnowledgeBaseUnavailableError,
)
from app.services.users import record_audit_event

router = APIRouter(prefix="/attachment-ingestion", tags=["attachment ingestion"])


def _timestamp(value) -> str | None:
    return value.isoformat() if value is not None else None


def as_attachment_import_batch_response(
    details: AttachmentImportDetails,
) -> AttachmentImportBatchResponse:
    batch = details.batch
    return AttachmentImportBatchResponse(
        id=batch.id,
        status=batch.status.value,
        attachment=as_attachment_response(details.attachment),
        warnings=list(batch.warnings),
        image_count=batch.image_count,
        extracted_char_count=batch.extracted_char_count,
        model_version=batch.model_version,
        attempt_count=batch.attempt_count,
        last_error=batch.last_error,
        expires_at=batch.expires_at.isoformat(),
        created_at=batch.created_at.isoformat(),
        completed_at=_timestamp(batch.completed_at),
        submitted_at=_timestamp(batch.submitted_at),
        final_submission_id=batch.final_submission_id,
        final_parent_id=batch.final_parent_id,
    )


def as_attachment_import_batch_detail_response(
    details: AttachmentImportDetails,
) -> AttachmentImportBatchDetailResponse:
    response = as_attachment_import_batch_response(details)
    return AttachmentImportBatchDetailResponse(
        **response.model_dump(),
        proposal=(
            AttachmentImportProposal.model_validate(details.batch.proposal)
            if details.batch.proposal is not None
            else None
        ),
    )


def _as_http_error(error: Exception) -> HTTPException:
    if isinstance(error, AttachmentImportNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="附件解析批次不存在")
    if isinstance(error, AttachmentImportExpiredError):
        return HTTPException(status_code=status.HTTP_410_GONE, detail=str(error))
    if isinstance(error, AttachmentImportStateError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, AttachmentImportConfirmationError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    if isinstance(error, ParentNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="问题大类不存在")
    if isinstance(error, ParentNotAvailableError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只能归并已发布的问题大类",
        )
    if isinstance(error, PendingSubmissionExistsError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该问题大类存在待审核或索引中的修订，暂不能归并附件",
        )
    if isinstance(error, TargetKnowledgeBaseUnavailableError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="目标知识库不存在或未启用",
        )
    if isinstance(error, TargetKnowledgeBaseNotAllowedError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="目标知识库不可用",
        )
    if isinstance(error, AttachmentNotFoundError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="原附件不存在",
        )
    if isinstance(error, AttachmentNotAllowedError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="原附件不可用或合并后超出附件数量/总大小限制",
        )
    raise error


@router.post(
    "/batches",
    response_model=AttachmentImportBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_attachment_import(
    attachment_file: Annotated[UploadFile, File(description="一个 DOC 或 DOCX 附件")],
    request: Request,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AttachmentImportBatchResponse:
    try:
        try:
            content = await attachment_file.read(settings.attachment_max_file_bytes + 1)
        finally:
            await attachment_file.close()
        upload = validate_attachment_upload(
            filename=attachment_file.filename,
            declared_content_type=attachment_file.content_type,
            content=content,
            max_file_bytes=settings.attachment_max_file_bytes,
        )
        if Path(upload.name).suffix.casefold() not in {".doc", ".docx"}:
            raise AttachmentValidationError("附件解析仅支持 DOC 或 DOCX 文件")
    except AttachmentValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    storage: AttachmentStorage = request.app.state.attachment_storage
    try:
        await storage.put_object(upload.storage_key, upload.content, upload.content_type)
    except AttachmentStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="附件存储暂时不可用，请稍后重试",
        ) from exc
    try:
        batch = await create_attachment_import_batch(
            session,
            owner_user_id=user.user.id,
            upload=upload,
            settings=settings,
        )
        details = await get_attachment_import_details(
            session,
            owner_user_id=user.user.id,
            batch_id=batch.id,
        )
        record_audit_event(
            session,
            event_type="attachment_ingestion.batch_created",
            actor_user_id=user.user.id,
            target_type="attachment_ingestion_batch",
            target_id=batch.id,
            payload={
                "attachment_sha256": upload.checksum_sha256,
                "size_bytes": len(upload.content),
            },
        )
        await session.commit()
    except Exception:
        await session.rollback()
        try:
            await storage.delete_object(upload.storage_key)
        except AttachmentStorageError:
            pass
        raise
    return as_attachment_import_batch_response(details)


@router.get("/batches", response_model=list[AttachmentImportBatchResponse])
async def list_my_attachment_imports(
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[AttachmentImportBatchResponse]:
    return [
        as_attachment_import_batch_response(details)
        for details in await list_attachment_import_batches(session, owner_user_id=user.user.id)
    ]


@router.get("/batches/{batch_id}", response_model=AttachmentImportBatchDetailResponse)
async def get_my_attachment_import(
    batch_id: UUID,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AttachmentImportBatchDetailResponse:
    try:
        details = await get_attachment_import_details(
            session,
            owner_user_id=user.user.id,
            batch_id=batch_id,
        )
    except AttachmentImportNotFoundError as exc:
        raise _as_http_error(exc) from exc
    return as_attachment_import_batch_detail_response(details)


@router.post("/batches/{batch_id}/retry", response_model=AttachmentImportBatchResponse)
async def retry_my_attachment_import(
    batch_id: UUID,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AttachmentImportBatchResponse:
    try:
        batch = await retry_attachment_import_batch(
            session,
            owner_user_id=user.user.id,
            batch_id=batch_id,
        )
        details = await get_attachment_import_details(
            session,
            owner_user_id=user.user.id,
            batch_id=batch.id,
        )
    except (AttachmentImportNotFoundError, AttachmentImportStateError) as exc:
        raise _as_http_error(exc) from exc
    record_audit_event(
        session,
        event_type="attachment_ingestion.batch_retried",
        actor_user_id=user.user.id,
        target_type="attachment_ingestion_batch",
        target_id=batch_id,
        payload={},
    )
    await session.commit()
    return as_attachment_import_batch_response(details)


@router.post("/batches/{batch_id}/confirm", response_model=AttachmentImportConfirmResponse)
async def confirm_my_attachment_import(
    batch_id: UUID,
    body: ConfirmAttachmentImportRequest,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AttachmentImportConfirmResponse:
    try:
        confirmation = await confirm_attachment_import(
            session,
            owner_user_id=user.user.id,
            batch_id=batch_id,
            request=body,
        )
    except (
        AttachmentImportNotFoundError,
        AttachmentImportStateError,
        AttachmentImportConfirmationError,
        ParentNotFoundError,
        ParentNotAvailableError,
        PendingSubmissionExistsError,
        TargetKnowledgeBaseUnavailableError,
        TargetKnowledgeBaseNotAllowedError,
        AttachmentNotFoundError,
        AttachmentNotAllowedError,
    ) as exc:
        raise _as_http_error(exc) from exc
    record_audit_event(
        session,
        event_type="attachment_ingestion.batch_confirmed",
        actor_user_id=user.user.id,
        target_type="attachment_ingestion_batch",
        target_id=batch_id,
        payload={
            "review_submission_id": str(confirmation.submission.submission.id),
            "parent_id": str(confirmation.parent_id),
            "draft_count": len(confirmation.created_draft_ids),
        },
    )
    await session.commit()
    return AttachmentImportConfirmResponse(
        submission=as_submission_response(confirmation.submission, submitter=user.user),
        parent_id=confirmation.parent_id,
        created_draft_ids=confirmation.created_draft_ids,
    )


@router.delete("/batches/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_my_attachment_import(
    batch_id: UUID,
    request: Request,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    storage: AttachmentStorage = request.app.state.attachment_storage
    try:
        await cancel_attachment_import_batch(
            session,
            storage=storage,
            owner_user_id=user.user.id,
            batch_id=batch_id,
        )
    except (
        AttachmentImportNotFoundError,
        AttachmentImportStateError,
        AttachmentStorageError,
    ) as exc:
        if isinstance(exc, AttachmentStorageError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="附件存储暂时不可用，请稍后重试",
            ) from exc
        raise _as_http_error(exc) from exc
    record_audit_event(
        session,
        event_type="attachment_ingestion.batch_cancelled",
        actor_user_id=user.user.id,
        target_type="attachment_ingestion_batch",
        target_id=batch_id,
        payload={},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
