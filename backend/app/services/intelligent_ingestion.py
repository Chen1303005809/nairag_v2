from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.intelligent_ingestion import (
    IntelligentIngestionBatch,
    IntelligentIngestionBatchStatus,
    KnowledgeDraft,
    KnowledgeDraftSource,
)
from app.services.conversation import (
    NormalizedConversationMessage,
    ValidatedConversation,
    validate_conversation,
)
from app.services.llm import (
    KnowledgeCandidate,
    LlmConfigurationError,
    LlmOutputError,
    LlmProvider,
    LlmProviderError,
)

RAW_INPUT_RETENTION_SECONDS = 24 * 60 * 60
TRANSIENT_INGESTION_ERROR = "智能处理服务暂时不可用，请稍后重试"
CONFIGURATION_INGESTION_ERROR = "智能处理服务配置错误，请联系管理员"
INVALID_OUTPUT_INGESTION_ERROR = "智能处理服务返回无效结果，请稍后重试"


class ConversationValidationError(ValueError):
    """Raised when the submitted conversation cannot be processed."""


class IngestionBatchNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class IngestionWorkerResult:
    batch_id: UUID
    status: IntelligentIngestionBatchStatus
    generated_count: int
    error: str | None = None


@dataclass(frozen=True)
class IngestionBatchDetails:
    batch: IntelligentIngestionBatch
    drafts: list[KnowledgeDraft]


