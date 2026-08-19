from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # Register model metadata.
from app.core.config import Settings
from app.db.base import Base
from app.main import create_app


async def build_test_app(tmp_path: Path) -> tuple[object, AsyncEngine]:
    initial_password_file = tmp_path / "initial-password.txt"
    initial_password_file.write_text("InitialPassword-123!", encoding="utf-8")
    database_path = tmp_path / "test.sqlite3"
    settings = Settings(
        app_environment="test",
        database_url=f"sqlite+aiosqlite:///{database_path}",
        jwt_secret="test-signing-key-that-is-long-enough",
        cookie_secure=False,
        initial_admin_username="bootstrap-admin",
        initial_admin_password_file=initial_password_file,
    )
    engine = create_async_engine(settings.database_url_with_password)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return create_app(settings=settings, db_session_factory=factory), engine


def csrf_headers(client: AsyncClient, settings: Settings) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[settings.csrf_cookie_name]}


def pre_auth_csrf_headers(client: AsyncClient, settings: Settings) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[settings.pre_auth_csrf_cookie_name]}


async def login(
    client: AsyncClient,
    settings: Settings,
    *,
    username: str,
    password: str,
) -> object:
    csrf = await client.get("/api/v1/auth/csrf")
    assert csrf.status_code == 204
    return await client.post(
        "/api/v1/auth/login",
        headers=pre_auth_csrf_headers(client, settings),
        json={"username": username, "password": password},
    )


@pytest.mark.asyncio
async def test_initial_admin_must_change_password_and_csrf_is_required(tmp_path: Path) -> None:
    app, engine = await build_test_app(tmp_path)
    settings: Settings = app.state.settings
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="https://testserver") as client:
                login_without_csrf = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "bootstrap-admin", "password": "InitialPassword-123!"},
                )
                assert login_without_csrf.status_code == 403

                login_response = await login(
                    client,
                    settings,
                    username="bootstrap-admin",
                    password="InitialPassword-123!",
                )
                assert login_response.status_code == 200
                assert login_response.json()["user"]["must_change_password"] is True

                blocked = await client.get("/api/v1/users")
                assert blocked.status_code == 403

                no_csrf = await client.post(
                    "/api/v1/auth/change-password",
                    json={
                        "current_password": "InitialPassword-123!",
                        "new_password": "ChangedPassword-123!",
                    },
                )
                assert no_csrf.status_code == 403

                changed = await client.post(
                    "/api/v1/auth/change-password",
                    headers=csrf_headers(client, settings),
                    json={
                        "current_password": "InitialPassword-123!",
                        "new_password": "ChangedPassword-123!",
                    },
                )
                assert changed.status_code == 200
                assert changed.json()["user"]["must_change_password"] is False

                users = await client.get("/api/v1/users")
                assert users.status_code == 200
                assert [user["username"] for user in users.json()] == ["bootstrap-admin"]

                cannot_disable_last_admin = await client.patch(
                    f"/api/v1/users/{users.json()[0]['id']}",
                    headers=csrf_headers(client, settings),
                    json={"is_active": False},
                )
                assert cannot_disable_last_admin.status_code == 409
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_account_management_invalidates_stale_sessions(tmp_path: Path) -> None:
    app, engine = await build_test_app(tmp_path)
    settings: Settings = app.state.settings
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as admin_client:
                admin_login = await login(
                    admin_client,
                    settings,
                    username="bootstrap-admin",
                    password="InitialPassword-123!",
                )
                assert admin_login.status_code == 200
                await admin_client.post(
                    "/api/v1/auth/change-password",
                    headers=csrf_headers(admin_client, settings),
                    json={
                        "current_password": "InitialPassword-123!",
                        "new_password": "ChangedPassword-123!",
                    },
                )

                created = await admin_client.post(
                    "/api/v1/users",
                    headers=csrf_headers(admin_client, settings),
                    json={
                        "username": "contributor",
                        "display_name": "Contributor",
                        "role": "normal_user",
                    },
                )
                assert created.status_code == 201
                created_payload = created.json()
                contributor_id = created_payload["user"]["id"]
                temporary_password = created_payload["temporary_password"]

                duplicate = await admin_client.post(
                    "/api/v1/users",
                    headers=csrf_headers(admin_client, settings),
                    json={
                        "username": "Contributor",
                        "display_name": "Duplicate",
                        "role": "normal_user",
                    },
                )
                assert duplicate.status_code == 409

                async with AsyncClient(
                    transport=transport, base_url="https://testserver"
                ) as contributor_client:
                    contributor_login = await login(
                        contributor_client,
                        settings,
                        username="contributor",
                        password=temporary_password,
                    )
                    assert contributor_login.status_code == 200
                    await contributor_client.post(
                        "/api/v1/auth/change-password",
                        headers=csrf_headers(contributor_client, settings),
                        json={
                            "current_password": temporary_password,
                            "new_password": "ContributorPassword-123!",
                        },
                    )

                    promoted = await admin_client.patch(
                        f"/api/v1/users/{contributor_id}",
                        headers=csrf_headers(admin_client, settings),
                        json={"role": "review_admin"},
                    )
                    assert promoted.status_code == 200
                    assert promoted.json()["role"] == "review_admin"

                    stale_after_role_change = await contributor_client.get("/api/v1/auth/me")
                    assert stale_after_role_change.status_code == 401

                    new_login = await login(
                        contributor_client,
                        settings,
                        username="contributor",
                        password="ContributorPassword-123!",
                    )
                    assert new_login.status_code == 200

                    reset = await admin_client.post(
                        f"/api/v1/users/{contributor_id}/reset-password",
                        headers=csrf_headers(admin_client, settings),
                    )
                    assert reset.status_code == 200
                    reset_password = reset.json()["temporary_password"]
                    assert reset.json()["user"]["must_change_password"] is True

                    stale_after_reset = await contributor_client.get("/api/v1/auth/me")
                    assert stale_after_reset.status_code == 401

                    reset_login = await login(
                        contributor_client,
                        settings,
                        username="contributor",
                        password=reset_password,
                    )
                    assert reset_login.status_code == 200
                    assert reset_login.json()["user"]["must_change_password"] is True
                    changed_after_reset = await contributor_client.post(
                        "/api/v1/auth/change-password",
                        headers=csrf_headers(contributor_client, settings),
                        json={
                            "current_password": reset_password,
                            "new_password": "AfterResetPassword-123!",
                        },
                    )
                    assert changed_after_reset.status_code == 200

                    disabled = await admin_client.patch(
                        f"/api/v1/users/{contributor_id}",
                        headers=csrf_headers(admin_client, settings),
                        json={"is_active": False},
                    )
                    assert disabled.status_code == 200
                    assert disabled.json()["is_active"] is False

                    stale_after_disable = await contributor_client.get("/api/v1/auth/me")
                    assert stale_after_disable.status_code == 401
    finally:
        await engine.dispose()
