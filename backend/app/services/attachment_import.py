"""Deep module for durable DOC/DOCX attachment imports.

Routes only start/query/retry/confirm/cancel batches.  Storage, document
conversion, LLM extraction, parent matching, draft creation and the review
transaction stay behind this module so callers cannot accidentally assemble a
partial import workflow.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.attachment_ingestion import (
    AttachmentIngestionBatch,
    AttachmentIngestionBatchStatus,
)
from app.models.intelligent_ingestion import KnowledgeDraft, KnowledgeDraftSource
from app.models.knowledge_content import (
    ChildRevision,
    ChildRevisionQuestionVariant,
    EvidenceAttachment,
    ParentLexicalRule,
    ReviewSubmission,
    ReviewSubmissionKind,
    ReviewSubmissionStatus,
    WebLink,
)
from app.schemas.attachment_ingestion import (
    AttachmentImportCandidate,
    AttachmentImportParentProposal,
    AttachmentImportProposal,
    AttachmentImportSimilarParent,
    ConfirmAttachmentImportRequest,
)
from app.schemas.knowledge_content import ChildContentInput, ParentContentInput, WebLinkInput
from app.services.attachment_storage import AttachmentStorage, AttachmentStorageError
from app.services.attachments import ValidatedAttachmentUpload
from app.services.document_extraction import (
    DocumentExtractionError,
    DocumentExtractionTransientError,
    extract_word_document_async,
)
from app.services.knowledge_content import (
    AvailableParentDetails,
    SubmissionDetails,
    get_available_parent_details,
    list_available_parents,
    list_submissions_by_author,
    submit_new_parent_aggregate,
    submit_parent_aggregate_revision,
)
from app.services.llm import (
    AttachmentKnowledgeExtraction,
    AttachmentProposalProvider,
    LlmConfigurationError,
    LlmOutputError,
    LlmProviderError,
)
from app.services.taxonomy import is_allowed_parent_type, is_allowed_taxonomy_value

TRANSIENT_ATTACHMENT_IMPORT_ERROR = "附件解析暂时不可用，请稍后自动重试"
INVALID_ATTACHMENT_IMPORT_ERROR = "附件解析结果无效，已生成可人工编辑的兜底方案"
_PII_PATTERNS = (
    re.compile(r"(?<![\d])(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"),
    re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE),
    re.compile(
        r"(?:姓名|联系人|客户名称|公司名称|联系方式|手机号|手机号码|邮箱|账号|账户|客户编号|订单号)"
        r"\s*[：:]\s*[^\s，,。；;]{2,}"
    ),
)
_VERSION_SUFFIX = re.compile(
    r"(?:[ _\-]*(?:v(?:ersion)?\s*)?\d+(?:\.\d+){0,3}|[ _\-]*版本[ _\-]*\d+(?:\.\d+)*)$",
    re.IGNORECASE,
)


class AttachmentImportNotFoundError(Exception):
    pass


class AttachmentImportStateError(Exception):
    pass


class AttachmentImportExpiredError(AttachmentImportStateError):
    pass


class AttachmentImportConfirmationError(Exception):
    pass


@dataclass(frozen=True)
class AttachmentImportWorkerResult:
    batch_id: UUID
    status: AttachmentIngestionBatchStatus
    error: str | None = None


@dataclass(frozen=True)
class AttachmentImportDetails:
    batch: AttachmentIngestionBatch
    attachment: EvidenceAttachment


@dataclass(frozen=True)
class AttachmentImportConfirmation:
    submission: SubmissionDetails
    parent_id: UUID
    created_draft_ids: list[UUID]


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive DateTime round-trips before Python compares them."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _batch_is_expired(batch: AttachmentIngestionBatch) -> bool:
    return _as_utc(batch.expires_at) <= _now()


def _retry_delay_seconds(attempt_count: int) -> int:
    return min(30 * (2 ** max(attempt_count - 1, 0)), 600)


def _normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _redact_text(value: str | None) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    redacted = value
    for pattern in _PII_PATTERNS:
        redacted = pattern.sub("", redacted)
    redacted = re.sub(r"[ \t]{2,}", " ", redacted).strip()
    return redacted or "已脱敏", redacted != value


def _fallback_stem(name: str) -> str:
    stem = _VERSION_SUFFIX.sub("", Path(name).stem).strip(" _-")
    stem, _ = _redact_text(stem)
    return (stem or "附件操作说明")[:255]


def _fallback_proposal(name: str, warnings: list[str]) -> AttachmentImportProposal:
    stem = _fallback_stem(name)
    safe_name = f"{stem}{Path(name).suffix.casefold()}"
    candidate_id = uuid4().hex
    return AttachmentImportProposal(
        parent=AttachmentImportParentProposal(
            name="问题反馈",
            canonical_keyword=stem,
            aliases=[],
        ),
        children=[
            AttachmentImportCandidate(
                id=candidate_id,
                question=f"{stem}的操作步骤是什么？",
                response_content=f"详细操作步骤和页面示意请查看附件《{safe_name}》。",
            )
        ],
        recommended_primary_child_id=candidate_id,
        warnings=warnings,
    )


def _candidate_from_llm(
    *,
    candidate,
    warnings: list[str],
    position: int,
) -> AttachmentImportCandidate:
    values: dict[str, object] = {
        "question": candidate.question,
        "response_content": candidate.response_content,
        "question_variants": list(candidate.question_variants),
        "follow_up_guidance": candidate.follow_up_guidance,
        "question_type": candidate.question_type,
        "business_object": candidate.business_object,
        "purpose": candidate.purpose,
        "customer_type": candidate.customer_type,
        "feature_explanation": candidate.feature_explanation,
        "example": candidate.example,
    }
    redacted_any = False
    for field_name, value in tuple(values.items()):
        if isinstance(value, str) or value is None:
            redacted, changed = _redact_text(value)
            values[field_name] = redacted
            redacted_any = redacted_any or changed
        elif isinstance(value, list):
            normalized_values: list[str] = []
            for item in value:
                redacted, changed = _redact_text(item)
                if redacted is not None:
                    normalized_values.append(redacted)
                redacted_any = redacted_any or changed
            values[field_name] = normalized_values
    question = values["question"]
    assert isinstance(question, str)
    variants = values["question_variants"]
    assert isinstance(variants, list)
    deduplicated_variants: list[str] = []
    for variant in variants:
        if not isinstance(variant, str):
            continue
        if variant.casefold() == question.casefold():
            continue
        if variant.casefold() not in {item.casefold() for item in deduplicated_variants}:
            deduplicated_variants.append(variant)
    values["question_variants"] = deduplicated_variants
    values["id"] = uuid4().hex
    for field_name, label in (
        ("question_type", "问题类型"),
        ("business_object", "具体功能与模块"),
        ("purpose", "应用场景"),
        ("customer_type", "客户类型"),
    ):
        value = values[field_name]
        if isinstance(value, str) and not is_allowed_taxonomy_value(field_name, value):
            values[field_name] = None
            warnings.append(f"第 {position} 条候选的{label}不在固定选项中，已清空，请人工选择。")
    if redacted_any:
        warnings.append("已自动清理模型建议中的个人信息或客户标识。")
    return AttachmentImportCandidate.model_validate(values)


async def _similar_published_parents(
    session: AsyncSession,
    *,
    proposal: AttachmentImportProposal,
    settings: Settings,
) -> list[AttachmentImportSimilarParent]:
    query_terms = [
        _normalize_match_text(value)
        for value in (
            proposal.parent.name,
            proposal.parent.canonical_keyword,
            *(child.question for child in proposal.children),
        )
    ]
    query_terms = [term for term in query_terms if term]
    if not query_terms:
        return []
    parents = await list_available_parents(session)
    candidates: list[AttachmentImportSimilarParent] = []
    for details in parents:
        rules = list(
            (
                await session.scalars(
                    select(ParentLexicalRule)
                    .where(ParentLexicalRule.parent_revision_id == details.parent_revision.id)
                    .order_by(ParentLexicalRule.sort_order)
                )
            ).all()
        )
        keywords = [details.parent_revision.canonical_keyword] + [
            rule.rule_value for rule in rules if rule.rule_type.value == "alias"
        ]
        best_score = 0
        best_keyword = details.parent_revision.canonical_keyword
        for keyword in keywords:
            normalized_keyword = _normalize_match_text(keyword)
            if not normalized_keyword:
                continue
            if normalized_keyword in query_terms:
                score = 100
            elif len(normalized_keyword) >= 2 and any(
                normalized_keyword in term or term in normalized_keyword for term in query_terms
            ):
                score = 95
            else:
                score = max(
                    round(100 * SequenceMatcher(None, term, normalized_keyword).ratio())
                    for term in query_terms
                )
            if score > best_score:
                best_score = score
                best_keyword = keyword
        if best_score >= settings.attachment_import_match_threshold:
            candidates.append(
                AttachmentImportSimilarParent(
                    id=details.parent.id,
                    name=details.parent_revision.name,
                    canonical_keyword=details.parent_revision.canonical_keyword,
                    score=best_score,
                    matched_keyword=best_keyword,
                    available_knowledge_bases=[
                        knowledge_base.id for knowledge_base in details.knowledge_bases
                    ],
                )
            )
    candidates.sort(key=lambda item: (-item.score, item.name, str(item.id)))
    return candidates[: settings.attachment_import_match_limit]


async def create_attachment_import_batch(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    upload: ValidatedAttachmentUpload,
    settings: Settings,
) -> AttachmentIngestionBatch:
    """Stage an already-stored, validated Word document and create a batch."""

    if Path(upload.name).suffix.casefold() not in {".doc", ".docx"}:
        raise AttachmentImportConfirmationError("附件解析仅支持 DOC 或 DOCX 文件")
    now = _now()
    attachment = EvidenceAttachment(
        name=upload.name,
        storage_key=upload.storage_key,
        content_type=upload.content_type,
        size_bytes=len(upload.content),
        checksum_sha256=upload.checksum_sha256,
        uploaded_by_user_id=owner_user_id,
    )
    session.add(attachment)
    await session.flush()
    batch = AttachmentIngestionBatch(
        owner_user_id=owner_user_id,
        attachment_id=attachment.id,
        status=AttachmentIngestionBatchStatus.PROCESSING,
        available_at=now,
        expires_at=now + timedelta(days=settings.attachment_import_retention_days),
    )
    session.add(batch)
    await session.flush()
    return batch


async def _get_owned_batch(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    batch_id: UUID,
    lock: bool = False,
) -> AttachmentIngestionBatch:
    statement = select(AttachmentIngestionBatch).where(
        AttachmentIngestionBatch.id == batch_id,
        AttachmentIngestionBatch.owner_user_id == owner_user_id,
    )
    if lock:
        statement = statement.with_for_update()
    batch = await session.scalar(statement)
    if batch is None:
        raise AttachmentImportNotFoundError(batch_id)
    return batch


async def get_attachment_import_details(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    batch_id: UUID,
) -> AttachmentImportDetails:
    batch = await _get_owned_batch(session, owner_user_id=owner_user_id, batch_id=batch_id)
    attachment = await session.get(EvidenceAttachment, batch.attachment_id)
    if attachment is None:
        raise AttachmentImportNotFoundError(batch_id)
    return AttachmentImportDetails(batch=batch, attachment=attachment)


async def list_attachment_import_batches(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
) -> list[AttachmentImportDetails]:
    rows = list(
        (
            await session.execute(
                select(AttachmentIngestionBatch, EvidenceAttachment)
                .join(
                    EvidenceAttachment,
                    EvidenceAttachment.id == AttachmentIngestionBatch.attachment_id,
                )
                .where(AttachmentIngestionBatch.owner_user_id == owner_user_id)
                .order_by(AttachmentIngestionBatch.created_at.desc())
                .limit(50)
            )
        ).all()
    )
    return [
        AttachmentImportDetails(batch=batch, attachment=attachment) for batch, attachment in rows
    ]


async def retry_attachment_import_batch(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    batch_id: UUID,
) -> AttachmentIngestionBatch:
    batch = await _get_owned_batch(
        session,
        owner_user_id=owner_user_id,
        batch_id=batch_id,
        lock=True,
    )
    if _batch_is_expired(batch):
        raise AttachmentImportExpiredError("附件解析批次已过期")
    if batch.status != AttachmentIngestionBatchStatus.FAILED:
        raise AttachmentImportStateError("只有失败的附件解析批次可以重试")
    batch.status = AttachmentIngestionBatchStatus.PROCESSING
    batch.proposal = None
    batch.warnings = []
    batch.image_count = 0
    batch.extracted_char_count = 0
    batch.model_version = None
    batch.attempt_count = 0
    batch.available_at = _now()
    batch.lease_owner = None
    batch.lease_expires_at = None
    batch.started_at = None
    batch.completed_at = None
    batch.last_error = None
    await session.flush()
    return batch


async def claim_next_attachment_import_batch(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int,
) -> AttachmentIngestionBatch | None:
    if not worker_id.strip():
        raise ValueError("worker_id must not be empty")
    now = _now()
    batch = await session.scalar(
        select(AttachmentIngestionBatch)
        .where(
            AttachmentIngestionBatch.status == AttachmentIngestionBatchStatus.PROCESSING,
            AttachmentIngestionBatch.available_at <= now,
            AttachmentIngestionBatch.expires_at > now,
            (AttachmentIngestionBatch.lease_expires_at.is_(None))
            | (AttachmentIngestionBatch.lease_expires_at < now),
        )
        .order_by(
            AttachmentIngestionBatch.available_at,
            AttachmentIngestionBatch.created_at,
            AttachmentIngestionBatch.id,
        )
        .with_for_update(skip_locked=True)
    )
    if batch is None:
        return None
    batch.attempt_count += 1
    batch.lease_owner = worker_id[:120]
    batch.lease_expires_at = now + timedelta(seconds=lease_seconds)
    batch.started_at = batch.started_at or now
    batch.last_error = None
    await session.flush()
    return batch


async def _fail_attachment_import_batch(
    session: AsyncSession,
    *,
    batch: AttachmentIngestionBatch,
    error: str,
    retryable: bool,
) -> None:
    now = _now()
    batch.last_error = error[:2_000]
    if retryable and batch.attempt_count < batch.max_attempts:
        batch.available_at = now + timedelta(seconds=_retry_delay_seconds(batch.attempt_count))
        batch.lease_owner = None
        batch.lease_expires_at = None
    else:
        batch.status = AttachmentIngestionBatchStatus.FAILED
        batch.completed_at = now
        batch.lease_owner = None
        batch.lease_expires_at = None
    await session.flush()


async def process_attachment_import_batch(
    session: AsyncSession,
    *,
    batch: AttachmentIngestionBatch,
    storage: AttachmentStorage,
    provider: AttachmentProposalProvider | None,
    settings: Settings,
) -> AttachmentImportWorkerResult:
    attachment = await session.get(EvidenceAttachment, batch.attachment_id)
    if attachment is None:
        raise DocumentExtractionError("原附件不存在，无法解析")
    content = await storage.get_object(attachment.storage_key)
    suffix = Path(attachment.name).suffix.casefold()
    extracted = await extract_word_document_async(
        content,
        suffix=suffix,
        soffice_path=settings.attachment_import_soffice_path,
        timeout_seconds=settings.attachment_import_timeout_seconds,
    )
    if len(extracted.text) > settings.attachment_import_max_text_chars:
        raise DocumentExtractionError(
            f"附件正文超过 {settings.attachment_import_max_text_chars:,} 字符限制，未进行截断"
        )

    warnings: list[str] = []
    if extracted.image_count:
        warnings.append(
            f"文档含 {extracted.image_count} 张内嵌图片；首版不会识别图中文字，请查看原附件。"
        )
    extraction: AttachmentKnowledgeExtraction | None = None
    model_version: str | None = None
    if not extracted.text:
        warnings.append("附件正文不足，已按文件名生成可人工编辑的兜底问题。")
    elif provider is None:
        warnings.append("智能处理服务未配置，已按文件名生成可人工编辑的兜底问题。")
    else:
        try:
            extraction = await provider.extract_attachment_proposal(extracted.text)
        except (LlmOutputError, LlmConfigurationError):
            warnings.append("模型未生成有效知识，已按文件名生成可人工编辑的兜底问题。")
        else:
            model_version = getattr(provider, "model", None)

    if extraction is None or not extraction.candidates:
        if extracted.text and extraction is not None:
            warnings.append("模型未生成有效知识，已按文件名生成可人工编辑的兜底问题。")
        proposal = _fallback_proposal(attachment.name, warnings)
    else:
        children = [
            _candidate_from_llm(candidate=candidate, warnings=warnings, position=index + 1)
            for index, candidate in enumerate(extraction.candidates)
        ]
        parent_name, parent_changed = _redact_text(
            extraction.parent.name if extraction.parent else None
        )
        parent_keyword, keyword_changed = _redact_text(
            extraction.parent.canonical_keyword if extraction.parent else None
        )
        aliases: list[str] = []
        for alias in extraction.parent.aliases if extraction.parent else []:
            redacted, changed = _redact_text(alias)
            if redacted is not None and all(
                redacted.casefold() != existing.casefold() for existing in aliases
            ):
                aliases.append(redacted)
            parent_changed = parent_changed or changed
        if parent_changed or keyword_changed:
            warnings.append("已自动清理大类建议中的个人信息或客户标识。")
        if not parent_name or not parent_keyword:
            fallback = _fallback_proposal(attachment.name, warnings)
            parent = fallback.parent
            warnings.append("模型未生成有效大类建议，已按文件名生成兜底大类。")
        else:
            if not is_allowed_parent_type(parent_name):
                warnings.append("模型建议的问题大类不在固定选项中，已改为“问题反馈”，请人工确认。")
                parent_name = "问题反馈"
            aliases = [alias for alias in aliases if alias.casefold() != parent_keyword.casefold()]
            parent = AttachmentImportParentProposal(
                name=parent_name[:120],
                canonical_keyword=parent_keyword[:255],
                aliases=aliases[:50],
            )
        recommended_index = extraction.recommended_primary_index
        if recommended_index is None or recommended_index >= len(children):
            recommended_index = 0
            warnings.append("模型未明确推荐主小类，已默认选择第一条，请人工确认。")
        proposal = AttachmentImportProposal(
            parent=parent,
            children=children,
            recommended_primary_child_id=children[recommended_index].id,
            warnings=warnings,
            image_count=extracted.image_count,
        )

    proposal = proposal.model_copy(
        update={
            "similar_parents": await _similar_published_parents(
                session,
                proposal=proposal,
                settings=settings,
            )
        }
    )
    batch.proposal = proposal.model_dump(mode="json")
    batch.warnings = list(proposal.warnings)
    batch.image_count = extracted.image_count
    batch.extracted_char_count = len(extracted.text)
    batch.model_version = model_version
    batch.status = (
        AttachmentIngestionBatchStatus.READY_WITH_WARNINGS
        if batch.warnings
        else AttachmentIngestionBatchStatus.READY
    )
    batch.completed_at = _now()
    batch.lease_owner = None
    batch.lease_expires_at = None
    batch.last_error = None
    await session.flush()
    return AttachmentImportWorkerResult(batch_id=batch.id, status=batch.status)


async def run_attachment_import_worker_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    storage: AttachmentStorage,
    provider: AttachmentProposalProvider | None,
    settings: Settings,
    worker_id: str,
    lease_seconds: int,
) -> AttachmentImportWorkerResult | None:
    async with session_factory() as session:
        await purge_expired_attachment_import_batches(session, storage=storage)
        batch = await claim_next_attachment_import_batch(
            session,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        if batch is None:
            await session.commit()
            return None
        try:
            result = await process_attachment_import_batch(
                session,
                batch=batch,
                storage=storage,
                provider=provider,
                settings=settings,
            )
        except (AttachmentStorageError, LlmProviderError):
            await _fail_attachment_import_batch(
                session,
                batch=batch,
                error=TRANSIENT_ATTACHMENT_IMPORT_ERROR,
                retryable=True,
            )
            await session.commit()
            return AttachmentImportWorkerResult(
                batch_id=batch.id,
                status=batch.status,
                error=TRANSIENT_ATTACHMENT_IMPORT_ERROR,
            )
        except DocumentExtractionTransientError as exc:
            await _fail_attachment_import_batch(
                session,
                batch=batch,
                error=str(exc),
                retryable=True,
            )
            await session.commit()
            return AttachmentImportWorkerResult(
                batch_id=batch.id,
                status=batch.status,
                error=str(exc),
            )
        except (DocumentExtractionError, LlmConfigurationError) as exc:
            await _fail_attachment_import_batch(
                session,
                batch=batch,
                error=str(exc),
                retryable=False,
            )
            await session.commit()
            return AttachmentImportWorkerResult(
                batch_id=batch.id,
                status=batch.status,
                error=str(exc),
            )
        except LlmOutputError:
            # An invalid LLM output is intentionally handled as a successful
            # fallback proposal, but retain this safety net for provider changes.
            await _fail_attachment_import_batch(
                session,
                batch=batch,
                error=INVALID_ATTACHMENT_IMPORT_ERROR,
                retryable=False,
            )
            await session.commit()
            return AttachmentImportWorkerResult(
                batch_id=batch.id,
                status=batch.status,
                error=INVALID_ATTACHMENT_IMPORT_ERROR,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never log raw document text or model responses.
            await _fail_attachment_import_batch(
                session,
                batch=batch,
                error=TRANSIENT_ATTACHMENT_IMPORT_ERROR,
                retryable=True,
            )
            await session.commit()
            return AttachmentImportWorkerResult(
                batch_id=batch.id,
                status=batch.status,
                error=TRANSIENT_ATTACHMENT_IMPORT_ERROR,
            )
        await session.commit()
        return result


def _as_child_content(
    candidate: AttachmentImportCandidate,
    *,
    attachment_ids: list[UUID] | None = None,
) -> ChildContentInput:
    return ChildContentInput(
        question=candidate.question,
        response_content=candidate.response_content,
        question_variants=list(candidate.question_variants),
        follow_up_guidance=candidate.follow_up_guidance,
        question_type=candidate.question_type,
        business_object=candidate.business_object,
        purpose=candidate.purpose,
        customer_type=candidate.customer_type,
        feature_explanation=candidate.feature_explanation,
        example=candidate.example,
        internal_notes=candidate.internal_notes,
        attachments=attachment_ids or [],
        web_links=[],
    )


def _assert_complete_taxonomy(candidate: AttachmentImportCandidate) -> None:
    for field_name, label in (
        ("question_type", "问题类型"),
        ("business_object", "具体功能与模块"),
        ("purpose", "应用场景"),
        ("customer_type", "客户类型"),
    ):
        value = getattr(candidate, field_name)
        if not is_allowed_taxonomy_value(field_name, value):
            raise AttachmentImportConfirmationError(
                f"小类“{candidate.question}”必须选择有效的{label}"
            )


def _assert_edited_children_match_proposal(
    request: ConfirmAttachmentImportRequest,
    proposal: AttachmentImportProposal,
) -> None:
    proposal_ids = {candidate.id for candidate in proposal.children}
    if any(candidate.id not in proposal_ids for candidate in request.children):
        raise AttachmentImportConfirmationError("确认方案包含不属于该解析批次的小类")
    for candidate in request.children:
        _assert_complete_taxonomy(candidate)


async def _current_primary_content(
    session: AsyncSession,
    *,
    parent_details: AvailableParentDetails,
    new_attachment_id: UUID,
) -> tuple[ParentContentInput, ChildContentInput]:
    source_submission = await session.scalar(
        select(ReviewSubmission)
        .where(
            ReviewSubmission.parent_id == parent_details.parent.id,
            ReviewSubmission.submission_kind == ReviewSubmissionKind.PARENT_WITH_PRIMARY,
            ReviewSubmission.status == ReviewSubmissionStatus.PUBLISHED,
        )
        .order_by(ReviewSubmission.submitted_at.desc(), ReviewSubmission.id.desc())
    )
    if source_submission is None:
        raise AttachmentImportStateError("已有大类缺少已发布主小类")
    source_child_revision = await session.get(ChildRevision, source_submission.child_revision_id)
    if source_child_revision is None:
        raise AttachmentImportStateError("已有大类主小类修订不存在")
    rules = list(
        (
            await session.scalars(
                select(ParentLexicalRule)
                .where(ParentLexicalRule.parent_revision_id == parent_details.parent_revision.id)
                .order_by(ParentLexicalRule.sort_order)
            )
        ).all()
    )
    variants = list(
        (
            await session.scalars(
                select(ChildRevisionQuestionVariant)
                .where(ChildRevisionQuestionVariant.child_revision_id == source_child_revision.id)
                .order_by(ChildRevisionQuestionVariant.sort_order)
            )
        ).all()
    )
    attachments = list(
        (
            await session.scalars(
                select(EvidenceAttachment)
                .where(EvidenceAttachment.child_revision_id == source_child_revision.id)
                .order_by(EvidenceAttachment.sort_order)
            )
        ).all()
    )
    if len(attachments) >= 10:
        raise AttachmentImportStateError("已有主小类的附件数量已达上限")
    links = list(
        (
            await session.scalars(
                select(WebLink)
                .where(WebLink.child_revision_id == source_child_revision.id)
                .order_by(WebLink.sort_order)
            )
        ).all()
    )
    return (
        ParentContentInput(
            name=parent_details.parent_revision.name,
            canonical_keyword=parent_details.parent_revision.canonical_keyword,
            lexical_rules=[
                {"rule_type": rule.rule_type, "rule_value": rule.rule_value} for rule in rules
            ],
        ),
        ChildContentInput(
            question=source_child_revision.question,
            response_content=source_child_revision.response_content,
            question_variants=[variant.question_text for variant in variants],
            follow_up_guidance=source_child_revision.follow_up_guidance,
            question_type=source_child_revision.question_type,
            business_object=source_child_revision.business_object,
            purpose=source_child_revision.purpose,
            customer_type=source_child_revision.customer_type,
            feature_explanation=source_child_revision.feature_explanation,
            example=source_child_revision.example,
            internal_notes=source_child_revision.internal_notes,
            attachments=[*[attachment.id for attachment in attachments], new_attachment_id],
            web_links=[WebLinkInput(title=link.title, url=link.url) for link in links],
        ),
    )


def _draft_fingerprint(batch_id: UUID, candidate_id: str) -> str:
    return hashlib.sha256(f"{batch_id}:{candidate_id}".encode()).hexdigest()


async def _create_attachment_draft(
    session: AsyncSession,
    *,
    batch: AttachmentIngestionBatch,
    parent_id: UUID,
    candidate: AttachmentImportCandidate,
    knowledge_base_ids: list[UUID],
) -> KnowledgeDraft:
    content = _as_child_content(candidate)
    attachment = await session.get(EvidenceAttachment, batch.attachment_id)
    if attachment is None:
        raise AttachmentImportStateError("原附件不存在")
    draft = KnowledgeDraft(
        owner_user_id=batch.owner_user_id,
        source=KnowledgeDraftSource.ATTACHMENT_GENERATED,
        parent_id=parent_id,
        attachment_ingestion_batch_id=batch.id,
        candidate_fingerprint=_draft_fingerprint(batch.id, candidate.id),
        question=content.question,
        response_content=content.response_content,
        question_variants=content.question_variants,
        follow_up_guidance=content.follow_up_guidance,
        question_type=content.question_type,
        business_object=content.business_object,
        purpose=content.purpose,
        customer_type=content.customer_type,
        feature_explanation=content.feature_explanation,
        example=content.example,
        internal_notes=content.internal_notes,
        attachments=[],
        web_links=[],
        knowledge_base_ids=[str(knowledge_base_id) for knowledge_base_id in knowledge_base_ids],
        source_hash=attachment.checksum_sha256,
        extracted_at=_now(),
        model_version=batch.model_version,
    )
    session.add(draft)
    await session.flush()
    return draft


async def _existing_submission_details(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    submission_id: UUID,
) -> SubmissionDetails:
    submissions = await list_submissions_by_author(session, owner_user_id)
    for details in submissions:
        if details.submission.id == submission_id:
            return details
    raise AttachmentImportStateError("附件解析批次的投稿结果不存在")


async def _existing_attachment_draft_ids(
    session: AsyncSession,
    *,
    batch_id: UUID,
) -> list[UUID]:
    return list(
        (
            await session.scalars(
                select(KnowledgeDraft.id)
                .where(KnowledgeDraft.attachment_ingestion_batch_id == batch_id)
                .order_by(KnowledgeDraft.created_at, KnowledgeDraft.id)
            )
        ).all()
    )


async def confirm_attachment_import(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    batch_id: UUID,
    request: ConfirmAttachmentImportRequest,
) -> AttachmentImportConfirmation:
    """Atomically submit the primary revision and stage all ordinary children."""

    batch = await _get_owned_batch(
        session,
        owner_user_id=owner_user_id,
        batch_id=batch_id,
        lock=True,
    )
    if batch.status == AttachmentIngestionBatchStatus.SUBMITTED:
        if batch.final_submission_id is None or batch.final_parent_id is None:
            raise AttachmentImportStateError("已提交批次缺少投稿引用")
        return AttachmentImportConfirmation(
            submission=await _existing_submission_details(
                session,
                owner_user_id=owner_user_id,
                submission_id=batch.final_submission_id,
            ),
            parent_id=batch.final_parent_id,
            created_draft_ids=await _existing_attachment_draft_ids(
                session,
                batch_id=batch.id,
            ),
        )
    if _batch_is_expired(batch):
        raise AttachmentImportExpiredError("附件解析批次已过期")
    if batch.status not in {
        AttachmentIngestionBatchStatus.READY,
        AttachmentIngestionBatchStatus.READY_WITH_WARNINGS,
    }:
        raise AttachmentImportStateError("附件解析尚未完成，暂不能确认")
    if batch.proposal is None:
        raise AttachmentImportStateError("附件解析方案不存在，请重试解析")
    proposal = AttachmentImportProposal.model_validate(batch.proposal)
    _assert_edited_children_match_proposal(request, proposal)
    selected = next(
        candidate for candidate in request.children if candidate.id == request.primary_child_id
    )

    if request.target == "new":
        assert request.parent is not None
        if not is_allowed_parent_type(request.parent.name):
            raise AttachmentImportConfirmationError("问题大类必须从固定选项中选择")
        submission = await submit_new_parent_aggregate(
            session,
            parent_content=request.parent,
            primary_child_content=_as_child_content(
                selected,
                attachment_ids=[batch.attachment_id],
            ),
            knowledge_base_ids=request.knowledge_base_ids,
            submitted_by_user_id=owner_user_id,
        )
        parent_id = submission.submission.parent_id
        draft_candidates = [
            candidate for candidate in request.children if candidate.id != request.primary_child_id
        ]
        knowledge_base_ids = request.knowledge_base_ids
    else:
        assert request.existing_parent_id is not None
        parent_details = await get_available_parent_details(
            session,
            request.existing_parent_id,
            lock=True,
        )
        parent_content, primary_content = await _current_primary_content(
            session,
            parent_details=parent_details,
            new_attachment_id=batch.attachment_id,
        )
        submission = await submit_parent_aggregate_revision(
            session,
            parent_id=parent_details.parent.id,
            parent_content=parent_content,
            primary_child_content=primary_content,
            submitted_by_user_id=owner_user_id,
        )
        parent_id = parent_details.parent.id
        # When merging, every parsed candidate remains an ordinary-child draft;
        # the existing primary content is never replaced with a model result.
        draft_candidates = list(request.children)
        knowledge_base_ids = [
            knowledge_base.id for knowledge_base in parent_details.knowledge_bases
        ]

    drafts = [
        await _create_attachment_draft(
            session,
            batch=batch,
            parent_id=parent_id,
            candidate=candidate,
            knowledge_base_ids=knowledge_base_ids,
        )
        for candidate in draft_candidates
    ]
    batch.status = AttachmentIngestionBatchStatus.SUBMITTED
    batch.final_submission_id = submission.submission.id
    batch.final_parent_id = parent_id
    batch.submitted_at = _now()
    batch.proposal = None
    batch.lease_owner = None
    batch.lease_expires_at = None
    await session.flush()
    return AttachmentImportConfirmation(
        submission=submission,
        parent_id=parent_id,
        created_draft_ids=[draft.id for draft in drafts],
    )


async def cancel_attachment_import_batch(
    session: AsyncSession,
    *,
    storage: AttachmentStorage,
    owner_user_id: UUID,
    batch_id: UUID,
) -> None:
    batch = await _get_owned_batch(
        session,
        owner_user_id=owner_user_id,
        batch_id=batch_id,
        lock=True,
    )
    now = _now()
    if batch.status == AttachmentIngestionBatchStatus.SUBMITTED:
        raise AttachmentImportStateError("已提交的附件解析批次不能取消")
    if (
        batch.status == AttachmentIngestionBatchStatus.PROCESSING
        and batch.lease_expires_at is not None
        and _as_utc(batch.lease_expires_at) > now
    ):
        raise AttachmentImportStateError("附件解析正在执行，暂不能取消")
    attachment = await session.get(EvidenceAttachment, batch.attachment_id)
    if attachment is None:
        raise AttachmentImportStateError("原附件不存在")
    await storage.delete_object(attachment.storage_key)
    await session.delete(batch)
    await session.flush()
    await session.delete(attachment)
    await session.flush()


async def purge_expired_attachment_import_batches(
    session: AsyncSession,
    *,
    storage: AttachmentStorage,
    now: datetime | None = None,
) -> int:
    moment = now or _now()
    batches = list(
        (
            await session.scalars(
                select(AttachmentIngestionBatch)
                .where(
                    AttachmentIngestionBatch.status != AttachmentIngestionBatchStatus.SUBMITTED,
                    AttachmentIngestionBatch.expires_at < moment,
                    (AttachmentIngestionBatch.lease_expires_at.is_(None))
                    | (AttachmentIngestionBatch.lease_expires_at < moment),
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    removed = 0
    for batch in batches:
        attachment = await session.get(EvidenceAttachment, batch.attachment_id)
        if attachment is None:
            await session.delete(batch)
            removed += 1
            continue
        try:
            await storage.delete_object(attachment.storage_key)
        except AttachmentStorageError:
            # Leave the durable row for the next worker pass; deleting DB
            # state first would orphan an inaccessible private object.
            continue
        await session.delete(batch)
        await session.flush()
        await session.delete(attachment)
        removed += 1
    if removed:
        await session.flush()
    return removed
