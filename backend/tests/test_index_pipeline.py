from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # Register model metadata.
from app.core.config import Settings
from app.db.base import Base
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_content import (
    Child,
    ChildKnowledgeBasePublication,
    ChildPublicationStatus,
    ChildRevision,
    ChildRevisionQuestionVariant,
    IndexJob,
    IndexJobKind,
    IndexJobStatus,
    Parent,
    ParentRevision,
    ReviewSubmission,
    ReviewSubmissionKind,
    ReviewSubmissionStatus,
    ReviewSubmissionTarget,
    ReviewTargetStatus,
)
from app.models.user_account import UserAccount, UserRole
from app.services.embedding import (
    DeterministicEmbeddingProvider,
    EmbeddingProviderError,
    QwenEmbeddingProvider,
)
from app.services.index_backend import (
    RESPONSE_CHUNK_OVERLAP,
    VECTOR_DIMENSION,
    LocalArtifactIndexBackend,
    MilvusHttpWriter,
    MilvusIndexBackend,
    build_index_fragments,
    deterministic_hash_vector,
    stable_source_item_id,
)
from app.services.index_jobs import claim_next_index_job, run_next_index_job
from app.services.retrieval import (
    IndexQuery,
    LocalArtifactSearchBackend,
    MilvusClientHybridSearcher,
    MilvusSearchBackend,
)
from app.worker import run_worker


async def build_index_db(tmp_path: Path) -> tuple[async_sessionmaker[AsyncSession], object]:
    database_path = tmp_path / "index.sqlite3"
    settings = Settings(
        app_environment="test",
        database_url=f"sqlite+aiosqlite:///{database_path}",
        jwt_secret="test-signing-key-that-is-long-enough",
        cookie_secure=False,
    )
    engine = create_async_engine(settings.database_url_with_password)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def create_index_graph(
    session: AsyncSession,
    *,
    target_count: int = 1,
    parent_kind: bool = False,
    response_content: str = "请联系管理员。",
) -> tuple[ChildRevision, ReviewSubmission, list[KnowledgeBase], list[IndexJob]]:
    user = UserAccount(
        username=f"u-{uuid4().hex[:12]}",
        display_name="Test User",
        password_hash="x" * 32,
        role=UserRole.NORMAL_USER,
        must_change_password=False,
    )
    session.add(user)
    await session.flush()

    parent = Parent(created_by_user_id=user.id)
    session.add(parent)
    await session.flush()
    parent_revision = ParentRevision(
        parent_id=parent.id,
        revision_number=1,
        name="账号问题",
        canonical_keyword="账号",
        created_by_user_id=user.id,
    )
    child = Child(parent_id=parent.id, is_primary=parent_kind, created_by_user_id=user.id)
    session.add_all([parent_revision, child])
    await session.flush()
    child_revision = ChildRevision(
        child_id=child.id,
        revision_number=1,
        question="无法登录怎么办？",
        response_content=response_content,
        created_by_user_id=user.id,
    )
    session.add(child_revision)
    await session.flush()
    session.add(
        ChildRevisionQuestionVariant(
            child_revision_id=child_revision.id,
            question_text="登录失败如何处理？",
            sort_order=0,
        )
    )

    knowledge_bases: list[KnowledgeBase] = []
    for index in range(target_count):
        knowledge_base = KnowledgeBase(
            logical_key=f"kb-{uuid4().hex[:12]}",
            name=f"知识库 {index + 1}",
            current_physical_collection_name=f"collection-{uuid4().hex}",
            created_by_user_id=user.id,
        )
        session.add(knowledge_base)
        knowledge_bases.append(knowledge_base)
    await session.flush()

    submission = ReviewSubmission(
        submission_kind=(
            ReviewSubmissionKind.PARENT_WITH_PRIMARY
            if parent_kind
            else ReviewSubmissionKind.CHILD
        ),
        status=ReviewSubmissionStatus.PENDING_REVIEW,
        parent_id=parent.id,
        parent_revision_id=parent_revision.id if parent_kind else None,
        child_id=child.id,
        child_revision_id=child_revision.id,
        submitted_by_user_id=user.id,
    )
    session.add(submission)
    await session.flush()

    jobs: list[IndexJob] = []
    for knowledge_base in knowledge_bases:
        session.add(
            ReviewSubmissionTarget(
                review_submission_id=submission.id,
                knowledge_base_id=knowledge_base.id,
                status=ReviewTargetStatus.APPROVED,
            )
        )
        session.add(
            ChildKnowledgeBasePublication(
                child_id=child.id,
                knowledge_base_id=knowledge_base.id,
                status=ChildPublicationStatus.PENDING,
                pending_submission_id=submission.id,
            )
        )
        job = IndexJob(
            job_kind=IndexJobKind.INDEX_TARGET,
            status=IndexJobStatus.PENDING,
            idempotency_key=f"test:{submission.id}:{knowledge_base.id}",
            review_submission_id=submission.id,
            knowledge_base_id=knowledge_base.id,
            child_id=child.id,
            child_revision_id=child_revision.id,
            available_at=datetime.now(UTC),
            max_attempts=3,
        )
        session.add(job)
        jobs.append(job)
    await session.commit()
    return child_revision, submission, knowledge_bases, jobs


