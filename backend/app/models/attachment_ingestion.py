"""Durable state for DOC/DOCX attachment-to-knowledge imports."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class AttachmentIngestionBatchStatus(str, Enum):
    PROCESSING = "processing"
    READY = "ready"
    READY_WITH_WARNINGS = "ready_with_warnings"
    FAILED = "failed"
    SUBMITTED = "submitted"


class AttachmentIngestionBatch(Base):
    """One owner-private, resumable attachment parsing attempt.

    The original file remains in the regular evidence-attachment store.  The
    extracted document body is deliberately never persisted: a retry extracts
    it again from that source object, while the durable proposal is sufficient
    for the user-facing confirmation flow.
    """

    __tablename__ = "attachment_ingestion_batch"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_attachment_ingestion_batch_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "max_attempts >= 1",
            name="ck_attachment_ingestion_batch_max_attempts_positive",
        ),
        CheckConstraint(
            "status NOT IN ('ready', 'ready_with_warnings', 'failed', 'submitted') "
            "OR lease_owner IS NULL",
            name="ck_attachment_ingestion_batch_finished_not_leased",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attachment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evidence_attachment.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    status: Mapped[AttachmentIngestionBatchStatus] = mapped_column(
        SqlEnum(
            AttachmentIngestionBatchStatus,
            name="attachment_ingestion_batch_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [value.value for value in enum_class],
        ),
        nullable=False,
        default=AttachmentIngestionBatchStatus.PROCESSING,
        index=True,
    )
    # JSON-serializable ``AttachmentImportProposal``.  It is cleared after a
    # successful confirmation, because it is temporary derived data.
    proposal: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
    )
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    image_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extracted_char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_submission_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_submission.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    final_parent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("parent.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
