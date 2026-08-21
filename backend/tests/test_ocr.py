from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models  # noqa: F401  # Register model metadata.
from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from app.models.audit_event import AuditEvent
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_content import (
    Child,
    ChildKnowledgeBasePublication,
    ChildPublicationStatus,
    ChildRevision,
    Parent,
    ParentLexicalRule,
    ParentLexicalRuleType,
    ParentRevision,
    ReviewSubmission,
    ReviewSubmissionKind,
    ReviewSubmissionStatus,
    SearchEvent,
)
from app.models.user_account import UserAccount, UserRole
from app.services.ocr import (
    OCR_MODEL_NAME,
    HttpOcrProvider,
    OcrNoTextError,
    OcrRecognition,
    OcrRecognitionTokenError,
    create_ocr_recognition_token,
    decode_ocr_recognition_token,
    validate_ocr_image,
)
from app.services.search import search_published_content

PNG_BYTES = b"\x89PNG\r\n\x1a\nocr-test-image"


class FakeOcrProvider:
    async def recognize(self, image_bytes: bytes, media_type: str) -> OcrRecognition:
        assert media_type == "image/png"
        return OcrRecognition(
            text="账号 登录失败",
            keywords=("账号", "登录"),
            confidence=0.97,
            model_version=OCR_MODEL_NAME,
            image_sha256=hashlib.sha256(image_bytes).hexdigest(),
        )


async def build_api_test_app(tmp_path: Path) -> tuple[object, AsyncEngine]:
    initial_password_file = tmp_path / "initial-password.txt"
    initial_password_file.write_text("InitialPassword-123!", encoding="utf-8")
    settings = Settings(
        app_environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'ocr-api.sqlite3'}",
        jwt_secret="test-signing-key-that-is-long-enough",
        cookie_secure=False,
        initial_admin_username="bootstrap-admin",
        initial_admin_password_file=initial_password_file,
        index_artifact_dir=tmp_path / "index-artifacts",
        attachment_storage_dir=tmp_path / "attachments",
    )
    engine = create_async_engine(settings.database_url_with_password)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(settings=settings, db_session_factory=factory)
    app.state.ocr_provider = FakeOcrProvider()
    return app, engine


def csrf_headers(client: AsyncClient, settings: Settings) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies[settings.csrf_cookie_name]}


async def fully_authenticate_test_admin(client: AsyncClient, settings: Settings) -> None:
    assert (await client.get("/api/v1/auth/csrf")).status_code == 204
    logged_in = await client.post(
        "/api/v1/auth/login",
        headers={"X-CSRF-Token": client.cookies[settings.pre_auth_csrf_cookie_name]},
        json={"username": "bootstrap-admin", "password": "InitialPassword-123!"},
    )
    assert logged_in.status_code == 200
    changed_password = await client.post(
        "/api/v1/auth/change-password",
        headers=csrf_headers(client, settings),
        json={
            "current_password": "InitialPassword-123!",
            "new_password": "ChangedPassword-123!",
        },
    )
    assert changed_password.status_code == 200