def test_deterministic_vector_and_source_id_are_stable() -> None:
    revision_id = uuid4()
    first = stable_source_item_id(
        child_revision_id=revision_id,
        field_type="response_content",
        ordinal=0,
        field_text="相同内容",
    )
    second = stable_source_item_id(
        child_revision_id=revision_id,
        field_type="response_content",
        ordinal=0,
        field_text="相同内容",
    )
    assert first == second
    assert first != stable_source_item_id(
        child_revision_id=revision_id,
        field_type="response_content",
        ordinal=1,
        field_text="相同内容",
    )
    vector = deterministic_hash_vector("登录失败")
    assert len(vector) == VECTOR_DIMENSION
    assert sum(item * item for item in vector) == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_long_response_chunks_overlap_and_artifact_is_rebuildable(tmp_path: Path) -> None:
    factory, engine = await build_index_db(tmp_path)
    try:
        long_response = "登录失败后请先检查密码。" * 400
        async with factory() as session:
            revision, _submission, knowledge_bases, jobs = await create_index_graph(
                session,
                response_content=long_response,
            )
            fragments = await build_index_fragments(session, child_revision_id=revision.id)
            response_fragments = [
                item for item in fragments if item.field_type == "response_content"
            ]
            assert len(response_fragments) > 1
            assert response_fragments[0].field_text[-RESPONSE_CHUNK_OVERLAP:] == (
                response_fragments[1].field_text[:RESPONSE_CHUNK_OVERLAP]
            )
            assert all(len(item.dense_vector) == VECTOR_DIMENSION for item in fragments)

            artifact_dir = tmp_path / "artifacts"
            await LocalArtifactIndexBackend(artifact_dir).index_target(session, jobs[0])
            artifact_path = artifact_dir / str(knowledge_bases[0].id) / f"{revision.id}.json"
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            assert payload["child_revision_id"] == str(revision.id)
            assert [item["source_item_id"] for item in payload["fragments"]] == [
                item.source_item_id for item in fragments
            ]
            assert not list(artifact_path.parent.glob("*.tmp"))
    finally:
        await engine.dispose()


class FailingBackend:
    async def index_target(self, session: AsyncSession, job: IndexJob) -> None:
        raise RuntimeError("backend unavailable")


