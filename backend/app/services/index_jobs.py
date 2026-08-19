from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.knowledge_content import (
    IndexJob,
    IndexJobKind,
    IndexJobStatus,
    ReviewSubmission,
    ReviewSubmissionKind,
    ReviewSubmissionTarget,
    ReviewTargetStatus,
)


class IndexJobNotFoundError(Exception):
    pass


class IndexJobLeaseError(Exception):
    pass


class IndexJobStateError(Exception):
    pass


class IndexTargetFieldsError(Exception):
    pass


@dataclass(frozen=True)
class IndexWorkerResult:
    job_id: UUID
    status: IndexJobStatus
    error: str | None = None


class IndexBackend(Protocol):
    """Adapter seam for Milvus/embedding implementations.

    The first runnable backend is deliberately a no-op.  It lets the durable
    state machine be exercised in local development while a later deployment
    supplies the real embedding and Milvus adapter.
    """

    async def index_target(self, session: AsyncSession, job: IndexJob) -> None:
        ...


class NoopIndexBackend:
    async def index_target(self, session: AsyncSession, job: IndexJob) -> None:
        return None


def _index_job_key(submission_id: UUID, knowledge_base_id: UUID, revision_id: UUID) -> str:
    return f"index-target:{submission_id}:{knowledge_base_id}:{revision_id}"


async def enqueue_index_jobs_for_submission(
    session: AsyncSession,
    *,
    review_submission_id: UUID,
) -> list[IndexJob]:
    """Create one idempotent target job for every currently approved target."""

    submission = await session.get(ReviewSubmission, review_submission_id)
    if submission is None:
        raise IndexJobNotFoundError(review_submission_id)
    if submission.child_revision_id is None:
        raise IndexTargetFieldsError(review_submission_id)

    targets = list(
        (
            await session.scalars(
                select(ReviewSubmissionTarget).where(
                    ReviewSubmissionTarget.review_submission_id == review_submission_id,
                    ReviewSubmissionTarget.status == ReviewTargetStatus.APPROVED,
                )
            )
        ).all()
    )
    if submission.submission_kind == ReviewSubmissionKind.PARENT_WITH_PRIMARY:
        all_targets = list(
            (
                await session.scalars(
                    select(ReviewSubmissionTarget).where(
                        ReviewSubmissionTarget.review_submission_id == review_submission_id
                    )
                )
            ).all()
        )
        if not all_targets or any(
            target.status != ReviewTargetStatus.APPROVED for target in all_targets
        ):
            return []
    jobs: list[IndexJob] = []
    for target in targets:
        idempotency_key = _index_job_key(
            submission.id,
            target.knowledge_base_id,
            submission.child_revision_id,
        )
        existing = await session.scalar(
            select(IndexJob).where(IndexJob.idempotency_key == idempotency_key)
        )
        if existing is not None:
            jobs.append(existing)
            continue
        job = IndexJob(
            job_kind=IndexJobKind.INDEX_TARGET,
            status=IndexJobStatus.PENDING,
            idempotency_key=idempotency_key,
            review_submission_id=submission.id,
            knowledge_base_id=target.knowledge_base_id,
            child_id=submission.child_id,
            child_revision_id=submission.child_revision_id,
        )
        session.add(job)
        jobs.append(job)
    await session.flush()
    return jobs


async def claim_next_index_job(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = 300,
) -> IndexJob | None:
    if not worker_id.strip():
        raise ValueError("worker_id must not be empty")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")

    now = datetime.now(UTC)
    while True:
        statement = (
            select(IndexJob)
            .where(
                IndexJob.available_at <= now,
                or_(
                    IndexJob.status == IndexJobStatus.PENDING,
                    (IndexJob.status == IndexJobStatus.RUNNING)
                    & IndexJob.lease_expires_at.is_not(None)
                    & (IndexJob.lease_expires_at < now),
                ),
            )
            .order_by(IndexJob.available_at, IndexJob.created_at, IndexJob.id)
            .with_for_update(skip_locked=True)
        )
        job = await session.scalar(statement)
        if job is None:
            return None
        if job.attempt_count >= job.max_attempts:
            job.status = IndexJobStatus.FAILED
            job.completed_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_error = job.last_error or "maximum attempts exceeded after lease expiry"
            if job.review_submission_id is not None and job.knowledge_base_id is not None:
                target = await session.scalar(
                    select(ReviewSubmissionTarget).where(
                        ReviewSubmissionTarget.review_submission_id == job.review_submission_id,
                        ReviewSubmissionTarget.knowledge_base_id == job.knowledge_base_id,
                    )
                )
                if target is not None and target.status in {
                    ReviewTargetStatus.APPROVED,
                    ReviewTargetStatus.INDEXING,
                }:
                    from app.services.knowledge_content import mark_target_index_failed

                    await mark_target_index_failed(
                        session,
                        review_submission_id=job.review_submission_id,
                        knowledge_base_id=job.knowledge_base_id,
                    )
            await session.flush()
            continue

        job.status = IndexJobStatus.RUNNING
        job.attempt_count += 1
        job.lease_owner = worker_id
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.started_at = job.started_at or now
        job.last_error = None
        await session.flush()
        return job


async def _locked_job(session: AsyncSession, job_id: UUID) -> IndexJob:
    job = await session.scalar(select(IndexJob).where(IndexJob.id == job_id).with_for_update())
    if job is None:
        raise IndexJobNotFoundError(job_id)
    return job


