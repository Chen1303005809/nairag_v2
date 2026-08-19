from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedSession,
    get_app_settings,
    get_current_session,
    require_csrf,
    require_pre_auth_csrf,
)
from app.core.config import Settings
from app.core.input_validation import validate_password
from app.core.security import (
    clear_session_cookies,
    hash_password,
    password_needs_rehash,
    set_pre_auth_csrf_cookie,
    set_session_cookies,
    verify_password,
)
from app.db.session import get_db_session
from app.schemas.auth import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginRequest,
    LoginResponse,
    UserResponse,
)
from app.services.users import find_user_by_username, mark_login, record_audit_event

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/csrf", status_code=status.HTTP_204_NO_CONTENT)
async def issue_pre_auth_csrf_token(
    response: Response,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    set_pre_auth_csrf_cookie(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    _csrf: Annotated[None, Depends(require_pre_auth_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> LoginResponse:
    user = await find_user_by_username(session, body.username)
    password_is_valid = user is not None and verify_password(
        body.password, user.password_hash, settings
    )
    if user is None or not user.is_active or not password_is_valid:
        record_audit_event(
            session,
            event_type="auth.login_failed",
            actor_user_id=None,
            target_type="user_account" if user else None,
            target_id=user.id if user else None,
            payload={"username": body.username},
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    if password_needs_rehash(user.password_hash, settings):
        user.password_hash = hash_password(body.password, settings)
    mark_login(user)
    record_audit_event(
        session,
        event_type="auth.login_succeeded",
        actor_user_id=user.id,
        target_type="user_account",
        target_id=user.id,
    )
    await session.commit()
    set_session_cookies(response, user, settings)
    return LoginResponse(user=UserResponse.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    authenticated: Annotated[AuthenticatedSession, Depends(get_current_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    record_audit_event(
        session,
        event_type="auth.logout",
        actor_user_id=authenticated.user.id,
        target_type="user_account",
        target_id=authenticated.user.id,
    )
    await session.commit()
    clear_session_cookies(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserResponse)
async def read_current_user(
    authenticated: Annotated[AuthenticatedSession, Depends(get_current_session)],
) -> UserResponse:
    return UserResponse.model_validate(authenticated.user)


@router.post("/change-password", response_model=ChangePasswordResponse)
async def change_password(
    body: ChangePasswordRequest,
    response: Response,
    authenticated: Annotated[AuthenticatedSession, Depends(get_current_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ChangePasswordResponse:
    user = authenticated.user
    if not verify_password(body.current_password, user.password_hash, settings):
        record_audit_event(
            session,
            event_type="auth.password_change_failed",
            actor_user_id=user.id,
            target_type="user_account",
            target_id=user.id,
        )
        await session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前密码错误")
    new_password = validate_password(body.new_password, settings)
    if verify_password(new_password, user.password_hash, settings):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="新密码不能与当前密码相同",
        )

    user.password_hash = hash_password(new_password, settings)
    user.must_change_password = False
    user.token_version += 1
    record_audit_event(
        session,
        event_type="auth.password_changed",
        actor_user_id=user.id,
        target_type="user_account",
        target_id=user.id,
    )
    await session.commit()
    set_session_cookies(response, user, settings)
    return ChangePasswordResponse(user=UserResponse.model_validate(user))
