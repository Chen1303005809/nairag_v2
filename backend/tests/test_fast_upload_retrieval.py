from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # Register model metadata.
from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from app.models.intelligent_ingestion import (
    IntelligentIngestionBatch,
    IntelligentIngestionBatchStatus,
    KnowledgeDraft,
    KnowledgeDraftSource,
)
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_content import (
    Child,
    ChildKnowledgeBasePublication,
    ChildPublicationStatus,
    ChildRevision,
    Parent,
    ParentRevision,
    ReviewSubmission,
    ReviewSubmissionKind,
    ReviewSubmissionStatus,
)
from app.models.user_account import UserAccount, UserRole
from app.schemas.drafts import KnowledgeDraftInput
from app.services.conversation import (
    ConversationInputError,
    NormalizedConversationMessage,
    validate_conversation,
)
from app.services.drafts import (
    DraftNotSubmittableError,
    create_manual_draft,
    submit_draft,
    update_draft,
)
from app.services.fast_search import conversation_assisted_search
from app.services.intelligent_ingestion import (
    create_ingestion_batch,
    process_ingestion_batch,
    purge_expired_ingestion_raw_input,
    run_ingestion_worker_once,
)
from app.services.llm import (
    KnowledgeExtraction,
    LlmConfigurationError,
    LlmOutputError,
    LlmProviderError,
    OpenAiCompatibleLlmProvider,
    QueryExtraction,
    RelevanceCandidate,
)
from app.services.search import search_published_content


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'fast-upload.sqlite3'}",
        jwt_secret="test-signing-key-that-is-long-enough",
        cookie_secure=False,
        index_artifact_dir=tmp_path / "index-artifacts",
        llm_max_conversation_messages=4,
        llm_max_conversation_chars=1_000,
        openai_base_url=None,
        openai_key=None,
    )


def test_optional_openai_configuration_accepts_blank_values() -> None:
    settings = Settings(
        app_environment="test",
        database_url="sqlite+aiosqlite:///./fast-upload-settings.sqlite3",
        jwt_secret="test-signing-key-that-is-long-enough",
        cookie_secure=False,
        openai_base_url="  ",
        openai_key="  ",
    )

    assert settings.openai_base_url is None
    assert settings.openai_key is None


async def build_db(tmp_path: Path) -> tuple[async_sessionmaker[AsyncSession], object, Settings]:
    settings = make_settings(tmp_path)
    engine = create_async_engine(settings.database_url_with_password)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine, settings


async def create_user(session: AsyncSession) -> UserAccount:
    user = UserAccount(
        username=f"user-{uuid4().hex[:8]}",
        display_name="Fast Upload User",
        password_hash="x" * 32,
        role=UserRole.NORMAL_USER,
        must_change_password=False,
    )
    session.add(user)
    await session.flush()
    return user


async def create_published_graph(
    session: AsyncSession,
    user_id,
) -> tuple[Parent, KnowledgeBase, Child, ChildRevision]:
    parent = Parent(created_by_user_id=user_id)
    knowledge_base = KnowledgeBase(
        logical_key=f"kb-{uuid4().hex[:8]}",
        name="快速上传知识库",
        current_physical_collection_name=f"collection-{uuid4().hex[:8]}",
        created_by_user_id=user_id,
    )
    session.add_all([parent, knowledge_base])
    await session.flush()

    parent_revision = ParentRevision(
        parent_id=parent.id,
        revision_number=1,
        name="登录问题",
        canonical_keyword="登录",
        created_by_user_id=user_id,
    )
    primary_child = Child(parent_id=parent.id, is_primary=True, created_by_user_id=user_id)
    session.add_all([parent_revision, primary_child])
    await session.flush()
    primary_revision = ChildRevision(
        child_id=primary_child.id,
        revision_number=1,
        question="登录失败怎么办？",
        response_content="请先检查账号状态，再联系管理员重置。",
        created_by_user_id=user_id,
    )
    session.add(primary_revision)
    await session.flush()
    session.add(
        ReviewSubmission(
            submission_kind=ReviewSubmissionKind.PARENT_WITH_PRIMARY,
            status=ReviewSubmissionStatus.PUBLISHED,
            parent_id=parent.id,
            parent_revision_id=parent_revision.id,
            child_id=primary_child.id,
            child_revision_id=primary_revision.id,
            submitted_by_user_id=user_id,
        )
    )
    session.add(
        ChildKnowledgeBasePublication(
            child_id=primary_child.id,
            knowledge_base_id=knowledge_base.id,
            status=ChildPublicationStatus.PUBLISHED,
            active_revision_id=primary_revision.id,
        )
    )
    await session.flush()
    return parent, knowledge_base, primary_child, primary_revision