def _candidate_fingerprint(candidate: KnowledgeCandidate) -> str:
    """Return a stable idempotency key for one LLM-generated draft.

    A leased batch can be retried after an interrupted worker or an expired
    lease.  The key is deliberately based on every persistable candidate field
    rather than model order, so a repeated response cannot create duplicate
    drafts even when the provider changes their ordering.
    """

    payload = {
        "question": candidate.question,
        "response_content": candidate.response_content,
        "question_variants": candidate.question_variants,
        "follow_up_guidance": candidate.follow_up_guidance,
        "question_type": candidate.question_type,
        "business_object": candidate.business_object,
        "purpose": candidate.purpose,
        "customer_type": candidate.customer_type,
        "feature_explanation": candidate.feature_explanation,
        "example": candidate.example,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _create_generated_draft_if_absent(
    session: AsyncSession,
    *,
    batch: IntelligentIngestionBatch,
    candidate: KnowledgeCandidate,
    source_hash: str,
    extracted_at: datetime,
    model_version: str | None,
    known_fingerprints: set[str],
) -> bool:
    """Persist one candidate once, including across concurrent lease retries."""

    fingerprint = _candidate_fingerprint(candidate)
    if fingerprint in known_fingerprints:
        return False

    draft = KnowledgeDraft(
        owner_user_id=batch.owner_user_id,
        source=KnowledgeDraftSource.INTELLIGENT_GENERATED,
        ingestion_batch_id=batch.id,
        candidate_fingerprint=fingerprint,
        question=candidate.question,
        response_content=candidate.response_content,
        question_variants=candidate.question_variants,
        follow_up_guidance=candidate.follow_up_guidance,
        question_type=candidate.question_type,
        business_object=candidate.business_object,
        purpose=candidate.purpose,
        customer_type=candidate.customer_type,
        feature_explanation=candidate.feature_explanation,
        example=candidate.example,
        source_hash=source_hash,
        extracted_at=extracted_at,
        model_version=model_version,
    )
    try:
        async with session.begin_nested():
            session.add(draft)
            await session.flush()
    except IntegrityError:
        # The unique batch/fingerprint constraint is the cross-worker guard.
        # Re-raise unrelated integrity failures instead of hiding corruption.
        existing = await session.scalar(
            select(KnowledgeDraft.id).where(
                KnowledgeDraft.ingestion_batch_id == batch.id,
                KnowledgeDraft.candidate_fingerprint == fingerprint,
            )
        )
        if existing is None:
            raise
        known_fingerprints.add(fingerprint)
        return False

    known_fingerprints.add(fingerprint)
    return True


async def create_ingestion_batch(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    messages: list[NormalizedConversationMessage],
    settings: Settings,
) -> IntelligentIngestionBatch:
    try:
        conversation = validate_conversation(
            messages,
            max_messages=settings.llm_max_conversation_messages,
            max_chars=settings.llm_max_conversation_chars,
            require_both_parties=True,
        )
    except ValueError as exc:
        raise ConversationValidationError(str(exc)) from exc

    now = datetime.now(UTC)
    batch = IntelligentIngestionBatch(
        owner_user_id=owner_user_id,
        status=IntelligentIngestionBatchStatus.PROCESSING,
        normalized_messages=[
            message.model_dump(mode="json") for message in conversation.messages
        ],
        message_count=len(conversation.messages),
        source_hash=conversation.source_hash,
        available_at=now,
        raw_input_expires_at=now + timedelta(seconds=RAW_INPUT_RETENTION_SECONDS),
    )
    session.add(batch)
    await session.flush()
    return batch


async def claim_next_ingestion_batch(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int,
) -> IntelligentIngestionBatch | None:
    if not worker_id.strip():
        raise ValueError("worker_id must not be empty")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")

    now = datetime.now(UTC)
    statement = (
        select(IntelligentIngestionBatch)
        .where(
            IntelligentIngestionBatch.status == IntelligentIngestionBatchStatus.PROCESSING,
            IntelligentIngestionBatch.available_at <= now,
            IntelligentIngestionBatch.normalized_messages.is_not(None),
            (
                (IntelligentIngestionBatch.lease_expires_at.is_(None))
                | (IntelligentIngestionBatch.lease_expires_at < now)
            ),
        )
        .order_by(
            IntelligentIngestionBatch.available_at,
            IntelligentIngestionBatch.created_at,
            IntelligentIngestionBatch.id,
        )
        .with_for_update(skip_locked=True)
    )
    batch = await session.scalar(statement)
    if batch is None:
        return None

    batch.attempt_count += 1
    batch.lease_owner = worker_id
    batch.lease_expires_at = now + timedelta(seconds=lease_seconds)
    batch.started_at = batch.started_at or now
    batch.last_error = None
    await session.flush()
    return batch


def _conversation_from_batch(
    batch: IntelligentIngestionBatch,
) -> ValidatedConversation:
    raw_messages = batch.normalized_messages or []
    messages = [NormalizedConversationMessage.model_validate(item) for item in raw_messages]
    return validate_conversation(
        messages,
        max_messages=len(messages) + 1,
        max_chars=10_000_000,
        require_both_parties=True,
    )


async def process_ingestion_batch(
    session: AsyncSession,
    *,
    batch: IntelligentIngestionBatch,
    provider: LlmProvider,
) -> IngestionWorkerResult:
    conversation = _conversation_from_batch(batch)
    extraction = await provider.extract_knowledge_candidates(conversation.transcript)
    now = datetime.now(UTC)
    model_version = getattr(provider, "model", None)

    known_fingerprints = set(
        (
            await session.scalars(
                select(KnowledgeDraft.candidate_fingerprint).where(
                    KnowledgeDraft.ingestion_batch_id == batch.id,
                    KnowledgeDraft.candidate_fingerprint.is_not(None),
                )
            )
        ).all()
    )
    for candidate in extraction.candidates:
        await _create_generated_draft_if_absent(
            session,
            batch=batch,
            candidate=candidate,
            source_hash=batch.source_hash,
            extracted_at=now,
            model_version=model_version,
            known_fingerprints=known_fingerprints,
        )

    batch.generated_count = int(
        await session.scalar(
            select(func.count(KnowledgeDraft.id)).where(
                KnowledgeDraft.ingestion_batch_id == batch.id
            )
        )
        or 0
    )
    batch.rejected_count = len(extraction.non_candidates)
    batch.rejection_reasons = [
        {"topic": item.topic, "reason": item.reason} for item in extraction.non_candidates
    ]
    batch.model_version = model_version
    batch.status = (
        IntelligentIngestionBatchStatus.COMPLETED_WITH_WARNINGS
        if extraction.non_candidates
        else IntelligentIngestionBatchStatus.COMPLETED
    )
    batch.completed_at = now
    batch.normalized_messages = None
    batch.lease_owner = None
    batch.lease_expires_at = None
    batch.last_error = None
    await session.flush()

    return IngestionWorkerResult(
        batch_id=batch.id,
        status=batch.status,
        generated_count=batch.generated_count,
    )


def _retry_delay_seconds(attempt_count: int) -> int:
    return min(30 * (2 ** max(attempt_count - 1, 0)), 600)


async def fail_ingestion_batch(
    session: AsyncSession,
    *,
    batch: IntelligentIngestionBatch,
    error: str,
    retryable: bool,
) -> None:
    now = datetime.now(UTC)
    batch.last_error = error[:2_000]
    exhausted = batch.attempt_count >= batch.max_attempts
    if retryable and not exhausted:
        batch.available_at = now + timedelta(seconds=_retry_delay_seconds(batch.attempt_count))
        batch.lease_owner = None
        batch.lease_expires_at = None
    else:
        batch.status = IntelligentIngestionBatchStatus.FAILED
        batch.completed_at = now
        batch.normalized_messages = None
        batch.lease_owner = None
        batch.lease_expires_at = None
    await session.flush()


async def purge_expired_ingestion_raw_input(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """Physically remove raw chat that exceeded the 24-hour retention limit."""

    moment = now or datetime.now(UTC)
    batches = list(
        (
            await session.scalars(
                select(IntelligentIngestionBatch)
                .where(
                    IntelligentIngestionBatch.normalized_messages.is_not(None),
                    IntelligentIngestionBatch.raw_input_expires_at < moment,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for batch in batches:
        batch.normalized_messages = None
        if batch.status == IntelligentIngestionBatchStatus.PROCESSING:
            batch.status = IntelligentIngestionBatchStatus.FAILED
            batch.completed_at = moment
            batch.lease_owner = None
            batch.lease_expires_at = None
            batch.last_error = "原始聊天超过保留期限，已停止处理并删除"
    if batches:
        await session.flush()
    return len(batches)


async def run_ingestion_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider: LlmProvider | None,
    worker_id: str,
    lease_seconds: int,
) -> IngestionWorkerResult | None:
    async with session_factory() as session:
        await purge_expired_ingestion_raw_input(session)
        # A temporarily unavailable LLM configuration must not prevent the
        # worker from enforcing the 24-hour raw-chat retention limit.
        if provider is None:
            await session.commit()
            return None
        batch = await claim_next_ingestion_batch(
            session,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        if batch is None:
            await session.commit()
            return None
        try:
            result = await process_ingestion_batch(session, batch=batch, provider=provider)
        except LlmProviderError:
            await fail_ingestion_batch(
                session,
                batch=batch,
                error=TRANSIENT_INGESTION_ERROR,
                retryable=True,
            )
            await session.commit()
            return IngestionWorkerResult(
                batch_id=batch.id,
                status=batch.status,
                generated_count=0,
                error=TRANSIENT_INGESTION_ERROR,
            )
        except LlmConfigurationError:
            await fail_ingestion_batch(
                session,
                batch=batch,
                error=CONFIGURATION_INGESTION_ERROR,
                retryable=False,
            )
            await session.commit()
            return IngestionWorkerResult(
                batch_id=batch.id,
                status=batch.status,
                generated_count=0,
                error=CONFIGURATION_INGESTION_ERROR,
            )
        except LlmOutputError:
            await fail_ingestion_batch(
                session,
                batch=batch,
                error=INVALID_OUTPUT_INGESTION_ERROR,
                retryable=False,
            )
            await session.commit()
            return IngestionWorkerResult(
                batch_id=batch.id,
                status=batch.status,
                generated_count=0,
                error=INVALID_OUTPUT_INGESTION_ERROR,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never surface or log the conversation text from an unexpected
            # parser/provider failure.  Keep the durable retry contract and a
            # user-safe status message instead.
            error = "智能处理任务执行失败，请稍后重试"
            await fail_ingestion_batch(
                session,
                batch=batch,
                error=error,
                retryable=True,
            )
            await session.commit()
            return IngestionWorkerResult(
                batch_id=batch.id,
                status=batch.status,
                generated_count=0,
                error=error,
            )
        await session.commit()
        return result


async def list_ingestion_batches(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
) -> list[IntelligentIngestionBatch]:
    return list(
        (
            await session.scalars(
                select(IntelligentIngestionBatch)
                .where(IntelligentIngestionBatch.owner_user_id == owner_user_id)
                .order_by(IntelligentIngestionBatch.created_at.desc())
                .limit(50)
            )
        ).all()
    )


async def get_ingestion_batch_details(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    batch_id: UUID,
) -> IngestionBatchDetails:
    batch = await session.scalar(
        select(IntelligentIngestionBatch).where(
            IntelligentIngestionBatch.id == batch_id,
            IntelligentIngestionBatch.owner_user_id == owner_user_id,
        )
    )
    if batch is None:
        raise IngestionBatchNotFoundError(batch_id)
    drafts = list(
        (
            await session.scalars(
                select(KnowledgeDraft)
                .where(
                    KnowledgeDraft.ingestion_batch_id == batch.id,
                    KnowledgeDraft.owner_user_id == owner_user_id,
                )
                .order_by(KnowledgeDraft.created_at, KnowledgeDraft.id)
            )
        ).all()
    )
    return IngestionBatchDetails(batch=batch, drafts=drafts)
