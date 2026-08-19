from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import SessionClaims, csrf_tokens_match, decode_session_token
from app.db.session import get_db_session
from app.models.user_account import UserAccount, UserRole


@dataclass(frozen=True)
class AuthenticatedSession:
    user: UserAccount
    claims: SessionClaims


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_current_session(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AuthenticatedSession:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")

    try:
        claims = decode_session_token(token, settings)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效") from exc

    user = await session.get(UserAccount, claims.user_id)
    if user is None or not user.is_active or user.token_version != claims.token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    return AuthenticatedSession(user=user, claims=claims)


async def require_fully_authenticated_session(
    authenticated: Annotated[AuthenticatedSession, Depends(get_current_session)],
) -> AuthenticatedSession:
    if authenticated.user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="必须先修改临时密码",
        )
    return authenticated


async def require_system_administrator(
    authenticated: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
) -> AuthenticatedSession:
    if authenticated.user.role != UserRole.SYSTEM_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要系统管理员权限")
    return authenticated


async def require_review_administrator(
    authenticated: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
) -> AuthenticatedSession:
    if authenticated.user.role != UserRole.REVIEW_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要审查管理员权限")
    return authenticated


async def require_csrf(
    request: Request,
    authenticated: Annotated[AuthenticatedSession, Depends(get_current_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    header_token = request.headers.get("X-CSRF-Token")
    if not csrf_tokens_match(authenticated.claims.csrf_token, cookie_token, header_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")


async def require_pre_auth_csrf(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> None:
    cookie_token = request.cookies.get(settings.pre_auth_csrf_cookie_name)
    header_token = request.headers.get("X-CSRF-Token")
    if not csrf_tokens_match(cookie_token, header_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")
