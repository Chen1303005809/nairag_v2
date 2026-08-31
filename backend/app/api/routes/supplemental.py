from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthenticatedSession, require_csrf, require_system_administrator
from app.db.session import get_db_session
from app.schemas.supplemental import (
    SupplementalDeleteResponse,
    SupplementalMaterialPageResponse,
    SupplementalMaterialResponse,
    SupplementalSupportedFileTypesResponse,
    SupplementalUploadResponse,
)
from app.services.supplemental_retrieval import (
    SupplementalAvailability,
    SupplementalRetriever,
    SupplementalUnavailableError,
    SupplementalUpstreamError,
    validate_document_id,
    validate_upload_filename,
)
from app.services.users import record_audit_event

router = APIRouter(prefix="/supplemental-materials", tags=["supplemental materials"])

MAX_SUPPLEMENTAL_UPLOAD_BYTES = 20 * 1024 * 1024


def _available_retriever(request: Request) -> SupplementalRetriever:
    retriever: SupplementalRetriever | None = getattr(
        request.app.state,
        "supplemental_retriever",
        None,
    )
    if (
        retriever is None
        or retriever.availability_snapshot().state is not SupplementalAvailability.AVAILABLE
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="全局补充资料服务暂不可用，请稍后重试",
        )
    return retriever


def _as_material_response(material) -> SupplementalMaterialResponse:
    return SupplementalMaterialResponse(
        document_id=material.document_id,
        title=material.title,
        status=material.status,
        progress=material.progress,
        chunks_count=material.chunks_count,
        track_id=material.track_id,
        error_message=material.error_message,
        created_at=material.created_at,
        updated_at=material.updated_at,
    )


def _upstream_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="全局补充资料服务暂不可用，请稍后重试",
    )


@router.get("/supported-file-types", response_model=SupplementalSupportedFileTypesResponse)
async def list_supported_file_types(
    request: Request,
    _actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
) -> SupplementalSupportedFileTypesResponse:
    retriever = _available_retriever(request)
    try:
        extensions = await retriever.supported_file_types()
    except (SupplementalUnavailableError, SupplementalUpstreamError) as exc:
        raise _upstream_unavailable(exc) from exc
    return SupplementalSupportedFileTypesResponse(extensions=extensions)


@router.get("", response_model=SupplementalMaterialPageResponse)
async def list_supplemental_materials(
    request: Request,
    _actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    statuses: Annotated[list[str] | None, Query(alias="status", max_length=20)] = None,
) -> SupplementalMaterialPageResponse:
    normalized_statuses = [
        value.strip().lower()
        for value in (statuses or [])
        if value.strip()
        and len(value.strip()) <= 80
        and value.strip().replace("_", "").isalnum()
    ]
    retriever = _available_retriever(request)
    try:
        result = await retriever.list_materials(
            page=page,
            page_size=page_size,
            statuses=normalized_statuses or None,
        )
    except (SupplementalUnavailableError, SupplementalUpstreamError) as exc:
        raise _upstream_unavailable(exc) from exc
    return SupplementalMaterialPageResponse(
        materials=[_as_material_response(material) for material in result.materials],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.post("", response_model=SupplementalUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_supplemental_material(
    file: Annotated[UploadFile, File(description="全局补充资料文件")],
    request: Request,
    actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SupplementalUploadResponse:
    retriever = _available_retriever(request)
    try:
        try:
            filename = validate_upload_filename(file.filename)
            content = await file.read(MAX_SUPPLEMENTAL_UPLOAD_BYTES + 1)
        finally:
            await file.close()
        if not content:
            raise ValueError("上传文件不能为空")
        if len(content) > MAX_SUPPLEMENTAL_UPLOAD_BYTES:
            raise ValueError("上传文件不能超过 20 MB")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    try:
        track_id = await retriever.upload_material(
            filename=filename,
            content=content,
            content_type=file.content_type,
        )
    except (SupplementalUnavailableError, SupplementalUpstreamError) as exc:
        raise _upstream_unavailable(exc) from exc
    record_audit_event(
        session,
        event_type="supplemental_material.uploaded",
        actor_user_id=actor.user.id,
        target_type="supplemental_material",
        payload={"filename": filename, "size_bytes": len(content), "track_id": track_id},
    )
    await session.commit()
    return SupplementalUploadResponse(track_id=track_id)


@router.delete("/{document_id}", response_model=SupplementalDeleteResponse)
async def delete_supplemental_material(
    document_id: str,
    request: Request,
    actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SupplementalDeleteResponse:
    try:
        safe_document_id = validate_document_id(document_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    retriever = _available_retriever(request)
    try:
        await retriever.delete_material(document_id=safe_document_id)
    except (SupplementalUnavailableError, SupplementalUpstreamError) as exc:
        raise _upstream_unavailable(exc) from exc
    record_audit_event(
        session,
        event_type="supplemental_material.deleted",
        actor_user_id=actor.user.id,
        target_type="supplemental_material",
        payload={"document_id": safe_document_id},
    )
    await session.commit()
    return SupplementalDeleteResponse()
