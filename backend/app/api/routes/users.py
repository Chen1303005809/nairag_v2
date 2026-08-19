from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedSession,
    get_app_settings,
    require_csrf,
    require_system_administrator,
)
from app.core.config import Settings
from app.core.security import hash_password, new_temporary_password
from app.db.session import get_db_session
from app.models.user_account import UserAccount, UserRole
from app.schemas.auth import UserResponse
from app.schemas.users import CreateUserRequest, TemporaryPasswordResponse, UpdateUserRequest
from app.services.knowledge_bases import count_reviewer_assignments_for_user
from app.services.users import (
    UsernameAlreadyExistsError,
    count_active_system_administrators,
    create_user,
    record_audit_event,
)

router = APIRouter(prefix="/users", tags=["user accounts"])


async def get_target_user(session: AsyncSession, user_id: UUID) -> UserAccount:
    user = await session.get(UserAccount, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    return user


async def assert_not_removing_last_active_system_administrator(
    session: AsyncSession,
    *,
    user: UserAccount,
    desired_role: UserRole,
    desired_is_active: bool,
) -> None:
    removes_active_system_admin = (
        user.role == UserRole.SYSTEM_ADMIN
        and user.is_active
        and (desired_role != UserRole.SYSTEM_ADMIN or not desired_is_active)
    )
    if not removes_active_system_admin:
        return

    # Serialize removal of active system administrators. Without these row locks, two
    # concurrent requests could both observe two administrators and leave none enabled.
    await session.execute(
        select(UserAccount.id)
        .where(UserAccount.role == UserRole.SYSTEM_ADMIN, UserAccount.is_active.is_(True))
        .with_for_update()
    )
    if await count_active_system_administrators(session) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="不能移除最后一个启用的系统管理员",
        )


@router.get("", response_model=list[UserResponse])
async def list_users(
    _actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    include_disabled: bool = True,
) -> list[UserResponse]:
    statement = select(UserAccount).order_by(UserAccount.created_at, UserAccount.username)
    if not include_disabled:
        statement = statement.where(UserAccount.is_active.is_(True))
    users = (await session.scalars(statement)).all()
    return [UserResponse.model_validate(user) for user in users]


@router.post("", response_model=TemporaryPasswordResponse, status_code=status.HTTP_201_CREATED)
async def create_managed_user(
    body: CreateUserRequest,
    actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TemporaryPasswordResponse:
    temporary_password = new_temporary_password()
    try:
        user = await create_user(
            session,
            username=body.username,
            display_name=body.display_name,
            password=temporary_password,
            role=body.role,
            created_by_user_id=actor.user.id,
            must_change_password=True,
            settings=settings,
        )
    except UsernameAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在") from exc

    record_audit_event(
        session,
        event_type="account.created",
        actor_user_id=actor.user.id,
        target_type="user_account",
        target_id=user.id,
        payload={"username": user.username, "role": user.role.value},
    )
    await session.commit()
    return TemporaryPasswordResponse(
        user=UserResponse.model_validate(user), temporary_password=temporary_password
    )


@router.patch("/{user_id}", response_model=UserResponse)
async def update_managed_user(
    user_id: UUID,
    body: UpdateUserRequest,
    actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserResponse:
    user = await get_target_user(session, user_id)
    desired_role = body.role if body.role is not None else user.role
    desired_is_active = body.is_active if body.is_active is not None else user.is_active
    if user.role == UserRole.REVIEW_ADMIN and desired_role != UserRole.REVIEW_ADMIN:
        assignment_count = await count_reviewer_assignments_for_user(session, user.id)
        if assignment_count:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该审查管理员仍有知识库授权；请先解除授权再变更角色",
            )
    await assert_not_removing_last_active_system_administrator(
        session,
        user=user,
        desired_role=desired_role,
        desired_is_active=desired_is_active,
    )

    changed_fields: dict[str, object] = {}
    if body.display_name is not None and body.display_name != user.display_name:
        user.display_name = body.display_name
        changed_fields["display_name"] = body.display_name
    if desired_role != user.role:
        user.role = desired_role
        user.token_version += 1
        changed_fields["role"] = desired_role.value
    if desired_is_active != user.is_active:
        user.is_active = desired_is_active
        user.token_version += 1
        changed_fields["is_active"] = desired_is_active

    if changed_fields:
        record_audit_event(
            session,
            event_type="account.updated",
            actor_user_id=actor.user.id,
            target_type="user_account",
            target_id=user.id,
            payload=changed_fields,
        )
        await session.commit()
    return UserResponse.model_validate(user)


@router.post("/{user_id}/reset-password", response_model=TemporaryPasswordResponse)
async def reset_user_password(
    user_id: UUID,
    actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TemporaryPasswordResponse:
    user = await get_target_user(session, user_id)
    temporary_password = new_temporary_password()
    user.password_hash = hash_password(temporary_password, settings)
    user.must_change_password = True
    user.token_version += 1
    record_audit_event(
        session,
        event_type="account.password_reset",
        actor_user_id=actor.user.id,
        target_type="user_account",
        target_id=user.id,
    )
    await session.commit()
    return TemporaryPasswordResponse(
        user=UserResponse.model_validate(user), temporary_password=temporary_password
    )
