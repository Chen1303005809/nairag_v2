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


@pytest.mark.asyncio
async def test_knowledge_base_management_and_reviewer_authorization(tmp_path: Path) -> None:
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

                reviewer_created = await admin_client.post(
                    "/api/v1/users",
                    headers=csrf_headers(admin_client, settings),
                    json={
                        "username": "reviewer",
                        "display_name": "Reviewer",
                        "role": "review_admin",
                    },
                )
                assert reviewer_created.status_code == 201
                reviewer_id = reviewer_created.json()["user"]["id"]
                reviewer_password = reviewer_created.json()["temporary_password"]

                normal_created = await admin_client.post(
                    "/api/v1/users",
                    headers=csrf_headers(admin_client, settings),
                    json={
                        "username": "ordinary",
                        "display_name": "Ordinary",
                        "role": "normal_user",
                    },
                )
                assert normal_created.status_code == 201
                normal_user_id = normal_created.json()["user"]["id"]

                knowledge_base_created = await admin_client.post(
                    "/api/v1/knowledge-bases",
                    headers=csrf_headers(admin_client, settings),
                    json={
                        "logical_key": "support-team",
                        "name": "客户支持知识库",
                        "description": "支持团队的知识内容",
                        "is_active": True,
                    },
                )
                assert knowledge_base_created.status_code == 201
                knowledge_base = knowledge_base_created.json()
                knowledge_base_id = knowledge_base["id"]
                assert knowledge_base["current_collection_generation"] == 1
                assert (
                    knowledge_base["current_physical_collection_name"] == "nairag_support_d_team_g1"
                )
                assert knowledge_base["reviewer_count"] == 0

                underscore_key = await admin_client.post(
                    "/api/v1/knowledge-bases",
                    headers=csrf_headers(admin_client, settings),
                    json={"logical_key": "support_team", "name": "下划线知识库"},
                )
                assert underscore_key.status_code == 201
                assert (
                    underscore_key.json()["current_physical_collection_name"]
                    == "nairag_support_u_team_g1"
                )

                duplicate = await admin_client.post(
                    "/api/v1/knowledge-bases",
                    headers=csrf_headers(admin_client, settings),
                    json={"logical_key": "SUPPORT-TEAM", "name": "重复知识库"},
                )
                assert duplicate.status_code == 409

                invalid_name_update = await admin_client.patch(
                    f"/api/v1/knowledge-bases/{knowledge_base_id}",
                    headers=csrf_headers(admin_client, settings),
                    json={"name": None},
                )
                assert invalid_name_update.status_code == 422

                available = await admin_client.get("/api/v1/knowledge-bases")
                assert available.status_code == 200
                available_by_key = {
                    record["logical_key"]: record for record in available.json()
                }
                assert set(available_by_key) == {"support-team", "support_team"}
                assert (
                    "current_physical_collection_name"
                    not in available_by_key["support-team"]
                )

                invalid_assignment = await admin_client.put(
                    f"/api/v1/knowledge-bases/{knowledge_base_id}/reviewers/{normal_user_id}",
                    headers=csrf_headers(admin_client, settings),
                )
                assert invalid_assignment.status_code == 422

                assignment = await admin_client.put(
                    f"/api/v1/knowledge-bases/{knowledge_base_id}/reviewers/{reviewer_id}",
                    headers=csrf_headers(admin_client, settings),
                )
                assert assignment.status_code == 200
                assert assignment.json()["reviewer"]["username"] == "reviewer"

                idempotent_assignment = await admin_client.put(
                    f"/api/v1/knowledge-bases/{knowledge_base_id}/reviewers/{reviewer_id}",
                    headers=csrf_headers(admin_client, settings),
                )
                assert idempotent_assignment.status_code == 200
                assignments = await admin_client.get(
                    f"/api/v1/knowledge-bases/admin/{knowledge_base_id}/reviewers"
                )
                assert assignments.status_code == 200
                assert len(assignments.json()) == 1

                role_change_while_assigned = await admin_client.patch(
                    f"/api/v1/users/{reviewer_id}",
                    headers=csrf_headers(admin_client, settings),
                    json={"role": "normal_user"},
                )
                assert role_change_while_assigned.status_code == 409

                async with AsyncClient(
                    transport=transport, base_url="https://testserver"
                ) as reviewer_client:
                    reviewer_login = await login(
                        reviewer_client,
                        settings,
                        username="reviewer",
                        password=reviewer_password,
                    )
                    assert reviewer_login.status_code == 200
                    await reviewer_client.post(
                        "/api/v1/auth/change-password",
                        headers=csrf_headers(reviewer_client, settings),
                        json={
                            "current_password": reviewer_password,
                            "new_password": "ReviewerPassword-123!",
                        },
                    )
                    assigned_to_reviewer = await reviewer_client.get(
                        "/api/v1/knowledge-bases/assigned-to-me"
                    )
                    assert assigned_to_reviewer.status_code == 200
                    assert [record["id"] for record in assigned_to_reviewer.json()] == [
                        knowledge_base_id
                    ]

                    disabled = await admin_client.patch(
                        f"/api/v1/knowledge-bases/{knowledge_base_id}",
                        headers=csrf_headers(admin_client, settings),
                        json={"description": None, "is_active": False},
                    )
                    assert disabled.status_code == 200
                    assert disabled.json()["description"] is None
                    assert disabled.json()["is_active"] is False

                    no_longer_assigned = await reviewer_client.get(
                        "/api/v1/knowledge-bases/assigned-to-me"
                    )
                    assert no_longer_assigned.status_code == 200
                    assert no_longer_assigned.json() == []
                    hidden_when_disabled = await reviewer_client.get(
                        f"/api/v1/knowledge-bases/{knowledge_base_id}"
                    )
                    assert hidden_when_disabled.status_code == 404

                unassigned = await admin_client.delete(
                    f"/api/v1/knowledge-bases/{knowledge_base_id}/reviewers/{reviewer_id}",
                    headers=csrf_headers(admin_client, settings),
                )
                assert unassigned.status_code == 204
                role_change_after_unassignment = await admin_client.patch(
                    f"/api/v1/users/{reviewer_id}",
                    headers=csrf_headers(admin_client, settings),
                    json={"role": "normal_user"},
                )
                assert role_change_after_unassignment.status_code == 200
    finally:
        await engine.dispose()
