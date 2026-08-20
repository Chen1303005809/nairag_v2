from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, read_secret_file
from app.core.input_validation import normalize_display_name, normalize_username, validate_password
from app.core.security import hash_password
from app.models.audit_event import AuditEvent
from app.models.user_account import UserAccount, UserRole


class UsernameAlreadyExistsError(Exception):
    pass


async def find_user_by_username(session: AsyncSession, username: str) -> UserAccount | None:
    return await session.scalar(select(UserAccount).where(UserAccount.username == username))


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    display_name: str,
    password: str,
    role: UserRole,
    created_by_user_id: UUID | None,
    must_change_password: bool = True,
    settings: Settings,
) -> UserAccount:
    normalized_username = normalize_username(username)
    if await find_user_by_username(session, normalized_username):
        raise UsernameAlreadyExistsError(normalized_username)

    user = UserAccount(
        username=normalized_username,
        display_name=normalize_display_name(display_name),
        password_hash=hash_password(validate_password(password, settings), settings),
        role=role,
        must_change_password=must_change_password,
        created_by_user_id=created_by_user_id,
    )
    session.add(user)
    await session.flush()
    return user


def record_audit_event(
    session: AsyncSession,
    *,
    event_type: str,
    actor_user_id: UUID | None,
    target_type: str | None = None,
    target_id: UUID | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditEvent(
            event_type=event_type,
            actor_user_id=actor_user_id,
            target_type=target_type,
            target_id=target_id,
            payload=payload or {},
        )
    )


async def count_active_system_administrators(session: AsyncSession) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(UserAccount)
        .where(
            UserAccount.role == UserRole.SYSTEM_ADMIN,
            UserAccount.is_active.is_(True),
        )
    )
    return int(count or 0)


async def bootstrap_initial_admin(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Create the one initial system administrator only when no accounts exist."""

    async with session_factory() as session:
        async with session.begin():
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                # Multiple API replicas cannot both pass the empty-table check.
                await session.execute(text("SELECT pg_advisory_xact_lock(81723147)"))

            account_count = await session.scalar(select(func.count()).select_from(UserAccount))
            if account_count:
                return

            password_file = settings.initial_admin_password_file
            if password_file is None:
                raise RuntimeError(
                    "INITIAL_ADMIN_PASSWORD_FILE is required when bootstrapping the first account"
                )

            password = read_secret_file(Path(password_file), "INITIAL_ADMIN_PASSWORD_FILE")
            initial_admin = await create_user(
                session,
                username=settings.initial_admin_username,
                display_name="Initial System Administrator",
                password=password,
                role=UserRole.SYSTEM_ADMIN,
                created_by_user_id=None,
                must_change_password=True,
                settings=settings,
            )
            record_audit_event(
                session,
                event_type="account.initial_admin_bootstrapped",
                actor_user_id=initial_admin.id,
                target_type="user_account",
                target_id=initial_admin.id,
                payload={"username": initial_admin.username},
            )


def mark_login(user: UserAccount) -> None:
    user.last_login_at = datetime.now(UTC)
