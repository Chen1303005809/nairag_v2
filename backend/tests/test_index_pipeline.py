from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

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
from app.services.index_backend import (
    RESPONSE_CHUNK_OVERLAP,
    VECTOR_DIMENSION,
    LocalArtifactIndexBackend,
    build_index_fragments,
    deterministic_hash_vector,
    stable_source_item_id,
)
from app.services.index_jobs import claim_next_index_job, run_next_index_job
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