def sample_messages() -> list[NormalizedConversationMessage]:
    return [
        NormalizedConversationMessage(
            speaker="张客户",
            role="customer",
            body="登录一直失败怎么办？",
        ),
        NormalizedConversationMessage(
            speaker="融航-李支持",
            role="ours",
            body="请先在登录页检查账号状态；若仍失败，请联系管理员重置密码。",
        ),
        NormalizedConversationMessage(speaker="张客户", role="customer", body="还有发票怎么开？"),
        NormalizedConversationMessage(
            speaker="融航-李支持",
            role="ours",
            body="发票请联系财务邮箱开具，我需要再确认一下流程。",
        ),
    ]


class FakeExtractionProvider:
    def __init__(self) -> None:
        self.model = "fake-llm"

    async def extract_knowledge_candidates(self, transcript: str):
        from app.services.llm import KnowledgeCandidate, KnowledgeExtraction, RejectedCandidate

        assert "张客户" in transcript
        return KnowledgeExtraction(
            candidates=[
                KnowledgeCandidate(
                    question="登录失败怎么办？",
                    response_content="请先在登录页检查账号状态；若仍失败，请联系管理员重置密码。",
                    question_variants=["无法登录如何处理？"],
                    follow_up_guidance="仍无法登录时联系管理员。",
                )
            ],
            non_candidates=[
                RejectedCandidate(topic="发票开具", reason="回答存在不确定性，缺少可复用结论")
            ],
        )

    async def extract_search_queries(self, transcript: str):
        from app.services.llm import QueryExtraction

        return QueryExtraction(queries=["登录失败如何处理？", "发票开具流程"], total_candidates=2)


class FailingProvider:
    def __init__(self, error: Exception) -> None:
        self.model = "fake-llm"
        self.error = error

    async def extract_knowledge_candidates(self, transcript: str):
        raise self.error

    async def extract_search_queries(self, transcript: str):
        raise self.error


class StubIngestionProvider:
    model = "stub-llm"

    async def extract_knowledge_candidates(self, transcript: str):
        return KnowledgeExtraction(candidates=[], non_candidates=[])

    async def extract_search_queries(self, transcript: str):
        return QueryExtraction(queries=[], total_candidates=0)


class EmptyIndexBackend:
    async def search(self, **_kwargs):
        return []


class FailingIndexBackend:
    async def search(self, **_kwargs):
        raise RuntimeError("embedding service unavailable")


def test_conversation_validation_requires_both_parties_and_limits() -> None:
    settings = make_settings(Path("/tmp/nairag-fast-upload-test"))
    customer_only = [NormalizedConversationMessage(speaker="张客户", role="customer", body="问题")]
    with pytest.raises(ConversationInputError, match="双方"):
        validate_conversation(
            customer_only,
            max_messages=settings.llm_max_conversation_messages,
            max_chars=settings.llm_max_conversation_chars,
            require_both_parties=True,
        )

    with pytest.raises(ConversationInputError, match="数量超过上限"):
        validate_conversation(
            sample_messages() * 2,
            max_messages=settings.llm_max_conversation_messages,
            max_chars=settings.llm_max_conversation_chars,
            require_both_parties=True,
        )

    long_messages = [
            NormalizedConversationMessage(
                speaker="张客户",
                role="customer",
                body="x" * 600,
            ),
            NormalizedConversationMessage(speaker="融航-支持", role="ours", body="y" * 600),
    ]
    with pytest.raises(ConversationInputError, match="长度超过上限"):
        validate_conversation(
            long_messages,
            max_messages=settings.llm_max_conversation_messages,
            max_chars=settings.llm_max_conversation_chars,
            require_both_parties=True,
        )

    conversation = validate_conversation(
        sample_messages(),
        max_messages=settings.llm_max_conversation_messages,
        max_chars=settings.llm_max_conversation_chars,
        require_both_parties=True,
    )
    assert conversation.source_hash
    assert "张客户（客户）" in conversation.transcript
    assert "融航-李支持（我方）" in conversation.transcript


