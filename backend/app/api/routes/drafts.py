from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedSession,
    require_csrf,
    require_fully_authenticated_session,
)
from app.api.routes.intelligent_ingestion import (
    as_knowledge_draft_response,
    resolve_draft_attachments,
)
from app.api.routes.knowledge_content import as_submission_response
from app.db.session import get_db_session
from app.schemas.drafts import KnowledgeDraftInput, KnowledgeDraftResponse
from app.schemas.knowledge_content import ReviewSubmissionResponse
from app.services.drafts import (
    DraftNotFoundError,
    DraftNotSubmittableError,
    create_manual_draft,
    delete_draft,
    list_drafts,
    submit_draft,
    update_draft,
)
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

router = APIRouter(prefix="/knowledge-content", tags=["knowledge drafts"])


@router.get("/drafts", response_model=list[KnowledgeDraftResponse])
async def list_my_knowledge_drafts(
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[KnowledgeDraftResponse]:
    drafts = await list_drafts(session, owner_user_id=user.user.id)
    attachments_by_id = await resolve_draft_attachments(session, drafts)
    return [
        as_knowledge_draft_response(draft, attachments_by_id) for draft in drafts
    ]


@router.post("/drafts", response_model=KnowledgeDraftResponse, status_code=status.HTTP_201_CREATED)
async def create_knowledge_draft(
    body: KnowledgeDraftInput,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowledgeDraftResponse:
    draft = await create_manual_draft(session, owner_user_id=user.user.id, content=body)
    attachments_by_id = await resolve_draft_attachments(session, [draft])
    record_audit_event(
        session,
        event_type="knowledge_draft.created",
        actor_user_id=user.user.id,
        target_type="knowledge_draft",
        target_id=draft.id,
        payload={"source": draft.source.value},
    )
    await session.commit()
    return as_knowledge_draft_response(draft, attachments_by_id)


@router.patch("/drafts/{draft_id}", response_model=KnowledgeDraftResponse)
async def update_knowledge_draft(
    draft_id: UUID,
    body: KnowledgeDraftInput,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowledgeDraftResponse:
    try:
        draft = await update_draft(
            session,
            owner_user_id=user.user.id,
            draft_id=draft_id,
            content=body,
        )
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="草稿不存在") from exc
    attachments_by_id = await resolve_draft_attachments(session, [draft])
    await session.commit()
    return as_knowledge_draft_response(draft, attachments_by_id)


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_draft(
    draft_id: UUID,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    try:
        await delete_draft(session, owner_user_id=user.user.id, draft_id=draft_id)
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="草稿不存在") from exc
    record_audit_event(
        session,
        event_type="knowledge_draft.deleted",
        actor_user_id=user.user.id,
        target_type="knowledge_draft",
        target_id=draft_id,
        payload={},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/drafts/{draft_id}/submit",
    response_model=ReviewSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_knowledge_draft(
    draft_id: UUID,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReviewSubmissionResponse:
    try:
        submission = await submit_draft(
            session,
            owner_user_id=user.user.id,
            draft_id=draft_id,
        )
    except DraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="草稿不存在") from exc
    except DraftNotSubmittableError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except (
        ParentNotFoundError,
        ParentNotAvailableError,
        TargetKnowledgeBaseUnavailableError,
        TargetKnowledgeBaseNotAllowedError,
        PendingSubmissionExistsError,
        AttachmentNotFoundError,
        AttachmentNotAllowedError,
    ) as exc:
        if isinstance(exc, ParentNotAvailableError):
            detail = "父类尚未完成可用审核，暂不能提交普通子条目"
        elif isinstance(exc, TargetKnowledgeBaseUnavailableError):
            detail = "目标知识库不存在或未启用"
        elif isinstance(exc, TargetKnowledgeBaseNotAllowedError):
            detail = "普通子条目的目标知识库必须属于父类主子条目的已发布知识库"
        elif isinstance(exc, PendingSubmissionExistsError):
            detail = "目标内容已有未结束的候选提交，请等待审核、发布或驳回"
        elif isinstance(exc, AttachmentNotFoundError):
            detail = "附件不存在"
        else:
            detail = "内容不存在"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        ) from exc
    record_audit_event(
        session,
        event_type="knowledge_draft.submitted",
        actor_user_id=user.user.id,
        target_type="knowledge_draft",
        target_id=draft_id,
        payload={"review_submission_id": str(submission.submission.id)},
    )
    await session.commit()
    return as_submission_response(submission, submitter=user.user)
