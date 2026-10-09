from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # Register model metadata.
from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import Base
from app.main import create_app
from app.models.audit_event import AuditEvent
from app.models.knowledge_content import Child, ChildRevision, Parent
from app.models.user_account import UserAccount, UserRole
from app.services.taxonomy import DEFAULT_TAXONOMY_OPTIONS


def make_settings(tmp_path: Path) -> Settings:
    initial_password_file = tmp_path / "initial-password.txt"
    initial_password_file.write_text("InitialPassword-123!", encoding="utf-8")
    return Settings(
        app_environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'taxonomy.sqlite3'}",
        jwt_secret="test-signing-key-that-is-long-enough",
        cookie_secure=False,
        index_artifact_dir=tmp_path / "index-artifacts",
        attachment_storage_dir=tmp_path / "attachments",
        initial_admin_username="taxonomy-admin",
        initial_admin_password_file=initial_password_file,
    )


def csrf_headers(client: AsyncClient, settings: Settings) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[settings.csrf_cookie_name]}


def pre_auth_csrf_headers(client: AsyncClient, settings: Settings) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[settings.pre_auth_csrf_cookie_name]}


async def login(
    client: AsyncClient,
    settings: Settings,
    username: str,
    password: str,
) -> None:
    assert (await client.get("/api/v1/auth/csrf")).status_code == 204
    response = await client.post(
        "/api/v1/auth/login",
        headers=pre_auth_csrf_headers(client, settings),
        json={"username": username, "password": password},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_taxonomy_admin_can_add_rename_and_preserve_historical_search_values(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    engine = create_async_engine(settings.database_url_with_password)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(settings=settings, db_session_factory=factory)

    try:
        async with app.router.lifespan_context(app):
            async with factory() as session:
                admin = await session.scalar(
                    select(UserAccount).where(UserAccount.username == "taxonomy-admin")
                )
                assert admin is not None
                reviewer = UserAccount(
                    username="taxonomy-reviewer",
                    display_name="审查管理员",
                    password_hash=hash_password("InitialPassword-123!", settings),
                    role=UserRole.REVIEW_ADMIN,
                    is_active=True,
                    must_change_password=False,
                    token_version=0,
                )
                session.add(reviewer)
                parent = Parent(created_by_user_id=admin.id)
                session.add(parent)
                await session.flush()
                child = Child(
                    parent_id=parent.id,
                    is_primary=False,
                    created_by_user_id=admin.id,
                )
                session.add(child)
                await session.flush()
                session.add(
                    ChildRevision(
                        child_id=child.id,
                        revision_number=1,
                        question="历史分类测试",
                        response_content="历史分类仍可筛选。",
                        question_type="功能故障类",
                        created_by_user_id=admin.id,
                    )
                )
                await session.commit()

            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as admin_client:
                await login(
                    admin_client,
                    settings,
                    "taxonomy-admin",
                    "InitialPassword-123!",
                )
                changed = await admin_client.post(
                    "/api/v1/auth/change-password",
                    headers=csrf_headers(admin_client, settings),
                    json={
                        "current_password": "InitialPassword-123!",
                        "new_password": "ChangedPassword-123!",
                    },
                )
                assert changed.status_code == 200

                initial = await admin_client.get("/api/v1/knowledge-content/taxonomy")
                assert initial.status_code == 200
                assert initial.json()["question_types"] == list(
                    DEFAULT_TAXONOMY_OPTIONS["question_types"]
                )

                no_csrf = await admin_client.post(
                    "/api/v1/knowledge-content/taxonomy/question_types",
                    json={"value": "新问题类型"},
                )
                assert no_csrf.status_code == 403

                created = await admin_client.post(
                    "/api/v1/knowledge-content/taxonomy/question_types",
                    headers=csrf_headers(admin_client, settings),
                    json={"value": "  新问题类型  "},
                )
                assert created.status_code == 201
                assert "新问题类型" in created.json()["question_types"]

                duplicate = await admin_client.post(
                    "/api/v1/knowledge-content/taxonomy/question_types",
                    headers=csrf_headers(admin_client, settings),
                    json={"value": "新问题类型"},
                )
                assert duplicate.status_code == 409
                assert (
                    await admin_client.post(
                        "/api/v1/knowledge-content/taxonomy/question_types",
                        headers=csrf_headers(admin_client, settings),
                        json={"value": "   "},
                    )
                ).status_code == 422
                assert (
                    await admin_client.post(
                        "/api/v1/knowledge-content/taxonomy/question_types",
                        headers=csrf_headers(admin_client, settings),
                        json={"value": "x" * 256},
                    )
                ).status_code == 422

                renamed = await admin_client.patch(
                    "/api/v1/knowledge-content/taxonomy/question_types",
                    headers=csrf_headers(admin_client, settings),
                    json={"old_value": "功能故障类", "value": "功能异常类"},
                )
                assert renamed.status_code == 200
                renamed_taxonomy = renamed.json()
                assert "功能异常类" in renamed_taxonomy["question_types"]
                assert "功能故障类" not in renamed_taxonomy["question_types"]
                assert "功能故障类" in renamed_taxonomy["search_filters"]["question_types"]

                persisted = await admin_client.get("/api/v1/knowledge-content/taxonomy")
                assert "功能异常类" in persisted.json()["question_types"]
                assert "功能故障类" in persisted.json()["search_filters"]["question_types"]

            async with AsyncClient(
                transport=transport, base_url="https://testserver"
            ) as reviewer_client:
                await login(
                    reviewer_client,
                    settings,
                    "taxonomy-reviewer",
                    "InitialPassword-123!",
                )
                forbidden = await reviewer_client.post(
                    "/api/v1/knowledge-content/taxonomy/question_types",
                    headers=csrf_headers(reviewer_client, settings),
                    json={"value": "不应写入"},
                )
                assert forbidden.status_code == 403

            async with factory() as session:
                audit_types = list(
                    await session.scalars(
                        select(AuditEvent.event_type).where(
                            AuditEvent.event_type.in_(
                                {
                                    "knowledge_taxonomy.option_added",
                                    "knowledge_taxonomy.option_renamed",
                                }
                            )
                        )
                    )
                )
                assert audit_types.count("knowledge_taxonomy.option_added") == 1
                assert audit_types.count("knowledge_taxonomy.option_renamed") == 1
    finally:
        await engine.dispose()