def llm_http_response(content: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"choices": [{"message": {"content": json.dumps(content)}}]},
        request=httpx.Request("POST", "https://llm.local/chat/completions"),
    )


@pytest.mark.asyncio
async def test_openai_compatible_provider_parses_structured_output() -> None:
    request_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_paths.append(request.url.path)
        return llm_http_response(
            {
                "candidates": [
                    {
                        "question": "登录失败怎么办？",
                        "response_content": "检查账号状态。",
                        "question_variants": ["登录失败怎么办？", "无法登录 ", "无法登录"],
                    }
                ],
                "non_candidates": [{"topic": "发票", "reason": "答案不确定"}],
            }
        )

    provider = OpenAiCompatibleLlmProvider(
        base_url="https://llm.local/v1",
        api_key="test-key",
        model="deepseek-chat",
        timeout_seconds=5,
        http_client=httpx.AsyncClient(
            base_url="https://llm.local/v1",
            transport=httpx.MockTransport(handler),
        ),
    )
    extraction = await provider.extract_knowledge_candidates("transcript")
    assert extraction.candidates[0].question == "登录失败怎么办？"
    assert extraction.candidates[0].question_variants == ["无法登录"]
    assert extraction.non_candidates[0].topic == "发票"
    assert request_paths == ["/v1/chat/completions"]


@pytest.mark.asyncio
async def test_openai_compatible_provider_maps_http_errors() -> None:
    provider = OpenAiCompatibleLlmProvider(
        base_url="https://llm.local/v1",
        api_key="test-key",
        model="deepseek-chat",
        timeout_seconds=5,
        http_client=httpx.AsyncClient(
            base_url="https://llm.local/v1",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    401,
                    json={"error": "bad key"},
                    request=httpx.Request("POST", "https://llm.local/v1/chat/completions"),
                )
            ),
        ),
    )
    with pytest.raises(LlmConfigurationError):
        await provider.extract_search_queries("transcript")

    invalid = OpenAiCompatibleLlmProvider(
        base_url="https://llm.local/v1",
        api_key="test-key",
        model="deepseek-chat",
        timeout_seconds=5,
        http_client=httpx.AsyncClient(
            base_url="https://llm.local/v1",
            transport=httpx.MockTransport(
                lambda request: llm_http_response({"queries": "not-a-list"})
            ),
        ),
    )
    with pytest.raises(LlmOutputError):
        await invalid.extract_search_queries("transcript")

    blank_required_content = OpenAiCompatibleLlmProvider(
        base_url="https://llm.local/v1",
        api_key="test-key",
        model="deepseek-chat",
        timeout_seconds=5,
        http_client=httpx.AsyncClient(
            base_url="https://llm.local/v1",
            transport=httpx.MockTransport(
                lambda request: llm_http_response(
                    {
                        "candidates": [
                            {"question": "   ", "response_content": "有效回复"}
                        ],
                        "non_candidates": [],
                    }
                )
            ),
        ),
    )
    malformed = await blank_required_content.extract_knowledge_candidates("transcript")
    assert malformed.candidates == []
    assert malformed.non_candidates[0].reason == "候选字段不完整或格式无效"


@pytest.mark.asyncio
async def test_openai_provider_requires_complete_relevance_decisions() -> None:
    candidates = [
        RelevanceCandidate(candidate_id="candidate-a", document="问题：登录失败"),
        RelevanceCandidate(candidate_id="candidate-b", document="问题：重置密码"),
    ]
    provider = OpenAiCompatibleLlmProvider(
        base_url="https://llm.local/v1",
        api_key="test-key",
        model="deepseek-chat",
        timeout_seconds=5,
        http_client=httpx.AsyncClient(
            base_url="https://llm.local/v1",
            transport=httpx.MockTransport(
                lambda request: llm_http_response(
                    {
                        "decisions": [
                            {"candidate_id": "candidate-a", "relevant": True},
                            {"candidate_id": "candidate-b", "relevant": False},
                        ]
                    }
                )
            ),
        ),
    )
    decisions = await provider.judge_search_relevance("登录失败怎么办？", candidates)
    assert [(item.candidate_id, item.relevant) for item in decisions] == [
        ("candidate-a", True),
        ("candidate-b", False),
    ]

    invalid = OpenAiCompatibleLlmProvider(
        base_url="https://llm.local/v1",
        api_key="test-key",
        model="deepseek-chat",
        timeout_seconds=5,
        http_client=httpx.AsyncClient(
            base_url="https://llm.local/v1",
            transport=httpx.MockTransport(
                lambda request: llm_http_response(
                    {"decisions": [{"candidate_id": "candidate-a", "relevant": True}]}
                )
            ),
        ),
    )
    with pytest.raises(LlmOutputError, match="覆盖全部候选"):
        await invalid.judge_search_relevance("登录失败怎么办？", candidates)