@pytest.mark.asyncio
async def test_uploading_an_image_issues_a_user_bound_ocr_token_and_records_safe_metadata(
    tmp_path: Path,
) -> None:
    app, engine = await build_api_test_app(tmp_path)
    settings: Settings = app.state.settings
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="https://testserver") as client:
                await fully_authenticate_test_admin(client, settings)
                recognized = await client.post(
                    "/api/v1/search/ocr",
                    headers=csrf_headers(client, settings),
                    files={"image": ("query.png", PNG_BYTES, "image/png")},
                )
                assert recognized.status_code == 200
                payload = recognized.json()
                assert payload["text"] == "账号 登录失败"
                assert payload["keywords"] == ["账号", "登录"]
                assert payload["confidence"] == 0.97
                assert payload["model_version"] == OCR_MODEL_NAME

                searched = await client.post(
                    "/api/v1/search",
                    headers=csrf_headers(client, settings),
                    json={"ocr_recognition_token": payload["recognition_token"]},
                )
                assert searched.status_code == 200
                assert searched.json()["query_mode"] == "image"

            session_factory = app.state.session_factory  # type: ignore[attr-defined]
            async with session_factory() as session:
                audit_event = await session.scalar(
                    select(AuditEvent).where(AuditEvent.event_type == "search.ocr_recognized")
                )
                assert audit_event is not None
                assert audit_event.payload == {
                    "ocr_text": "账号 登录失败",
                    "keywords": ["账号", "登录"],
                    "confidence": 0.97,
                    "model_version": OCR_MODEL_NAME,
                    "image_sha256": hashlib.sha256(PNG_BYTES).hexdigest(),
                }
                assert "image_base64" not in audit_event.payload

                event = await session.scalar(select(SearchEvent))
                assert event is not None
                assert event.ocr_text == "账号 登录失败"
                assert event.ocr_keywords == ["账号", "登录"]
                assert event.ocr_confidence == 0.97
                assert event.ocr_model_version == OCR_MODEL_NAME
                assert event.ocr_image_sha256 == hashlib.sha256(PNG_BYTES).hexdigest()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_conversation_ocr_does_not_retain_recognized_chat_text_in_audit_logs(
    tmp_path: Path,
) -> None:
    app, engine = await build_api_test_app(tmp_path)
    settings: Settings = app.state.settings
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="https://testserver") as client:
                await fully_authenticate_test_admin(client, settings)
                recognized = await client.post(
                    "/api/v1/search/ocr?purpose=conversation",
                    headers=csrf_headers(client, settings),
                    files={"image": ("forwarded-card.png", PNG_BYTES, "image/png")},
                )
                assert recognized.status_code == 200
                assert recognized.json()["text"] == "账号 登录失败"

            session_factory = app.state.session_factory  # type: ignore[attr-defined]
            async with session_factory() as session:
                audit_event = await session.scalar(
                    select(AuditEvent).where(AuditEvent.event_type == "conversation.ocr_recognized")
                )
                assert audit_event is not None
                assert audit_event.payload == {
                    "model_version": OCR_MODEL_NAME,
                    "image_sha256": hashlib.sha256(PNG_BYTES).hexdigest(),
                }
                assert "ocr_text" not in audit_event.payload
                assert "keywords" not in audit_event.payload
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_http_ocr_provider_uses_the_fixed_local_service_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://ocr.local/ocr")
        assert request.headers["authorization"] == "Bearer service-token"
        request_payload = json.loads(request.content)
        assert request_payload == {
            "model": OCR_MODEL_NAME,
            "image_base64": base64.b64encode(PNG_BYTES).decode("ascii"),
            "mime_type": "image/png",
        }
        return httpx.Response(
            200,
            json={
                "data": {
                    "text": "  账号\n登录失败  ",
                    "keywords": ["账号", "登录", "账号"],
                    "confidence": 0.93,
                    "model_version": OCR_MODEL_NAME,
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        recognition = await HttpOcrProvider(
            "http://ocr.local",
            api_key="service-token",
            client=client,
        ).recognize(PNG_BYTES, "image/png")
    finally:
        await client.aclose()

    assert recognition.text == "账号 登录失败"
    assert recognition.keywords == ("账号", "登录")
    assert recognition.confidence == 0.93
    assert recognition.image_sha256 == hashlib.sha256(PNG_BYTES).hexdigest()


@pytest.mark.asyncio
async def test_http_ocr_provider_maps_a_local_no_text_response() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                422,
                json={"detail": "图片中没有可用于检索的文字"},
            )
        )
    )
    try:
        with pytest.raises(OcrNoTextError, match="没有可用于检索"):
            await HttpOcrProvider("http://ocr.local", client=client).recognize(
                PNG_BYTES,
                "image/png",
            )
    finally:
        await client.aclose()


def test_ocr_image_validation_and_token_are_strictly_scoped() -> None:
    assert validate_ocr_image(PNG_BYTES, max_bytes=len(PNG_BYTES)) == "image/png"
    with pytest.raises(ValueError, match="PNG、JPEG 或 WebP"):
        validate_ocr_image(b"not-an-image", max_bytes=1024)

    settings = Settings(
        app_environment="test",
        database_url="sqlite+aiosqlite:///token-test.sqlite3",
        jwt_secret="test-signing-key-that-is-long-enough",
        cookie_secure=False,
    )
    owner_id = uuid4()
    recognition = OcrRecognition(
        text="账号登录",
        keywords=("账号",),
        confidence=0.95,
        model_version=OCR_MODEL_NAME,
        image_sha256=hashlib.sha256(PNG_BYTES).hexdigest(),
    )
    token = create_ocr_recognition_token(recognition, user_id=owner_id, settings=settings)
    assert decode_ocr_recognition_token(token, user_id=owner_id, settings=settings) == recognition
    with pytest.raises(OcrRecognitionTokenError, match="不属于当前用户"):
        decode_ocr_recognition_token(token, user_id=uuid4(), settings=settings)


