from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedSession,
    require_csrf,
    require_fully_authenticated_session,
    require_review_administrator,
    require_system_administrator,
)
from app.db.session import get_db_session
from app.models.knowledge_base import KnowledgeBase
from app.models.user_account import UserAccount, UserRole
from app.schemas.knowledge_bases import (
    CreateKnowledgeBaseRequest,
    KnowledgeBaseResponse,
    ManagedKnowledgeBaseResponse,
    ReviewerAccountResponse,
    ReviewerAssignmentResponse,
    UpdateKnowledgeBaseRequest,
)
from app.services.knowledge_bases import (
    KnowledgeBaseCollectionUnavailableError,
    KnowledgeBaseKeyAlreadyExistsError,
    ReviewerAssignmentWithUser,
    ReviewerNotEligibleError,
    assign_reviewer,
    create_knowledge_base,
    get_knowledge_base,
    list_active_knowledge_bases,
    list_assigned_knowledge_bases,
    list_managed_knowledge_bases,
    list_reviewer_assignments,
    remove_reviewer_assignment,
)
from app.services.users import record_audit_event

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge bases"])


def as_knowledge_base_response(knowledge_base: KnowledgeBase) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse.model_validate(knowledge_base)


def as_managed_knowledge_base_response(
    knowledge_base: KnowledgeBase,
    reviewer_count: int,
) -> ManagedKnowledgeBaseResponse:
    return ManagedKnowledgeBaseResponse(
        **as_knowledge_base_response(knowledge_base).model_dump(),
        current_collection_generation=knowledge_base.current_collection_generation,
        current_physical_collection_name=knowledge_base.current_physical_collection_name,
        reviewer_count=reviewer_count,
    )


def as_reviewer_assignment_response(
    assignment_with_user: ReviewerAssignmentWithUser,
) -> ReviewerAssignmentResponse:
    assignment = assignment_with_user.assignment
    return ReviewerAssignmentResponse(
        knowledge_base_id=assignment.knowledge_base_id,
        reviewer=ReviewerAccountResponse.model_validate(assignment_with_user.reviewer),
        assigned_by_user_id=assignment.assigned_by_user_id,
        assigned_at=assignment.assigned_at,
    )


async def require_knowledge_base(
    session: AsyncSession,
    knowledge_base_id: UUID,
) -> KnowledgeBase:
    knowledge_base = await get_knowledge_base(session, knowledge_base_id)
    if knowledge_base is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    return knowledge_base


