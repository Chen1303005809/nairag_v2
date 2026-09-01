from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class KnowledgeDraftSource(str, Enum):
    MANUAL_SAVED = "manual_saved"
    INTELLIGENT_GENERATED = "intelligent_generated"
    ATTACHMENT_GENERATED = "attachment_generated"


class IntelligentIngestionBatchStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"


class IntelligentIngestionBatch(Base):
    """Async state for one fast-upload conversation batch.

    ``normalized_messages`` is a short-lived processing input. It is physically
    deleted as soon as the batch finishes and no later than
    ``raw_input_expires_at``; logs and drafts never contain the full transcript.
    """

    __tablename__ = "intelligent_ingestion_batch"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_ingestion_batch_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "max_attempts >= 1",
            name="ck_ingestion_batch_max_attempts_positive",
        ),
        CheckConstraint(
            "status NOT IN ('completed', 'completed_with_warnings', 'failed') "
            "OR normalized_messages IS NULL",
            name="ck_ingestion_batch_finished_raw_input_purged",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[IntelligentIngestionBatchStatus] = mapped_column(
        SqlEnum(
            IntelligentIngestionBatchStatus,
            name="intelligent_ingestion_batch_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [value.value for value in enum_class],
        ),
        nullable=False,
        default=IntelligentIngestionBatchStatus.PROCESSING,
        index=True,
    )
    normalized_messages: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejection_reasons: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    model_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_input_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class KnowledgeDraft(Base):
    """Creator-private mutable staging data for a future ordinary child."""

    __tablename__ = "knowledge_draft"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint(
            "question IS NOT NULL OR response_content IS NOT NULL "
            "OR json_array_length(question_variants) > 0 "
            "OR follow_up_guidance IS NOT NULL OR question_type IS NOT NULL "
            "OR business_object IS NOT NULL OR purpose IS NOT NULL "
            "OR customer_type IS NOT NULL OR feature_explanation IS NOT NULL "
            "OR example IS NOT NULL OR internal_notes IS NOT NULL "
            "OR json_array_length(attachments) > 0 "
            "OR json_array_length(web_links) > 0",
            name="ck_knowledge_draft_has_content",
        ),
        CheckConstraint(
            "candidate_fingerprint IS NULL OR length(candidate_fingerprint) = 64",
            name="ck_knowledge_draft_candidate_fingerprint_sha256",
        ),
        UniqueConstraint(
            "ingestion_batch_id",
            "candidate_fingerprint",
            name="uq_knowledge_draft_ingestion_candidate",
        ),
        UniqueConstraint(
            "attachment_ingestion_batch_id",
            "candidate_fingerprint",
            name="uq_knowledge_draft_attachment_candidate",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source: Mapped[KnowledgeDraftSource] = mapped_column(
        SqlEnum(
            KnowledgeDraftSource,
            name="knowledge_draft_source",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [value.value for value in enum_class],
        ),
        nullable=False,
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("parent.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    ingestion_batch_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("intelligent_ingestion_batch.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    attachment_ingestion_batch_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("attachment_ingestion_batch.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    candidate_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_variants: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    follow_up_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    business_object: Mapped[str | None] = mapped_column(String(255), nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    feature_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    example: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    web_links: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    knowledge_base_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
