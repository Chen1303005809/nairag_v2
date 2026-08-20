from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # Register model metadata.
from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from app.models.knowledge_content import (
    ChildKnowledgeBasePublication,
    ChildPublicationStatus,
    IndexJob,
    IndexJobStatus,
    ReviewSubmission,
    ReviewSubmissionStatus,
    ReviewSubmissionTarget,
    ReviewTargetStatus,
)
from app.services.index_backend import LocalArtifactIndexBackend
from app.services.index_jobs import run_index_worker_once


async def build_test_app(tmp_path: Path) -> tuple[object, AsyncEngine]:
    initial_password_file = tmp_path / "initial-password.txt"
    initial_password_file.write_text("InitialPassword-123!", encoding="utf-8")
    database_path = tmp_path / "test.sqlite3"
    settings = Settings(
        app_environment="test",
        database_url=f"sqlite+aiosqlite:///{database_path}",
        jwt_secret="test-signing-key-that-is-long-enough",
        cookie_secure=False,
        index_artifact_dir=tmp_path / "index-artifacts",
        attachment_storage_dir=tmp_path / "attachments",
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


async def publish_parent_submission_for_test(app: object, submission_id: str) -> None:
    session_factory = app.state.session_factory  # type: ignore[attr-defined]
    async with session_factory() as session:
        submission = await session.get(ReviewSubmission, UUID(submission_id))
        assert submission is not None
        submission.status = ReviewSubmissionStatus.PUBLISHED
        targets = list(
            (
                await session.scalars(
                    select(ReviewSubmissionTarget).where(
                        ReviewSubmissionTarget.review_submission_id == submission.id
                    )
                )
            ).all()
        )
        publications = list(
            (
                await session.scalars(
                    select(ChildKnowledgeBasePublication).where(
                        ChildKnowledgeBasePublication.child_id == submission.child_id
                    )
                )
            ).all()
        )
        assert len(targets) == len(publications)
        for target in targets:
            target.status = ReviewTargetStatus.PUBLISHED
        for publication in publications:
            publication.status = ChildPublicationStatus.PUBLISHED
            publication.active_revision_id = submission.child_revision_id
            publication.pending_submission_id = None
        await session.commit()


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

                reset_system_admin = await client.post(
                    f"/api/v1/users/{users.json()[0]['id']}/reset-password",
                    headers=csrf_headers(client, settings),
                )
                assert reset_system_admin.status_code == 400
                assert "自定义新密码" in reset_system_admin.json()["detail"]

                cannot_disable_last_admin = await client.patch(
                    f"/api/v1/users/{users.json()[0]['id']}",
                    headers=csrf_headers(client, settings),
                    json={"is_active": False},
                )
                assert cannot_disable_last_admin.status_code == 409
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_content_submissions_preserve_parent_and_target_invariants(tmp_path: Path) -> None:
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
                password_change = await admin_client.post(
                    "/api/v1/auth/change-password",
                    headers=csrf_headers(admin_client, settings),
                    json={
                        "current_password": "InitialPassword-123!",
                        "new_password": "ChangedPassword-123!",
                    },
                )
                assert password_change.status_code == 200

                primary_knowledge_base = await admin_client.post(
                    "/api/v1/knowledge-bases",
                    headers=csrf_headers(admin_client, settings),
                    json={"logical_key": "product-help", "name": "产品帮助"},
                )
                assert primary_knowledge_base.status_code == 201
                primary_knowledge_base_id = primary_knowledge_base.json()["id"]

                other_knowledge_base = await admin_client.post(
                    "/api/v1/knowledge-bases",
                    headers=csrf_headers(admin_client, settings),
                    json={"logical_key": "sales-help", "name": "销售帮助"},
                )
                assert other_knowledge_base.status_code == 201
                other_knowledge_base_id = other_knowledge_base.json()["id"]

                created_author = await admin_client.post(
                    "/api/v1/users",
                    headers=csrf_headers(admin_client, settings),
                    json={
                        "username": "author",
                        "display_name": "Author",
                        "role": "normal_user",
                    },
                )
                assert created_author.status_code == 201
                author_password = created_author.json()["temporary_password"]

                async with AsyncClient(
                    transport=transport, base_url="https://testserver"
                ) as author_client:
                    author_login = await login(
                        author_client,
                        settings,
                        username="author",
                        password=author_password,
                    )
                    assert author_login.status_code == 200
                    changed = await author_client.post(
                        "/api/v1/auth/change-password",
                        headers=csrf_headers(author_client, settings),
                        json={
                            "current_password": author_password,
                            "new_password": "AuthorPassword-123!",
                        },
                    )
                    assert changed.status_code == 200

                    parent_submission = await author_client.post(
                        "/api/v1/knowledge-content/parent-submissions",
                        headers=csrf_headers(author_client, settings),
                        json={
                            "parent": {
                                "name": "账号登录",
                                "canonical_keyword": "登录",
                                "lexical_rules": [
                                    {"rule_type": "alias", "rule_value": "登陆"},
                                    {"rule_type": "regex", "rule_value": "^登录.+"},
                                ],
                            },
                            "primary_child": {
                                "question": "无法登录怎么办？",
                                "response_content": "请先确认账号与密码是否正确。",
                                "question_variants": ["登录不上怎么办？"],
                            },
                            "knowledge_base_ids": [primary_knowledge_base_id],
                        },
                    )
                    assert parent_submission.status_code == 201
                    parent_payload = parent_submission.json()
                    assert parent_payload["submission_kind"] == "parent_with_primary"
                    assert parent_payload["status"] == "pending_review"
                    assert parent_payload["targets"][0]["id"] == primary_knowledge_base_id
                    parent_id = parent_payload["parent_id"]

                    unavailable_parents = await author_client.get(
                        "/api/v1/knowledge-content/parents/available"
                    )
                    assert unavailable_parents.status_code == 200
                    assert unavailable_parents.json() == []

                    ordinary_before_parent_publication = await author_client.post(
                        "/api/v1/knowledge-content/child-submissions",
                        headers=csrf_headers(author_client, settings),
                        json={
                            "parent_id": parent_id,
                            "child": {
                                "question": "如何找回密码？",
                                "response_content": "请联系系统管理员重置密码。",
                            },
                            "knowledge_base_ids": [primary_knowledge_base_id],
                        },
                    )
                    assert ordinary_before_parent_publication.status_code == 409

                    my_submissions = await author_client.get(
                        "/api/v1/knowledge-content/submissions/mine"
                    )
                    assert my_submissions.status_code == 200
                    assert [
                        submission["title"] for submission in my_submissions.json()
                    ] == ["账号登录"]

                    await publish_parent_submission_for_test(app, parent_payload["id"])

                    available_parents = await author_client.get(
                        "/api/v1/knowledge-content/parents/available"
                    )
                    assert available_parents.status_code == 200
                    assert available_parents.json()[0]["id"] == parent_id
                    assert available_parents.json()[0]["available_knowledge_bases"] == [
                        {
                            "id": primary_knowledge_base_id,
                            "logical_key": "product-help",
                            "name": "产品帮助",
                        }
                    ]

                    invalid_target = await author_client.post(
                        "/api/v1/knowledge-content/child-submissions",
                        headers=csrf_headers(author_client, settings),
                        json={
                            "parent_id": parent_id,
                            "child": {
                                "question": "销售问题？",
                                "response_content": "这是不允许的目标库。",
                            },
                            "knowledge_base_ids": [other_knowledge_base_id],
                        },
                    )
                    assert invalid_target.status_code == 422

                    ordinary_submission = await author_client.post(
                        "/api/v1/knowledge-content/child-submissions",
                        headers=csrf_headers(author_client, settings),
                        json={
                            "parent_id": parent_id,
                            "child": {
                                "question": "如何找回密码？",
                                "response_content": "请联系系统管理员重置密码。",
                                "question_type": "账号管理",
                            },
                            "knowledge_base_ids": [primary_knowledge_base_id],
                        },
                    )
                    assert ordinary_submission.status_code == 201
                    ordinary_child_id = ordinary_submission.json()["child_id"]

                    duplicate_child_candidate = await author_client.post(
                        f"/api/v1/knowledge-content/children/{ordinary_child_id}/revisions",
                        headers=csrf_headers(author_client, settings),
                        json={
                            "child": {
                                "question": "如何找回密码？",
                                "response_content": "新的候选内容。",
                            },
                            "knowledge_base_ids": [primary_knowledge_base_id],
                        },
                    )
                    assert duplicate_child_candidate.status_code == 409

                    parent_revision = await author_client.post(
                        f"/api/v1/knowledge-content/parents/{parent_id}/revisions",
                        headers=csrf_headers(author_client, settings),
                        json={
                            "parent": {"name": "账号登录与验证", "canonical_keyword": "登录"},
                            "primary_child": {
                                "question": "无法登录怎么办？",
                                "response_content": "请先确认账号、密码和网络状态。",
                            },
                        },
                    )
                    assert parent_revision.status_code == 201

                    duplicate_parent_candidate = await author_client.post(
                        f"/api/v1/knowledge-content/parents/{parent_id}/revisions",
                        headers=csrf_headers(author_client, settings),
                        json={
                            "parent": {"name": "账号登录与验证", "canonical_keyword": "登录"},
                            "primary_child": {
                                "question": "无法登录怎么办？",
                                "response_content": "新的父类候选内容。",
                            },
                        },
                    )
                    assert duplicate_parent_candidate.status_code == 409
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_review_queue_decisions_and_target_publication_are_isolated(tmp_path: Path) -> None:
    app, engine = await build_test_app(tmp_path)
    settings: Settings = app.state.settings
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="https://testserver") as admin:
                assert (
                    await login(
                        admin,
                        settings,
                        username="bootstrap-admin",
                        password="InitialPassword-123!",
                    )
                ).status_code == 200
                assert (
                    await admin.post(
                        "/api/v1/auth/change-password",
                        headers=csrf_headers(admin, settings),
                        json={
                            "current_password": "InitialPassword-123!",
                            "new_password": "ChangedPassword-123!",
                        },
                    )
                ).status_code == 200

                knowledge_base_ids: list[str] = []
                for logical_key, name in (("product-help", "产品帮助"), ("sales-help", "销售帮助")):
                    created = await admin.post(
                        "/api/v1/knowledge-bases",
                        headers=csrf_headers(admin, settings),
                        json={"logical_key": logical_key, "name": name},
                    )
                    assert created.status_code == 201
                    knowledge_base_ids.append(created.json()["id"])

                reviewer_created = await admin.post(
                    "/api/v1/users",
                    headers=csrf_headers(admin, settings),
                    json={
                        "username": "reviewer",
                        "display_name": "Reviewer",
                        "role": "review_admin",
                    },
                )
                assert reviewer_created.status_code == 201
                reviewer_id = reviewer_created.json()["user"]["id"]
                reviewer_password = reviewer_created.json()["temporary_password"]
                for knowledge_base_id in knowledge_base_ids:
                    assigned = await admin.put(
                        f"/api/v1/knowledge-bases/{knowledge_base_id}/reviewers/{reviewer_id}",
                        headers=csrf_headers(admin, settings),
                    )
                    assert assigned.status_code == 200

                author_created = await admin.post(
                    "/api/v1/users",
                    headers=csrf_headers(admin, settings),
                    json={
                        "username": "author",
                        "display_name": "Author",
                        "role": "normal_user",
                    },
                )
                assert author_created.status_code == 201
                author_password = author_created.json()["temporary_password"]

                async with AsyncClient(
                    transport=transport, base_url="https://testserver"
                ) as reviewer, AsyncClient(
                    transport=transport, base_url="https://testserver"
                ) as author:
                    assert (
                        await login(
                            reviewer,
                            settings,
                            username="reviewer",
                            password=reviewer_password,
                        )
                    ).status_code == 200
                    assert (
                        await reviewer.post(
                            "/api/v1/auth/change-password",
                            headers=csrf_headers(reviewer, settings),
                            json={
                                "current_password": reviewer_password,
                                "new_password": "ReviewerPassword-123!",
                            },
                        )
                    ).status_code == 200
                    assert (
                        await login(
                            author,
                            settings,
                            username="author",
                            password=author_password,
                        )
                    ).status_code == 200
                    assert (
                        await author.post(
                            "/api/v1/auth/change-password",
                            headers=csrf_headers(author, settings),
                            json={
                                "current_password": author_password,
                                "new_password": "AuthorPassword-123!",
                            },
                        )
                    ).status_code == 200

                    submitted = await author.post(
                        "/api/v1/knowledge-content/parent-submissions",
                        headers=csrf_headers(author, settings),
                        json={
                            "parent": {
                                "name": "账号登录",
                                "canonical_keyword": "登录",
                            },
                            "primary_child": {
                                "question": "无法登录怎么办？",
                                "response_content": "请检查账号和密码。",
                            },
                            "knowledge_base_ids": knowledge_base_ids,
                        },
                    )
                    assert submitted.status_code == 201
                    parent_submission_id = submitted.json()["id"]
                    primary_child_id = submitted.json()["child_id"]

                    queue = await reviewer.get("/api/v1/knowledge-content/review-queue")
                    assert queue.status_code == 200
                    assert {item["knowledge_base"]["id"] for item in queue.json()} == set(
                        knowledge_base_ids
                    )

                    first_decision = await reviewer.post(
                        f"/api/v1/knowledge-content/review-submissions/{parent_submission_id}"
                        f"/targets/{knowledge_base_ids[0]}/decision",
                        headers=csrf_headers(reviewer, settings),
                        json={"decision": "approved"},
                    )
                    assert first_decision.status_code == 201
                    assert (await reviewer.get("/api/v1/knowledge-content/review-queue")).json()

                    second_decision = await reviewer.post(
                        f"/api/v1/knowledge-content/review-submissions/{parent_submission_id}"
                        f"/targets/{knowledge_base_ids[1]}/decision",
                        headers=csrf_headers(reviewer, settings),
                        json={"decision": "approved"},
                    )
                    assert second_decision.status_code == 201
                    assert (
                        await reviewer.get("/api/v1/knowledge-content/review-queue")
                    ).json() == []

                    review_history = await reviewer.get(
                        "/api/v1/knowledge-content/review-history"
                    )
                    assert review_history.status_code == 200
                    history_items = review_history.json()
                    assert len(history_items) == 2
                    assert {item["submitter"]["username"] for item in history_items} == {"author"}
                    assert {item["reviewer"]["username"] for item in history_items} == {"reviewer"}
                    assert {item["review_decision"] for item in history_items} == {"approved"}
                    assert all(item["submitted_at"] for item in history_items)
                    assert all(item["reviewed_at"] for item in history_items)

                    async with app.state.session_factory() as session:  # type: ignore[attr-defined]
                        jobs = list((await session.scalars(select(IndexJob))).all())
                        assert len(jobs) == 2
                        assert {job.status for job in jobs} == {IndexJobStatus.PENDING}
                    for _ in jobs:
                        result = await run_index_worker_once(
                            app.state.session_factory,  # type: ignore[attr-defined]
                            worker_id="test-worker",
                            backend=LocalArtifactIndexBackend(settings.index_artifact_dir),
                        )
                        assert result is not None
                        assert result.status == IndexJobStatus.SUCCEEDED

                    parent_id = submitted.json()["parent_id"]
                    available = await author.get("/api/v1/knowledge-content/parents/available")
                    assert available.status_code == 200
                    assert available.json()[0]["id"] == parent_id

                    uploaded_attachment = await author.post(
                        "/api/v1/knowledge-content/attachments",
                        headers=csrf_headers(author, settings),
                        files={
                            "attachment_file": (
                                "password-reset.txt",
                                "请先确认用户身份。".encode(),
                                "text/plain",
                            )
                        },
                    )
                    assert uploaded_attachment.status_code == 201
                    attachment_payload = uploaded_attachment.json()
                    attachment_id = attachment_payload["id"]
                    assert attachment_payload == {
                        "id": attachment_id,
                        "name": "password-reset.txt",
                        "content_type": "text/plain",
                        "size_bytes": len("请先确认用户身份。".encode()),
                    }
                    uploaded_download = await author.get(
                        f"/api/v1/knowledge-content/attachments/{attachment_id}/download"
                    )
                    assert uploaded_download.status_code == 200
                    assert uploaded_download.content == "请先确认用户身份。".encode()

                    ordinary = await author.post(
                        "/api/v1/knowledge-content/child-submissions",
                        headers=csrf_headers(author, settings),
                        json={
                            "parent_id": parent_id,
                            "child": {
                                "question": "如何找回密码？",
                                "response_content": "请联系管理员。",
                                "question_variants": ["密码找回流程怎么走？"],
                                "follow_up_guidance": "确认身份后再进行密码重置。",
                                "question_type": "功能故障类",
                                "business_object": "账户建立&账户迁移",
                                "purpose": "企业微信咨询",
                                "customer_type": "个人客户",
                                "feature_explanation": "用于处理账号密码找回问题。",
                                "example": "用户忘记登录密码。",
                                "internal_notes": "仅供支持人员参考。",
                                "attachments": [attachment_id],
                                "web_links": [
                                    {
                                        "title": "密码重置操作说明",
                                        "url": "https://docs.example.test/password-reset",
                                    }
                                ],
                            },
                            "knowledge_base_ids": knowledge_base_ids,
                        },
                    )
                    assert ordinary.status_code == 201
                    child_submission_id = ordinary.json()["id"]
                    child_id = ordinary.json()["child_id"]

                    child_queue = await reviewer.get("/api/v1/knowledge-content/review-queue")
                    assert child_queue.status_code == 200
                    child_queue_item = next(
                        item
                        for item in child_queue.json()
                        if item["review_submission_id"] == child_submission_id
                    )
                    assert child_queue_item["child_revision"]["attachments"] == [
                        attachment_payload
                    ]
                    assert child_queue_item["child_revision"]["web_links"] == [
                        {
                            "title": "密码重置操作说明",
                            "url": "https://docs.example.test/password-reset",
                        }
                    ]
                    reviewer_download = await reviewer.get(
                        f"/api/v1/knowledge-content/attachments/{attachment_id}/download"
                    )
                    assert reviewer_download.status_code == 200

                    approve_child = await reviewer.post(
                        f"/api/v1/knowledge-content/review-submissions/{child_submission_id}"
                        f"/targets/{knowledge_base_ids[0]}/decision",
                        headers=csrf_headers(reviewer, settings),
                        json={"decision": "approved"},
                    )
                    assert approve_child.status_code == 201
                    result = await run_index_worker_once(
                        app.state.session_factory,  # type: ignore[attr-defined]
                        worker_id="test-worker",
                        backend=LocalArtifactIndexBackend(settings.index_artifact_dir),
                    )
                    assert result is not None
                    assert result.status == IndexJobStatus.SUCCEEDED

                    reject_child = await reviewer.post(
                        f"/api/v1/knowledge-content/review-submissions/{child_submission_id}"
                        f"/targets/{knowledge_base_ids[1]}/decision",
                        headers=csrf_headers(reviewer, settings),
                        json={"decision": "rejected", "comment": "不适用于该知识库"},
                    )
                    assert reject_child.status_code == 201

                    mine = await author.get("/api/v1/knowledge-content/submissions/mine")
                    assert mine.status_code == 200
                    child_submission = next(
                        item for item in mine.json() if item["id"] == child_submission_id
                    )
                    assert (
                        child_submission["child_revision"]["response_content"] == "请联系管理员。"
                    )
                    assert child_submission["child_revision"]["attachments"] == [
                        attachment_payload
                    ]
                    assert child_submission["child_revision"]["web_links"] == [
                        {
                            "title": "密码重置操作说明",
                            "url": "https://docs.example.test/password-reset",
                        }
                    ]
                    child_targets = {
                        target["id"]: target for target in child_submission["targets"]
                    }
                    assert child_submission["submitter"]["username"] == "author"
                    assert (
                        child_targets[knowledge_base_ids[0]]["reviewer"]["username"]
                        == "reviewer"
                    )
                    assert child_targets[knowledge_base_ids[0]]["reviewed_at"] is not None
                    assert child_targets[knowledge_base_ids[1]]["status"] == "rejected"
                    assert (
                        child_targets[knowledge_base_ids[1]]["review_comment"]
                        == "不适用于该知识库"
                    )

                    editable_entries = await author.get(
                        "/api/v1/knowledge-content/entries/editable"
                    )
                    assert editable_entries.status_code == 200
                    editable_payload = editable_entries.json()
                    assert any(
                        entry["is_primary"]
                        and entry["parent_revision"] is not None
                        and set(item["id"] for item in entry["knowledge_bases"])
                        == set(knowledge_base_ids)
                        for entry in editable_payload
                    )
                    assert any(
                        not entry["is_primary"]
                        and entry["child_id"] == child_id
                        and [item["id"] for item in entry["knowledge_bases"]]
                        == [knowledge_base_ids[0]]
                        and entry["child_revision"]["attachments"] == [attachment_payload]
                        for entry in editable_payload
                    )

                    child_resubmitted = await author.post(
                        f"/api/v1/knowledge-content/review-submissions/{child_submission_id}"
                        "/resubmit-child",
                        headers=csrf_headers(author, settings),
                        json={
                            "child": {
                                "question": "如何找回密码？",
                                "response_content": "请先验证身份，再联系管理员重置密码。",
                                "attachments": [attachment_id],
                            },
                            "knowledge_base_ids": [knowledge_base_ids[1]],
                        },
                    )
                    assert child_resubmitted.status_code == 201
                    child_resubmitted_payload = child_resubmitted.json()
                    assert child_resubmitted_payload["id"] != child_submission_id
                    assert child_resubmitted_payload["child_id"] == child_id
                    assert (
                        child_resubmitted_payload["child_revision_id"]
                        != child_submission["child_revision_id"]
                    )
                    assert child_resubmitted_payload["status"] == "pending_review"
                    assert child_resubmitted_payload["targets"] == [
                        {
                            "id": knowledge_base_ids[1],
                            "logical_key": "sales-help",
                            "name": "销售帮助",
                            "status": "pending_review",
                            "review_comment": None,
                            "reviewer": None,
                            "reviewed_at": None,
                            "review_decision": None,
                        }
                    ]

                    rejected_parent = await author.post(
                        "/api/v1/knowledge-content/parent-submissions",
                        headers=csrf_headers(author, settings),
                        json={
                            "parent": {
                                "name": "支付问题",
                                "canonical_keyword": "支付",
                            },
                            "primary_child": {
                                "question": "支付失败怎么办？",
                                "response_content": "请检查支付状态。",
                            },
                            "knowledge_base_ids": knowledge_base_ids,
                        },
                    )
                    assert rejected_parent.status_code == 201
                    rejected_parent_payload = rejected_parent.json()
                    rejected_parent_submission_id = rejected_parent_payload["id"]
                    rejected_parent_id = rejected_parent_payload["parent_id"]
                    reject_parent = await reviewer.post(
                        f"/api/v1/knowledge-content/review-submissions/{rejected_parent_submission_id}"
                        f"/targets/{knowledge_base_ids[0]}/decision",
                        headers=csrf_headers(reviewer, settings),
                        json={"decision": "rejected", "comment": "请补充支付失败处理路径"},
                    )
                    assert reject_parent.status_code == 201

                    parent_resubmitted = await author.post(
                        f"/api/v1/knowledge-content/review-submissions/{rejected_parent_submission_id}"
                        "/resubmit-parent",
                        headers=csrf_headers(author, settings),
                        json={
                            "parent": {
                                "name": "支付问题（已修订）",
                                "canonical_keyword": "支付",
                            },
                            "primary_child": {
                                "question": "支付失败时如何处理？",
                                "response_content": "请先确认支付状态，再按照订单状态处理。",
                            },
                        },
                    )
                    assert parent_resubmitted.status_code == 201
                    parent_resubmitted_payload = parent_resubmitted.json()
                    assert parent_resubmitted_payload["id"] != rejected_parent_submission_id
                    assert parent_resubmitted_payload["parent_id"] == rejected_parent_id
                    assert parent_resubmitted_payload["status"] == "pending_review"
                    assert (
                        parent_resubmitted_payload["parent_revision_id"]
                        != rejected_parent_payload["parent_revision_id"]
                    )
                    assert (
                        parent_resubmitted_payload["child_revision_id"]
                        != rejected_parent_payload["child_revision_id"]
                    )
                    assert {
                        target["id"] for target in parent_resubmitted_payload["targets"]
                    } == set(knowledge_base_ids)
                    assert {
                        target["status"] for target in parent_resubmitted_payload["targets"]
                    } == {"pending_review"}

                    publication = await author.get(
                        f"/api/v1/knowledge-content/children/{child_id}/publications/{knowledge_base_ids[0]}"
                    )
                    assert publication.status_code == 200
                    assert publication.json()["status"] == "published"
                    assert publication.json()["pending_submission_id"] is None

                    filtered_search = await author.post(
                        "/api/v1/search",
                        headers=csrf_headers(author, settings),
                        json={
                            "retrieval_mode": "field_filter",
                            "question_type": "功能故障类",
                            "business_object": "账户建立&账户迁移",
                            "limit": 10,
                        },
                    )
                    assert filtered_search.status_code == 200
                    filtered_payload = filtered_search.json()
                    assert filtered_payload["no_match"] is False
                    filtered_item = filtered_payload["groups"][0]["children"][0]
                    assert filtered_item["match_reason"] == "field_filter"
                    assert filtered_item["question_variants"] == ["密码找回流程怎么走？"]
                    assert filtered_item["follow_up_guidance"] == "确认身份后再进行密码重置。"
                    assert filtered_item["question_type"] == "功能故障类"
                    assert filtered_item["business_object"] == "账户建立&账户迁移"
                    assert filtered_item["purpose"] == "企业微信咨询"
                    assert filtered_item["customer_type"] == "个人客户"
                    assert filtered_item["feature_explanation"] == "用于处理账号密码找回问题。"
                    assert filtered_item["example"] == "用户忘记登录密码。"
                    assert "internal_notes" not in filtered_item
                    assert filtered_item["attachments"] == [attachment_payload]
                    assert filtered_item["web_links"] == [
                        {
                            "title": "密码重置操作说明",
                            "url": "https://docs.example.test/password-reset",
                        }
                    ]
                    published_download = await author.get(
                        f"/api/v1/knowledge-content/attachments/{attachment_id}/download"
                    )
                    assert published_download.status_code == 200

                    all_entries_search = await author.post(
                        "/api/v1/search",
                        headers=csrf_headers(author, settings),
                        json={"retrieval_mode": "field_filter", "limit": 1},
                    )
                    assert all_entries_search.status_code == 200
                    all_entries_payload = all_entries_search.json()
                    all_entries = [
                        item
                        for group in all_entries_payload["groups"]
                        for item in group["children"]
                    ]
                    assert len(all_entries) > 1
                    assert {primary_child_id, child_id}.issubset(
                        {item["child_id"] for item in all_entries}
                    )
                    assert {item["match_reason"] for item in all_entries} == {"field_filter"}

                    invalid_mixed_search = await author.post(
                        "/api/v1/search",
                        headers=csrf_headers(author, settings),
                        json={
                            "retrieval_mode": "vector",
                            "query": "如何找回密码？",
                            "question_type": "功能故障类",
                        },
                    )
                    assert invalid_mixed_search.status_code == 422

                    search = await author.post(
                        "/api/v1/search",
                        headers=csrf_headers(author, settings),
                        json={"query": "如何找回密码？", "limit": 10},
                    )
                    assert search.status_code == 200
                    search_payload = search.json()
                    assert search_payload["no_match"] is False
                    result_item = search_payload["groups"][0]["children"][0]
                    assert result_item["match_reason"].startswith("hybrid_dense_bm25")
                    feedback = await author.post(
                        f"/api/v1/search/events/{search_payload['search_event_id']}/feedback",
                        headers=csrf_headers(author, settings),
                        json={"result_item_id": result_item["result_item_id"]},
                    )
                    assert feedback.status_code == 200
                    assert feedback.json()["already_recorded"] is False
                    duplicate_feedback = await author.post(
                        f"/api/v1/search/events/{search_payload['search_event_id']}/feedback",
                        headers=csrf_headers(author, settings),
                        json={"result_item_id": result_item["result_item_id"]},
                    )
                    assert duplicate_feedback.status_code == 200
                    assert duplicate_feedback.json()["already_recorded"] is True

                    revised_child = await author.post(
                        f"/api/v1/knowledge-content/children/{child_id}/revisions",
                        headers=csrf_headers(author, settings),
                        json={
                            "child": {
                                "question": "如何找回密码？",
                                "response_content": "请先确认身份，再联系管理员重置密码。",
                            },
                            "knowledge_base_ids": [knowledge_base_ids[0]],
                        },
                    )
                    assert revised_child.status_code == 201
                    revised_child_payload = revised_child.json()
                    assert revised_child_payload["child_id"] == child_id
                    assert (
                        revised_child_payload["child_revision_id"]
                        != child_submission["child_revision_id"]
                    )

                    reject_revised_child = await reviewer.post(
                        f"/api/v1/knowledge-content/review-submissions/{revised_child_payload['id']}"
                        f"/targets/{knowledge_base_ids[0]}/decision",
                        headers=csrf_headers(reviewer, settings),
                        json={"decision": "rejected", "comment": "保留当前线上版本"},
                    )
                    assert reject_revised_child.status_code == 201

                    managed_knowledge = await admin.get("/api/v1/knowledge-content/admin/knowledge")
                    assert managed_knowledge.status_code == 200
                    managed_entry = next(
                        entry
                        for entry in managed_knowledge.json()
                        if entry["child_id"] == child_id
                        and entry["knowledge_base"]["id"] == knowledge_base_ids[0]
                    )
                    assert managed_entry["uploaded_by"]["username"] == "author"
                    assert managed_entry["uploaded_at"]
                    assert managed_entry["embedded_at"]
                    assert managed_entry["status"] == "published"

                    deleted_knowledge = await admin.delete(
                        f"/api/v1/knowledge-content/admin/knowledge/{child_id}"
                        f"/knowledge-bases/{knowledge_base_ids[0]}",
                        headers=csrf_headers(admin, settings),
                    )
                    assert deleted_knowledge.status_code == 204

                    archived_publication = await author.get(
                        f"/api/v1/knowledge-content/children/{child_id}/publications/{knowledge_base_ids[0]}"
                    )
                    assert archived_publication.status_code == 200
                    assert archived_publication.json()["status"] == "archived"

                    cleanup_result = await run_index_worker_once(
                        app.state.session_factory,  # type: ignore[attr-defined]
                        worker_id="test-worker",
                        backend=LocalArtifactIndexBackend(settings.index_artifact_dir),
                    )
                    assert cleanup_result is not None
                    assert cleanup_result.status == IndexJobStatus.SUCCEEDED
                    artifact_path = (
                        settings.index_artifact_dir
                        / knowledge_base_ids[0]
                        / f"{child_submission['child_revision_id']}.json"
                    )
                    assert not artifact_path.exists()

                    managed_after_delete = await admin.get(
                        "/api/v1/knowledge-content/admin/knowledge"
                    )
                    assert managed_after_delete.status_code == 200
                    assert any(
                        entry["child_id"] == child_id
                        and entry["knowledge_base"]["id"] == knowledge_base_ids[0]
                        and entry["status"] == "archived"
                        for entry in managed_after_delete.json()
                    )
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