@pytest.mark.asyncio
async def test_openai_provider_keeps_valid_candidates_when_one_is_malformed() -> None:
    provider = OpenAiCompatibleLlmProvider(
        base_url="https://llm.local/v1",
        api_key="test-key",
        model="deepseek-chat",
        timeout_seconds=5,
        http_client=httpx.AsyncClient(
            base_url="https://llm.local/v1",
            transport=httpx.MockTransport(
                lambda request: llm_http_response(
                    {
                        "candidates": [
                            {
                                "question": "登录失败怎么办？",
                                "response_content": "检查账号状态。",
                            },
                            {"question": "   ", "response_content": "无效候选"},
                        ],
                        "non_candidates": [],
                    }
                )
            ),
        ),
    )

    extraction = await provider.extract_knowledge_candidates("transcript")

    assert [item.question for item in extraction.candidates] == ["登录失败怎么办？"]
    assert extraction.non_candidates[0].topic == "模型返回的候选"
    assert extraction.non_candidates[0].reason == "候选字段不完整或格式无效"


@pytest.mark.asyncio
async def test_search_persists_staged_scores_and_keeps_keyword_fallback_independent(
    tmp_path: Path,
) -> None:
    factory, engine, _settings = await build_db(tmp_path)
    try:
        async with factory() as session:
            user = await create_user(session)
            _parent, knowledge_base, _child, _revision = await create_published_graph(
                session,
                user.id,
            )

            high_confidence = await search_published_content(
                session,
                user_id=user.id,
                query="登录失败怎么办？",
                ocr_text=None,
                knowledge_base_id=knowledge_base.id,
                retrieval_mode="vector",
                limit=10,
            )
            result_item = high_confidence.groups[0][1][0][0]
            assert result_item.selection_stage == "hybrid"
            assert result_item.hybrid_score == 1.0
            assert result_item.rerank_score is None
            assert result_item.helpful_count_at_search == 0
            assert high_confidence.event.degraded is False

            index_fallback = await search_published_content(
                session,
                user_id=user.id,
                query="登录失败怎么办？",
                ocr_text=None,
                knowledge_base_id=knowledge_base.id,
                retrieval_mode="vector",
                limit=10,
                index_backend=FailingIndexBackend(),
            )
            index_fallback_item = index_fallback.groups[0][1][0][0]
            assert index_fallback_item.selection_stage == "hybrid"
            assert index_fallback.event.degradation_reasons == ["index_unavailable"]

            keyword_fallback = await search_published_content(
                session,
                user_id=user.id,
                query="登录",
                ocr_text=None,
                knowledge_base_id=knowledge_base.id,
                retrieval_mode="vector",
                limit=10,
                index_backend=EmptyIndexBackend(),
            )
            fallback_item = keyword_fallback.groups[0][1][0][0]
            assert fallback_item.selection_stage == "keyword_fallback"
            assert fallback_item.match_reason == "parent_keyword_fallback"
            assert keyword_fallback.event.degraded is False
            assert keyword_fallback.event.degradation_reasons is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ingestion_batch_creates_drafts_and_purges_raw_input(tmp_path: Path) -> None:
    factory, engine, settings = await build_db(tmp_path)
    try:
        async with factory() as session:
            user = await create_user(session)
            batch = await create_ingestion_batch(
                session,
                owner_user_id=user.id,
                messages=sample_messages(),
                settings=settings,
            )
            await session.commit()
            assert batch.status == IntelligentIngestionBatchStatus.PROCESSING
            assert batch.normalized_messages is not None

        result = await run_ingestion_worker_once(
            factory,
            provider=FakeExtractionProvider(),
            worker_id="test-worker",
            lease_seconds=60,
        )
        assert result is not None
        assert result.status == IntelligentIngestionBatchStatus.COMPLETED_WITH_WARNINGS
        assert result.generated_count == 1

        async with factory() as session:
            batch = await session.get(IntelligentIngestionBatch, result.batch_id)
            assert batch is not None
            assert batch.normalized_messages is None
            assert batch.generated_count == 1
            assert batch.rejected_count == 1
            assert batch.rejection_reasons[0]["topic"] == "发票开具"
            draft = await session.scalar(select(KnowledgeDraft))
            assert draft is not None
            assert draft.source == KnowledgeDraftSource.INTELLIGENT_GENERATED
            assert draft.question == "登录失败怎么办？"
            assert draft.question_variants == ["无法登录如何处理？"]
            assert draft.parent_id is None
            assert draft.model_version == "fake-llm"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ingestion_batch_deduplicates_replayed_candidates(tmp_path: Path) -> None:
    factory, engine, settings = await build_db(tmp_path)
    try:
        async with factory() as session:
            user = await create_user(session)
            batch = await create_ingestion_batch(
                session,
                owner_user_id=user.id,
                messages=sample_messages(),
                settings=settings,
            )
            first = await process_ingestion_batch(
                session,
                batch=batch,
                provider=FakeExtractionProvider(),
            )
            await session.commit()
            assert first.generated_count == 1

            # Simulate recovery from an ambiguous worker completion: the same
            # durable input becomes available again after its draft write.
            batch.status = IntelligentIngestionBatchStatus.PROCESSING
            batch.completed_at = None
            batch.normalized_messages = [
                message.model_dump(mode="json") for message in sample_messages()
            ]
            replayed = await process_ingestion_batch(
                session,
                batch=batch,
                provider=FakeExtractionProvider(),
            )
            await session.commit()

            drafts = list(
                (
                    await session.scalars(
                        select(KnowledgeDraft).where(KnowledgeDraft.ingestion_batch_id == batch.id)
                    )
                ).all()
            )
            assert replayed.generated_count == 1
            assert len(drafts) == 1
            assert drafts[0].candidate_fingerprint is not None
            assert len(drafts[0].candidate_fingerprint) == 64
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ingestion_batch_retries_transient_errors_and_fails_when_exhausted(
    tmp_path: Path,
) -> None:
    factory, engine, settings = await build_db(tmp_path)
    try:
        async with factory() as session:
            user = await create_user(session)
            batch = await create_ingestion_batch(
                session,
                owner_user_id=user.id,
                messages=sample_messages(),
                settings=settings,
            )
            batch.max_attempts = 2
            await session.commit()

        first = await run_ingestion_worker_once(
            factory,
            provider=FailingProvider(LlmProviderError("upstream unavailable")),
            worker_id="test-worker",
            lease_seconds=60,
        )
        assert first is not None
        assert first.status == IntelligentIngestionBatchStatus.PROCESSING

        async with factory() as session:
            batch = await session.get(IntelligentIngestionBatch, first.batch_id)
            assert batch is not None
            assert batch.attempt_count == 1
            available_at = (
                batch.available_at.replace(tzinfo=UTC)
                if batch.available_at.tzinfo is None
                else batch.available_at
            )
            assert available_at > datetime.now(UTC)
            assert batch.normalized_messages is not None
            batch.available_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        second = await run_ingestion_worker_once(
            factory,
            provider=FailingProvider(LlmProviderError("upstream unavailable")),
            worker_id="test-worker",
            lease_seconds=60,
        )
        assert second is not None
        assert second.status == IntelligentIngestionBatchStatus.FAILED

        async with factory() as session:
            batch = await session.get(IntelligentIngestionBatch, second.batch_id)
            assert batch is not None
            assert batch.normalized_messages is None
            assert batch.last_error == "智能处理服务暂时不可用，请稍后重试"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_raw_input_is_physically_purged(tmp_path: Path) -> None:
    factory, engine, settings = await build_db(tmp_path)
    try:
        async with factory() as session:
            user = await create_user(session)
            batch = await create_ingestion_batch(
                session,
                owner_user_id=user.id,
                messages=sample_messages(),
                settings=settings,
            )
            batch.raw_input_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

            purged = await purge_expired_ingestion_raw_input(session)
            await session.commit()
            assert purged == 1
            assert batch.normalized_messages is None
            assert batch.status == IntelligentIngestionBatchStatus.FAILED
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ingestion_worker_purges_expired_raw_input_without_llm_provider(
    tmp_path: Path,
) -> None:
    factory, engine, settings = await build_db(tmp_path)
    try:
        async with factory() as session:
            user = await create_user(session)
            batch = await create_ingestion_batch(
                session,
                owner_user_id=user.id,
                messages=sample_messages(),
                settings=settings,
            )
            batch.raw_input_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        result = await run_ingestion_worker_once(
            factory,
            provider=None,
            worker_id="test-worker",
            lease_seconds=60,
        )

        assert result is None
        async with factory() as session:
            persisted = await session.get(IntelligentIngestionBatch, batch.id)
            assert persisted is not None
            assert persisted.status == IntelligentIngestionBatchStatus.FAILED
            assert persisted.normalized_messages is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_manual_draft_lifecycle_and_submission(tmp_path: Path) -> None:
    factory, engine, settings = await build_db(tmp_path)
    try:
        async with factory() as session:
            user = await create_user(session)
            parent, knowledge_base, _primary_child, _revision = await create_published_graph(
                session, user.id
            )
            draft = await create_manual_draft(
                session,
                owner_user_id=user.id,
                content=KnowledgeDraftInput(question="登录失败怎么办？"),
            )
            await session.commit()
            assert draft.source == KnowledgeDraftSource.MANUAL_SAVED

            draft = await update_draft(
                session,
                owner_user_id=user.id,
                draft_id=draft.id,
                content=KnowledgeDraftInput(
                    parent_id=parent.id,
                    question="登录失败怎么办？",
                    response_content="请先检查账号状态，再联系管理员重置。",
                    knowledge_base_ids=[knowledge_base.id],
                ),
            )
            await session.commit()

            submission = await submit_draft(
                session,
                owner_user_id=user.id,
                draft_id=draft.id,
            )
            await session.commit()
            assert submission.submission.parent_id == parent.id
            assert submission.submission.child_id is not None
            assert submission.submission.status == ReviewSubmissionStatus.PENDING_REVIEW

            remaining = await session.scalar(
                select(KnowledgeDraft).where(KnowledgeDraft.id == draft.id)
            )
            assert remaining is None

            incomplete = await create_manual_draft(
                session,
                owner_user_id=user.id,
                content=KnowledgeDraftInput(question="只有问题的草稿"),
            )
            await session.commit()
            with pytest.raises(DraftNotSubmittableError):
                await submit_draft(
                    session,
                    owner_user_id=user.id,
                    draft_id=incomplete.id,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_conversation_assisted_search_merges_and_deduplicates(tmp_path: Path) -> None:
    factory, engine, settings = await build_db(tmp_path)
    try:
        async with factory() as session:
            user = await create_user(session)
            parent, knowledge_base, primary_child, primary_revision = await create_published_graph(
                session, user.id
            )

            ordinary_child = Child(
                parent_id=parent.id,
                is_primary=False,
                created_by_user_id=user.id,
            )
            session.add(ordinary_child)
            await session.flush()
            ordinary_revision = ChildRevision(
                child_id=ordinary_child.id,
                revision_number=1,
                question="发票开具流程是什么？",
                response_content="请联系财务邮箱申请开具发票。",
                created_by_user_id=user.id,
            )
            session.add(ordinary_revision)
            await session.flush()
            submission = ReviewSubmission(
                submission_kind=ReviewSubmissionKind.CHILD,
                status=ReviewSubmissionStatus.PUBLISHED,
                parent_id=parent.id,
                child_id=ordinary_child.id,
                child_revision_id=ordinary_revision.id,
                submitted_by_user_id=user.id,
            )
            session.add(submission)
            session.add(
                ChildKnowledgeBasePublication(
                    child_id=ordinary_child.id,
                    knowledge_base_id=knowledge_base.id,
                    status=ChildPublicationStatus.PUBLISHED,
                    active_revision_id=ordinary_revision.id,
                )
            )
            await session.commit()

            details = await conversation_assisted_search(
                session,
                user_id=user.id,
                messages=sample_messages(),
                knowledge_base_id=None,
                limit=10,
                settings=settings,
                index_backend=None,
                provider=FakeExtractionProvider(),
            )
            assert details.queries == ["登录失败如何处理？", "发票开具流程"]
            all_items = [item for _parent, items in details.groups for item in items]
            keys = {
                (item.candidate.child_revision.id, item.candidate.knowledge_base.id)
                for item in all_items
            }
            assert len(keys) == len(all_items)
            assert (
                primary_revision.id,
                knowledge_base.id,
            ) in keys
            assert (
                ordinary_revision.id,
                knowledge_base.id,
            ) in keys
            for item in all_items:
                assert item.matched_queries
                assert set(item.matched_queries) <= set(details.queries)
            assert primary_child.id  # keep reference meaningful
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ingestion_and_draft_api_routes_require_llm_configuration(
    tmp_path: Path,
) -> None:
    initial_password_file = tmp_path / "initial-password.txt"
    initial_password_file.write_text("InitialPassword-123!", encoding="utf-8")
    settings = make_settings(tmp_path)
    settings = settings.model_copy(
        update={
            "initial_admin_username": "bootstrap-admin",
            "initial_admin_password_file": initial_password_file,
        }
    )
    engine = create_async_engine(settings.database_url_with_password)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(settings=settings, db_session_factory=factory)
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="https://testserver") as client:
                assert (await client.get("/api/v1/auth/csrf")).status_code == 204
                login = await client.post(
                    "/api/v1/auth/login",
                    headers={"X-CSRF-Token": client.cookies[settings.pre_auth_csrf_cookie_name]},
                    json={"username": "bootstrap-admin", "password": "InitialPassword-123!"},
                )
                assert login.status_code == 200
                changed_password = await client.post(
                    "/api/v1/auth/change-password",
                    headers={"X-CSRF-Token": client.cookies[settings.csrf_cookie_name]},
                    json={
                        "current_password": "InitialPassword-123!",
                        "new_password": "ChangedPassword-123!",
                    },
                )
                assert changed_password.status_code == 200
                csrf = {"X-CSRF-Token": client.cookies[settings.csrf_cookie_name]}

                batch_response = await client.post(
                    "/api/v1/intelligent-ingestion/batches",
                    headers=csrf,
                    json={"messages": [{"speaker": "x", "role": "customer", "body": "y"}]},
                )
                assert batch_response.status_code == 503

                app.state.llm_provider = FakeExtractionProvider()
                assisted_search = await client.post(
                    "/api/v1/search/conversation-assist",
                    headers=csrf,
                    json={
                        "messages": [
                            {"speaker": "张客户", "role": "customer", "body": "发票怎么开？"},
                            {
                                "speaker": "融航-李支持",
                                "role": "ours",
                                "body": "我需要查询后再回复。",
                            },
                        ]
                    },
                )
                assert assisted_search.status_code == 200
                assert assisted_search.json()["queries"] == ["登录失败如何处理？", "发票开具流程"]

                app.state.llm_provider = FailingProvider(LlmProviderError("upstream unavailable"))
                unavailable_search = await client.post(
                    "/api/v1/search/conversation-assist",
                    headers=csrf,
                    json={
                        "messages": [
                            {"speaker": "张客户", "role": "customer", "body": "发票怎么开？"}
                        ]
                    },
                )
                assert unavailable_search.status_code == 503
                assert unavailable_search.json()["detail"] == "智能处理服务暂时不可用，请稍后重试"

                draft_response = await client.post(
                    "/api/v1/knowledge-content/drafts",
                    headers=csrf,
                    json={"question": "登录失败怎么办？"},
                )
                assert draft_response.status_code == 201
                draft_id = draft_response.json()["id"]
                assert draft_response.json()["source"] == "manual_saved"

                drafts = await client.get("/api/v1/knowledge-content/drafts")
                assert drafts.status_code == 200
                assert len(drafts.json()) == 1

                deleted = await client.delete(
                    f"/api/v1/knowledge-content/drafts/{draft_id}",
                    headers=csrf,
                )
                assert deleted.status_code == 204
    finally:
        await engine.dispose()