def _assert_lease(job: IndexJob, worker_id: str) -> None:
    if job.status != IndexJobStatus.RUNNING or job.lease_owner != worker_id:
        raise IndexJobLeaseError(job.id)


def _validate_target_fields(job: IndexJob) -> None:
    if (
        job.job_kind != IndexJobKind.INDEX_TARGET
        or job.review_submission_id is None
        or job.knowledge_base_id is None
        or job.child_id is None
        or job.child_revision_id is None
    ):
        raise IndexTargetFieldsError(job.id)


async def complete_index_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
) -> IndexJob:
    job = await _locked_job(session, job_id)
    _assert_lease(job, worker_id)
    _validate_target_fields(job)

    submission = await session.get(ReviewSubmission, job.review_submission_id)
    if submission is None:
        raise IndexJobNotFoundError(job.review_submission_id)
    if submission.submission_kind == ReviewSubmissionKind.PARENT_WITH_PRIMARY:
        sibling_jobs = list(
            (
                await session.scalars(
                    select(IndexJob).where(
                        IndexJob.review_submission_id == submission.id,
                        IndexJob.job_kind == IndexJobKind.INDEX_TARGET,
                    )
                )
            ).all()
        )
        if not sibling_jobs or any(
            sibling.status != IndexJobStatus.SUCCEEDED and sibling.id != job.id
            for sibling in sibling_jobs
        ):
            now = datetime.now(UTC)
            job.status = IndexJobStatus.SUCCEEDED
            job.completed_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_error = None
            await session.flush()
            return job

    from app.services.knowledge_content import publish_approved_target

    await publish_approved_target(
        session,
        review_submission_id=job.review_submission_id,
        knowledge_base_id=job.knowledge_base_id,
    )
    now = datetime.now(UTC)
    job.status = IndexJobStatus.SUCCEEDED
    job.completed_at = now
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_error = None
    await session.flush()
    return job


async def fail_index_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    worker_id: str,
    error: str,
    retry_delay_seconds: int | None = None,
) -> IndexJob:
    job = await _locked_job(session, job_id)
    _assert_lease(job, worker_id)
    _validate_target_fields(job)
    now = datetime.now(UTC)
    job.last_error = error[:8_000]
    job.lease_owner = None
    job.lease_expires_at = None

    from app.services.knowledge_content import mark_target_index_failed

    try:
        await mark_target_index_failed(
            session,
            review_submission_id=job.review_submission_id,
            knowledge_base_id=job.knowledge_base_id,
        )
    except Exception:
        # A completion racing with failure should not make the durable job
        # transaction disappear; the publication state remains authoritative.
        pass

    if job.attempt_count >= job.max_attempts:
        job.status = IndexJobStatus.FAILED
        job.completed_at = now
    else:
        delay = retry_delay_seconds
        if delay is None:
            delay = min(3_600, 2 ** max(job.attempt_count - 1, 0))
        if delay <= 0:
            raise ValueError("retry_delay_seconds must be positive")
        job.status = IndexJobStatus.PENDING
        job.available_at = now + timedelta(seconds=delay)
    await session.flush()
    return job


async def run_next_index_job(
    session: AsyncSession,
    *,
    worker_id: str,
    backend: IndexBackend | None = None,
    lease_seconds: int = 300,
) -> IndexWorkerResult | None:
    job = await claim_next_index_job(
        session,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    if job is None:
        return None
    backend = backend or NoopIndexBackend()
    try:
        from app.services.knowledge_content import (
            retry_failed_target_indexing,
            start_target_indexing,
        )

        _validate_target_fields(job)
        target = await session.scalar(
            select(ReviewSubmissionTarget).where(
                ReviewSubmissionTarget.review_submission_id == job.review_submission_id,
                ReviewSubmissionTarget.knowledge_base_id == job.knowledge_base_id,
            )
        )
        if target is None:
            raise IndexTargetFieldsError(job.id)
        if target.status == ReviewTargetStatus.PUBLISHED:
            await complete_index_job(session, job_id=job.id, worker_id=worker_id)
            return IndexWorkerResult(job_id=job.id, status=IndexJobStatus.SUCCEEDED)
        if target.status == ReviewTargetStatus.INDEX_FAILED:
            await retry_failed_target_indexing(
                session,
                review_submission_id=job.review_submission_id,
                knowledge_base_id=job.knowledge_base_id,
            )
        await start_target_indexing(
            session,
            review_submission_id=job.review_submission_id,
            knowledge_base_id=job.knowledge_base_id,
        )
        await backend.index_target(session, job)
        await complete_index_job(session, job_id=job.id, worker_id=worker_id)
        return IndexWorkerResult(job_id=job.id, status=IndexJobStatus.SUCCEEDED)
    except Exception as exc:
        failed = await fail_index_job(
            session,
            job_id=job.id,
            worker_id=worker_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return IndexWorkerResult(job_id=failed.id, status=failed.status, error=failed.last_error)


async def run_index_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    worker_id: str,
    backend: IndexBackend | None = None,
    lease_seconds: int = 300,
) -> IndexWorkerResult | None:
    async with session_factory() as session:
        result = await run_next_index_job(
            session,
            worker_id=worker_id,
            backend=backend,
            lease_seconds=lease_seconds,
        )
        await session.commit()
        return result