@router.get("", response_model=list[KnowledgeBaseResponse])
async def list_available_knowledge_bases(
    _user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[KnowledgeBaseResponse]:
    knowledge_bases = await list_active_knowledge_bases(session)
    return [as_knowledge_base_response(knowledge_base) for knowledge_base in knowledge_bases]


@router.get("/admin", response_model=list[ManagedKnowledgeBaseResponse])
async def list_managed_knowledge_base_records(
    _actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ManagedKnowledgeBaseResponse]:
    results = await list_managed_knowledge_bases(session)
    return [
        as_managed_knowledge_base_response(knowledge_base, reviewer_count)
        for knowledge_base, reviewer_count in results
    ]


@router.get("/assigned-to-me", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases_assigned_to_current_reviewer(
    reviewer: Annotated[AuthenticatedSession, Depends(require_review_administrator)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[KnowledgeBaseResponse]:
    knowledge_bases = await list_assigned_knowledge_bases(session, reviewer.user.id)
    return [as_knowledge_base_response(knowledge_base) for knowledge_base in knowledge_bases]


@router.get("/admin/{knowledge_base_id}/reviewers", response_model=list[ReviewerAssignmentResponse])
async def list_knowledge_base_reviewers(
    knowledge_base_id: UUID,
    _actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ReviewerAssignmentResponse]:
    await require_knowledge_base(session, knowledge_base_id)
    assignments = await list_reviewer_assignments(session, knowledge_base_id)
    return [as_reviewer_assignment_response(assignment) for assignment in assignments]


@router.post("", response_model=ManagedKnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_managed_knowledge_base(
    body: CreateKnowledgeBaseRequest,
    actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    request: Request,
) -> ManagedKnowledgeBaseResponse:
    try:
        knowledge_base = await create_knowledge_base(
            session,
            logical_key=body.logical_key,
            name=body.name,
            description=body.description,
            is_active=body.is_active,
            created_by_user_id=actor.user.id,
            collection_manager=getattr(request.app.state, "milvus_collection_manager", None),
        )
    except KnowledgeBaseKeyAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="知识库逻辑标识已存在",
        ) from exc
    except KnowledgeBaseCollectionUnavailableError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Milvus 集合创建失败，知识库未创建，请稍后重试",
        ) from exc

    record_audit_event(
        session,
        event_type="knowledge_base.created",
        actor_user_id=actor.user.id,
        target_type="knowledge_base",
        target_id=knowledge_base.id,
        payload={
            "logical_key": knowledge_base.logical_key,
            "current_collection_generation": knowledge_base.current_collection_generation,
            "is_active": knowledge_base.is_active,
        },
    )
    await session.commit()
    return as_managed_knowledge_base_response(knowledge_base, reviewer_count=0)


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def read_available_knowledge_base(
    knowledge_base_id: UUID,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowledgeBaseResponse:
    knowledge_base = await require_knowledge_base(session, knowledge_base_id)
    if not knowledge_base.is_active and user.user.role != UserRole.SYSTEM_ADMIN:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    return as_knowledge_base_response(knowledge_base)


@router.patch("/{knowledge_base_id}", response_model=ManagedKnowledgeBaseResponse)
async def update_managed_knowledge_base(
    knowledge_base_id: UUID,
    body: UpdateKnowledgeBaseRequest,
    actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ManagedKnowledgeBaseResponse:
    knowledge_base = await require_knowledge_base(session, knowledge_base_id)
    changed_fields: dict[str, object] = {}
    if "name" in body.model_fields_set and body.name != knowledge_base.name:
        knowledge_base.name = body.name
        changed_fields["name"] = knowledge_base.name
    if "description" in body.model_fields_set and body.description != knowledge_base.description:
        knowledge_base.description = body.description
        changed_fields["description"] = body.description
    if "is_active" in body.model_fields_set and body.is_active != knowledge_base.is_active:
        knowledge_base.is_active = bool(body.is_active)
        changed_fields["is_active"] = knowledge_base.is_active

    if changed_fields:
        record_audit_event(
            session,
            event_type="knowledge_base.updated",
            actor_user_id=actor.user.id,
            target_type="knowledge_base",
            target_id=knowledge_base.id,
            payload=changed_fields,
        )
        await session.commit()

    assignments = await list_reviewer_assignments(session, knowledge_base.id)
    return as_managed_knowledge_base_response(knowledge_base, reviewer_count=len(assignments))


@router.put(
    "/{knowledge_base_id}/reviewers/{reviewer_user_id}",
    response_model=ReviewerAssignmentResponse,
)
async def assign_knowledge_base_reviewer(
    knowledge_base_id: UUID,
    reviewer_user_id: UUID,
    actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReviewerAssignmentResponse:
    await require_knowledge_base(session, knowledge_base_id)
    try:
        assignment, was_created = await assign_reviewer(
            session,
            knowledge_base_id=knowledge_base_id,
            reviewer_user_id=reviewer_user_id,
            assigned_by_user_id=actor.user.id,
        )
    except ReviewerNotEligibleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="只能分配启用状态的审查管理员",
        ) from exc

    reviewer = await session.get(UserAccount, reviewer_user_id)
    assert reviewer is not None
    if was_created:
        record_audit_event(
            session,
            event_type="knowledge_base.reviewer_assigned",
            actor_user_id=actor.user.id,
            target_type="knowledge_base",
            target_id=knowledge_base_id,
            payload={"reviewer_user_id": str(reviewer_user_id)},
        )
        await session.commit()

    return as_reviewer_assignment_response(
        ReviewerAssignmentWithUser(assignment=assignment, reviewer=reviewer)
    )


@router.delete(
    "/{knowledge_base_id}/reviewers/{reviewer_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unassign_knowledge_base_reviewer(
    knowledge_base_id: UUID,
    reviewer_user_id: UUID,
    actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    await require_knowledge_base(session, knowledge_base_id)
    was_removed = await remove_reviewer_assignment(
        session,
        knowledge_base_id=knowledge_base_id,
        reviewer_user_id=reviewer_user_id,
    )
    if not was_removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="审查管理员未被分配到该知识库",
        )
    record_audit_event(
        session,
        event_type="knowledge_base.reviewer_unassigned",
        actor_user_id=actor.user.id,
        target_type="knowledge_base",
        target_id=knowledge_base_id,
        payload={"reviewer_user_id": str(reviewer_user_id)},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