async def build_search_data(
    tmp_path: Path,
) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine, UUID]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ocr-search.sqlite3'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = UserAccount(
            username="ocr-search-user",
            display_name="OCR Search User",
            password_hash="x" * 32,
            role=UserRole.NORMAL_USER,
            must_change_password=False,
        )
        session.add(user)
        await session.flush()
        knowledge_base = KnowledgeBase(
            logical_key="ocr-search",
            name="OCR 检索知识库",
            current_physical_collection_name="ocr-search-g1",
            created_by_user_id=user.id,
        )
        parent = Parent(created_by_user_id=user.id)
        session.add_all([knowledge_base, parent])
        await session.flush()
        parent_revision = ParentRevision(
            parent_id=parent.id,
            revision_number=1,
            name="账号登录",
            canonical_keyword="账号",
            created_by_user_id=user.id,
        )
        child = Child(parent_id=parent.id, is_primary=True, created_by_user_id=user.id)
        session.add_all([parent_revision, child])
        await session.flush()
        child_revision = ChildRevision(
            child_id=child.id,
            revision_number=1,
            question="怎样调整颜色主题？",
            response_content="在显示设置中调整颜色主题。",
            created_by_user_id=user.id,
        )
        session.add(child_revision)
        await session.flush()
        session.add_all(
            [
                ParentLexicalRule(
                    parent_revision_id=parent_revision.id,
                    rule_type=ParentLexicalRuleType.ALIAS,
                    rule_value="帐户",
                    sort_order=0,
                ),
                ParentLexicalRule(
                    parent_revision_id=parent_revision.id,
                    rule_type=ParentLexicalRuleType.REGEX,
                    rule_value="^截图关键词$",
                    sort_order=1,
                ),
                ReviewSubmission(
                    submission_kind=ReviewSubmissionKind.PARENT_WITH_PRIMARY,
                    status=ReviewSubmissionStatus.PUBLISHED,
                    parent_id=parent.id,
                    parent_revision_id=parent_revision.id,
                    child_id=child.id,
                    child_revision_id=child_revision.id,
                    submitted_by_user_id=user.id,
                ),
                ChildKnowledgeBasePublication(
                    child_id=child.id,
                    knowledge_base_id=knowledge_base.id,
                    status=ChildPublicationStatus.PUBLISHED,
                    active_revision_id=child_revision.id,
                ),
            ]
        )
        await session.commit()
        return factory, engine, user.id


@pytest.mark.asyncio
async def test_only_trusted_high_confidence_ocr_can_use_exact_keyword_fallback(
    tmp_path: Path,
) -> None:
    factory, engine, user_id = await build_search_data(tmp_path)
    trusted_recognition = OcrRecognition(
        text="截图显示账号错误",
        keywords=("账号",),
        confidence=0.95,
        model_version=OCR_MODEL_NAME,
        image_sha256=hashlib.sha256(PNG_BYTES).hexdigest(),
    )
    try:
        async with factory() as session:
            trusted = await search_published_content(
                session,
                user_id=user_id,
                query=None,
                ocr_text=trusted_recognition.text,
                ocr_recognition=trusted_recognition,
                ocr_keyword_fallback_min_confidence=0.9,
                knowledge_base_id=None,
                retrieval_mode="vector",
                limit=10,
            )
            assert trusted.event.ocr_confidence == 0.95
            assert trusted.groups[0][1][0][0].match_reason == "ocr_keyword_fallback"

            untrusted = await search_published_content(
                session,
                user_id=user_id,
                query=None,
                ocr_text=trusted_recognition.text,
                knowledge_base_id=None,
                retrieval_mode="vector",
                limit=10,
            )
            assert untrusted.event.no_match is True

            low_confidence = await search_published_content(
                session,
                user_id=user_id,
                query=None,
                ocr_text=trusted_recognition.text,
                ocr_recognition=OcrRecognition(
                    text=trusted_recognition.text,
                    keywords=trusted_recognition.keywords,
                    confidence=0.89,
                    model_version=OCR_MODEL_NAME,
                    image_sha256=trusted_recognition.image_sha256,
                ),
                ocr_keyword_fallback_min_confidence=0.9,
                knowledge_base_id=None,
                retrieval_mode="vector",
                limit=10,
            )
            assert low_confidence.event.no_match is True

            regex_only = await search_published_content(
                session,
                user_id=user_id,
                query=None,
                ocr_text="截图关键词",
                ocr_recognition=OcrRecognition(
                    text="截图关键词",
                    keywords=("截图关键词",),
                    confidence=0.95,
                    model_version=OCR_MODEL_NAME,
                    image_sha256=trusted_recognition.image_sha256,
                ),
                ocr_keyword_fallback_min_confidence=0.9,
                knowledge_base_id=None,
                retrieval_mode="vector",
                limit=10,
            )
            assert regex_only.event.no_match is True
    finally:
        await engine.dispose()