@pytest.mark.asyncio
async def test_backend_failure_is_retried_and_success_publishes(tmp_path: Path) -> None:
    factory, engine = await build_index_db(tmp_path)
    try:
        async with factory() as session:
            revision, submission, knowledge_bases, jobs = await create_index_graph(session)
            failed = await run_next_index_job(
                session,
                worker_id="worker-a",
                backend=FailingBackend(),
            )
            assert failed is not None
            assert failed.status == IndexJobStatus.PENDING
            job = await session.get(IndexJob, jobs[0].id)
            assert job is not None
            assert job.attempt_count == 1
            target = await session.scalar(
                select(ReviewSubmissionTarget).where(
                    ReviewSubmissionTarget.review_submission_id == submission.id,
                    ReviewSubmissionTarget.knowledge_base_id == knowledge_bases[0].id,
                )
            )
            assert target is not None
            assert target.status == ReviewTargetStatus.INDEX_FAILED

            job.available_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.flush()
            succeeded = await run_next_index_job(
                session,
                worker_id="worker-a",
                backend=LocalArtifactIndexBackend(tmp_path / "artifacts"),
            )
            assert succeeded is not None
            assert succeeded.status == IndexJobStatus.SUCCEEDED
            publication = await session.scalar(
                select(ChildKnowledgeBasePublication).where(
                    ChildKnowledgeBasePublication.child_id == job.child_id,
                    ChildKnowledgeBasePublication.knowledge_base_id == job.knowledge_base_id,
                )
            )
            assert publication is not None
            assert publication.status == ChildPublicationStatus.PUBLISHED
            assert publication.active_revision_id == revision.id
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_by_another_worker(tmp_path: Path) -> None:
    factory, engine = await build_index_db(tmp_path)
    try:
        async with factory() as session:
            _revision, _submission, _knowledge_bases, jobs = await create_index_graph(session)
            first = await claim_next_index_job(session, worker_id="worker-a", lease_seconds=60)
            assert first is not None
            first.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
            second = await claim_next_index_job(session, worker_id="worker-b", lease_seconds=60)
            assert second is not None
            assert second.id == jobs[0].id
            assert second.attempt_count == 2
            assert second.lease_owner == "worker-b"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_parent_publishes_only_after_last_target_index_succeeds(tmp_path: Path) -> None:
    factory, engine = await build_index_db(tmp_path)
    try:
        async with factory() as session:
            _revision, submission, knowledge_bases, jobs = await create_index_graph(
                session,
                target_count=2,
                parent_kind=True,
            )
            backend = LocalArtifactIndexBackend(tmp_path / "artifacts")
            first = await run_next_index_job(session, worker_id="worker-a", backend=backend)
            assert first is not None
            assert first.status == IndexJobStatus.SUCCEEDED
            publications = list(
                (
                    await session.scalars(
                        select(ChildKnowledgeBasePublication).where(
                            ChildKnowledgeBasePublication.child_id == jobs[0].child_id
                        )
                    )
                ).all()
            )
            assert {item.status for item in publications} == {ChildPublicationStatus.PENDING}

            second = await run_next_index_job(session, worker_id="worker-a", backend=backend)
            assert second is not None
            assert second.status == IndexJobStatus.SUCCEEDED
            publications = list(
                (
                    await session.scalars(
                        select(ChildKnowledgeBasePublication).where(
                            ChildKnowledgeBasePublication.child_id == jobs[0].child_id
                        )
                    )
                ).all()
            )
            assert {item.status for item in publications} == {ChildPublicationStatus.PUBLISHED}
            refreshed_submission = await session.get(ReviewSubmission, submission.id)
            assert refreshed_submission is not None
            assert refreshed_submission.status == ReviewSubmissionStatus.PUBLISHED
            assert {item.knowledge_base_id for item in publications} == {
                item.id for item in knowledge_bases
            }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_loop_processes_job_and_stops_gracefully(tmp_path: Path) -> None:
    factory, engine = await build_index_db(tmp_path)
    try:
        async with factory() as session:
            await create_index_graph(session)
        settings = Settings(
            app_environment="test",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'index.sqlite3'}",
            jwt_secret="test-signing-key-that-is-long-enough",
            cookie_secure=False,
            index_artifact_dir=tmp_path / "artifacts",
            worker_poll_interval_seconds=0.01,
            worker_lease_seconds=60,
            worker_id="test-worker",
        )
        stop_event = asyncio.Event()
        results = []

        async def stop_after_result(result) -> None:
            results.append(result)
            stop_event.set()

        await run_worker(
            settings=settings,
            session_factory=factory,
            backend=LocalArtifactIndexBackend(settings.index_artifact_dir),
            stop_event=stop_event,
            on_result=stop_after_result,
        )
        assert len(results) == 1
        assert results[0].status == IndexJobStatus.SUCCEEDED
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_qwen_embedding_provider_orders_and_validates_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = QwenEmbeddingProvider(
            "https://embedding.test/v1",
            model_name="Qwen/Qwen3-Embedding-0.6B",
            dimension=2,
            client=client,
        )
        assert await provider.embed_texts(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    finally:
        await client.aclose()

    def invalid_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    invalid_client = httpx.AsyncClient(transport=httpx.MockTransport(invalid_handler))
    try:
        provider = QwenEmbeddingProvider(
            "https://embedding.test/v1",
            dimension=2,
            client=invalid_client,
        )
        with pytest.raises(EmbeddingProviderError):
            await provider.embed_texts(["a"])
    finally:
        await invalid_client.aclose()


@pytest.mark.asyncio
async def test_qwen_embedding_provider_supports_legacy_embedding_responses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        payload = request.read()
        body = json.loads(payload)
        if "input" in body:
            return httpx.Response(200, json={"embedding": [1.0, 0.0]})
        return httpx.Response(
            200,
            json={"embedding": [1.0, 0.0] if body["prompt"] == "a" else [0.0, 1.0]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = QwenEmbeddingProvider(
            "http://embedding.test/v1",
            model_name="qwen3-embedding:0.6b",
            dimension=2,
            client=client,
        )
        assert await provider.embed_texts(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_qwen_embedding_provider_unwraps_array_embedding_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embedding": [[1.0, 0.0]]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = QwenEmbeddingProvider(
            "http://embedding.test/v1",
            model_name="qwen3-embedding:0.6b",
            dimension=2,
            client=client,
        )
        assert await provider.embed_texts(["a"]) == [[1.0, 0.0]]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_qwen_embedding_provider_retries_empty_legacy_response_with_prompt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "input" in body:
            return httpx.Response(200, json={"embedding": []})
        assert body == {"model": "qwen3-embedding:0.6b", "prompt": "a"}
        return httpx.Response(200, json={"embedding": [1.0, 0.0]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = QwenEmbeddingProvider(
            "http://embedding.test/v1",
            model_name="qwen3-embedding:0.6b",
            dimension=2,
            client=client,
        )
        assert await provider.embed_texts(["a"]) == [[1.0, 0.0]]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_qwen_embedding_provider_uses_ollama_native_embed_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        assert json.loads(request.content) == {
            "model": "qwen3-embedding:0.6b",
            "input": ["a", "b"],
        }
        return httpx.Response(
            200,
            json={"embeddings": [[1.0, 0.0], [0.0, 1.0]]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        provider = QwenEmbeddingProvider(
            "http://embedding.test/api",
            model_name="qwen3-embedding:0.6b",
            dimension=2,
            client=client,
        )
        assert await provider.embed_texts(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_local_artifact_search_uses_hybrid_scores(tmp_path: Path) -> None:
    factory, engine = await build_index_db(tmp_path)
    try:
        async with factory() as session:
            revision, _submission, knowledge_bases, jobs = await create_index_graph(
                session,
                response_content="请检查账号密码并联系管理员。",
            )
            await LocalArtifactIndexBackend(tmp_path / "artifacts").index_target(session, jobs[0])
            backend = LocalArtifactSearchBackend(tmp_path / "artifacts")
            hits = await backend.search(
                knowledge_base_id=knowledge_bases[0].id,
                queries=[IndexQuery(text="账号密码", channel="text", weight=1.0)],
                limit=10,
            )
            assert hits
            assert hits[0].child_revision_id == revision.id
            assert hits[0].dense_score >= 0
            assert hits[0].sparse_score > 0
            assert hits[0].match_reason == "hybrid_dense_bm25"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_milvus_backend_upserts_current_collection_with_stable_rows(tmp_path: Path) -> None:
    factory, engine = await build_index_db(tmp_path)
    captured: dict[str, object] = {}

    class CapturingWriter:
        async def ensure_collection(self, *, collection_name: str) -> None:
            captured["ensured_collection_name"] = collection_name

        async def upsert(self, *, collection_name: str, rows: list[dict[str, object]]) -> None:
            captured["collection_name"] = collection_name
            captured["rows"] = rows

    try:
        async with factory() as session:
            revision, _submission, knowledge_bases, jobs = await create_index_graph(session)
            backend = MilvusIndexBackend(
                writer=CapturingWriter(),
                embedding_provider=DeterministicEmbeddingProvider(),
            )
            await backend.index_target(session, jobs[0])
            assert (
                captured["collection_name"]
                == knowledge_bases[0].current_physical_collection_name
            )
            assert (
                captured["ensured_collection_name"]
                == knowledge_bases[0].current_physical_collection_name
            )
            rows = captured["rows"]
            assert isinstance(rows, list)
            assert rows
            assert rows[0]["child_revision_id"] == str(revision.id)
            assert rows[0]["source_item_id"]
            assert len(rows[0]["dense_vector"]) == VECTOR_DIMENSION
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_milvus_http_writer_sends_idempotent_upsert_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/vectordb/entities/upsert"
        assert request.headers["Authorization"] == "Bearer milvus-token"
        body = json.loads(request.content)
        assert body["collectionName"] == "nairag_support_g1"
        assert body["data"][0]["source_item_id"] == "source-1"
        return httpx.Response(200, json={"code": 0, "data": {"upsertCount": 1}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        writer = MilvusHttpWriter(
            "https://milvus.test",
            token="milvus-token",
            client=client,
        )
        await writer.upsert(
            collection_name="nairag_support_g1",
            rows=[{"source_item_id": "source-1"}],
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_milvus_http_writer_ensures_fixed_collection_schema() -> None:
    collection_exists = False
    requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal collection_exists
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path == "/v2/vectordb/collections/has":
            return httpx.Response(200, json={"code": 0, "data": {"has": collection_exists}})
        if request.url.path == "/v2/vectordb/collections/create":
            collection_exists = True
            schema = body["schema"]
            fields = {field["fieldName"]: field for field in schema["fields"]}
            assert schema["enabledDynamicField"] is False
            assert fields["source_item_id"]["dataType"] == "VarChar"
            assert fields["source_item_id"]["isPrimary"] is True
            assert fields["dense_vector"]["elementTypeParams"]["dim"] == VECTOR_DIMENSION
            assert fields["field_text"]["elementTypeParams"]["enable_analyzer"] is True
            assert fields["sparse_vector"]["dataType"] == "SparseFloatVector"
            assert fields["sparse_terms"]["dataType"] == "JSON"
            assert schema["functions"] == [
                {
                    "name": "field_text_bm25",
                    "type": "BM25",
                    "inputFieldNames": ["field_text"],
                    "outputFieldNames": ["sparse_vector"],
                    "params": {},
                }
            ]
            index_params = {item["fieldName"]: item for item in body["indexParams"]}
            assert index_params["dense_vector"]["metricType"] == "COSINE"
            assert index_params["sparse_vector"]["metricType"] == "BM25"
            return httpx.Response(200, json={"code": 0, "data": {}})
        raise AssertionError(f"unexpected Milvus endpoint: {request.url.path}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        writer = MilvusHttpWriter("https://milvus.test", client=client)
        await writer.ensure_collection(collection_name="nairag_support_g1")
        await writer.ensure_collection(collection_name="nairag_support_g1")
        assert [path for path, _body in requests] == [
            "/v2/vectordb/collections/has",
            "/v2/vectordb/collections/create",
            "/v2/vectordb/collections/has",
        ]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_milvus_search_backend_maps_and_clamps_client_hits() -> None:
    revision_id = uuid4()
    knowledge_base_id = uuid4()

    class CapturingSearcher:
        async def hybrid_search(
            self,
            *,
            collection_name: str,
            queries: list[dict[str, object]],
            limit: int,
        ) -> list[dict[str, object]]:
            assert collection_name == "nairag_support_g1"
            assert len(queries) == 1
            assert len(queries[0]["dense_vector"]) == VECTOR_DIMENSION
            assert limit == 4
            return [
                {
                    "entity": {
                        "source_item_id": "source-1",
                        "child_revision_id": str(revision_id),
                        "field_type": "question",
                    },
                    "score": 1.7,
                    "dense_score": 0.9,
                    "sparse_score": 0.8,
                }
            ]

    backend = MilvusSearchBackend(
        searcher=CapturingSearcher(),
        embedding_provider=DeterministicEmbeddingProvider(),
    )
    hits = await backend.search(
        knowledge_base_id=knowledge_base_id,
        collection_name="nairag_support_g1",
        queries=[IndexQuery(text="账号", channel="text", weight=1.0)],
        limit=4,
    )
    assert len(hits) == 1
    assert hits[0].child_revision_id == revision_id
    assert hits[0].score == 1.0
    assert hits[0].dense_score == 0.9


@pytest.mark.asyncio
async def test_milvus_client_hybrid_searcher_loads_and_searches_one_query_at_a_time() -> None:
    class FakeAnnSearchRequest:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

    class FakeMilvusClient:
        def __init__(self) -> None:
            self.loaded_collections: list[str] = []
            self.calls: list[dict[str, object]] = []

        def load_collection(self, *, collection_name: str) -> None:
            self.loaded_collections.append(collection_name)

        def hybrid_search(self, **kwargs: object) -> list[list[dict[str, object]]]:
            self.calls.append(kwargs)
            return [
                [
                    {
                        "id": "source-1",
                        "distance": 0.9,
                        "entity": {
                            "child_revision_id": str(uuid4()),
                            "field_type": "question",
                        },
                    }
                ]
            ]

    client = FakeMilvusClient()
    searcher = MilvusClientHybridSearcher(
        "https://milvus.test",
        client=client,
        ann_search_request_factory=FakeAnnSearchRequest,
        ranker_factory=lambda dense, sparse: (dense, sparse),
    )
    result = await searcher.hybrid_search(
        collection_name="nairag_support_g1",
        queries=[
            {"text": "账号", "channel": "text", "weight": 1.0, "dense_vector": [0.1]},
            {"text": "登录", "channel": "ocr", "weight": 0.35, "dense_vector": [0.2]},
        ],
        limit=2,
    )

    assert client.loaded_collections == ["nairag_support_g1"]
    assert len(client.calls) == 2
    assert all(len(call["reqs"]) == 2 for call in client.calls)
    assert all(call["limit"] == 2 for call in client.calls)
    assert all(call["output_fields"] == [
        "source_item_id",
        "child_id",
        "child_revision_id",
        "field_type",
        "field_text",
        "embedding_model",
    ] for call in client.calls)
    assert [request.data for request in client.calls[0]["reqs"]] == [[[0.1]], ["账号"]]
    assert [request.data for request in client.calls[1]["reqs"]] == [[[0.2]], ["登录"]]
    assert result[0]["entity"]["source_item_id"] == "source-1"
    assert result[0]["channel"] == "text"
    assert result[1]["channel"] == "ocr"
