from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings, get_settings
from app.models.user_account import UserAccount


@dataclass(frozen=True)
class SessionClaims:
    user_id: UUID
    token_version: int
    csrf_token: str
    expires_at: datetime


@lru_cache
def get_password_hasher(
    memory_cost: int,
    time_cost: int,
    parallelism: int,
) -> PasswordHasher:
    return PasswordHasher(
        time_cost=time_cost,
        memory_cost=memory_cost,
        parallelism=parallelism,
        hash_len=32,
        salt_len=16,
    )


def password_hasher(settings: Settings | None = None) -> PasswordHasher:
    active_settings = settings or get_settings()
    return get_password_hasher(
        active_settings.argon2_memory_cost_kib,
        active_settings.argon2_time_cost,
        active_settings.argon2_parallelism,
    )


def hash_password(password: str, settings: Settings | None = None) -> str:
    return password_hasher(settings).hash(password)


def verify_password(password: str, password_hash: str, settings: Settings | None = None) -> bool:
    try:
        return password_hasher(settings).verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def password_needs_rehash(password_hash: str, settings: Settings | None = None) -> bool:
    try:
        return password_hasher(settings).check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def new_temporary_password() -> str:
    # 32 URL-safe random characters exceed the application's password minimum.
    return secrets.token_urlsafe(24)


def create_session_token(
    user: UserAccount,
    csrf_token: str,
    settings: Settings | None = None,
) -> str:
    active_settings = settings or get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=active_settings.jwt_expire_hours)
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "role": user.role.value,
        "ver": user.token_version,
        "csrf": csrf_token,
        "iat": now,
        "exp": expires_at,
    }
    return jwt.encode(payload, active_settings.signing_key, algorithm=active_settings.jwt_algorithm)


def decode_session_token(token: str, settings: Settings | None = None) -> SessionClaims:
    active_settings = settings or get_settings()
    payload = jwt.decode(
        token,
        active_settings.signing_key,
        algorithms=[active_settings.jwt_algorithm],
        options={"require": ["sub", "ver", "csrf", "exp", "iat"]},
    )
    try:
        expires_at = datetime.fromtimestamp(int(payload["exp"]), UTC)
        return SessionClaims(
            user_id=UUID(str(payload["sub"])),
            token_version=int(payload["ver"]),
            csrf_token=str(payload["csrf"]),
            expires_at=expires_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise jwt.InvalidTokenError("invalid session claims") from exc


def set_session_cookies(
    response: Any,
    user: UserAccount,
    settings: Settings | None = None,
) -> None:
    active_settings = settings or get_settings()
    csrf_token = new_csrf_token()
    session_token = create_session_token(user, csrf_token, active_settings)
    max_age = active_settings.jwt_expire_hours * 60 * 60
    common = {
        "max_age": max_age,
        "secure": active_settings.cookie_secure,
        "samesite": active_settings.cookie_samesite,
        "path": "/",
    }
    response.set_cookie(
        key=active_settings.session_cookie_name,
        value=session_token,
        httponly=True,
        **common,
    )
    response.set_cookie(
        key=active_settings.csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        **common,
    )


def set_pre_auth_csrf_cookie(response: Any, settings: Settings | None = None) -> None:
    """Issue a double-submit CSRF token for the unauthenticated login request."""

    active_settings = settings or get_settings()
    response.set_cookie(
        key=active_settings.pre_auth_csrf_cookie_name,
        value=new_csrf_token(),
        max_age=10 * 60,
        secure=active_settings.cookie_secure,
        httponly=False,
        samesite=active_settings.cookie_samesite,
        path="/",
    )


def clear_session_cookies(response: Any, settings: Settings | None = None) -> None:
    active_settings = settings or get_settings()
    common = {
        "secure": active_settings.cookie_secure,
        "samesite": active_settings.cookie_samesite,
        "path": "/",
    }
    response.delete_cookie(active_settings.session_cookie_name, httponly=True, **common)
    response.delete_cookie(active_settings.csrf_cookie_name, httponly=False, **common)
    response.delete_cookie(active_settings.pre_auth_csrf_cookie_name, httponly=False, **common)


def csrf_tokens_match(*tokens: str | None) -> bool:
    if not tokens or any(token is None for token in tokens):
        return False
    first = tokens[0]
    return all(secrets.compare_digest(first, token) for token in tokens[1:])
