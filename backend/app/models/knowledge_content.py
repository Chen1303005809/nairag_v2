from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class ParentLexicalRuleType(str, Enum):
    ALIAS = "alias"
    REGEX = "regex"


class ReviewSubmissionKind(str, Enum):
    PARENT_WITH_PRIMARY = "parent_with_primary"
    CHILD = "child"


class ReviewSubmissionStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    INDEXING = "indexing"
    PUBLISHED = "published"
    REJECTED = "rejected"
    INDEX_FAILED = "index_failed"


class ReviewTargetStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    INDEXING = "indexing"
    PUBLISHED = "published"
    INDEX_FAILED = "index_failed"


class ReviewDecisionKind(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ChildPublicationStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class IndexJobKind(str, Enum):
    INDEX_TARGET = "index_target"
    CLEAN_PUBLICATION = "clean_publication"


class IndexJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SearchQueryMode(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    MIXED = "mixed"


class Parent(Base):
    """Global parent identity. Its mutable business content belongs to revisions."""

    __tablename__ = "parent"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ParentRevision(Base):
    """An immutable snapshot of a parent's displayed name and lexical rules."""

    __tablename__ = "parent_revision"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("parent_id", "revision_number", name="uq_parent_revision_parent_number"),
        CheckConstraint("revision_number >= 1", name="ck_parent_revision_number_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    parent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("parent.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    canonical_keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ParentLexicalRule(Base):
    """A revision-scoped alias or controlled regular expression."""

    __tablename__ = "parent_lexical_rule"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint(
            "parent_revision_id",
            "sort_order",
            name="uq_parent_lexical_rule_revision_order",
        ),
        UniqueConstraint(
            "parent_revision_id",
            "rule_type",
            "rule_value",
            name="uq_parent_lexical_rule_revision_value",
        ),
        CheckConstraint("sort_order >= 0", name="ck_parent_lexical_rule_order_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    parent_revision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("parent_revision.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rule_type: Mapped[ParentLexicalRuleType] = mapped_column(
        SqlEnum(
            ParentLexicalRuleType,
            name="parent_lexical_rule_type",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [rule.value for rule in enum_class],
        ),
        nullable=False,
    )
    rule_value: Mapped[str] = mapped_column(String(512), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Child(Base):
    """Stable identity for a primary or ordinary child under one parent."""

    __tablename__ = "child"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        Index(
            "uq_child_one_primary_per_parent",
            "parent_id",
            unique=True,
            postgresql_where=text("is_primary"),
            sqlite_where=text("is_primary"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    parent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("parent.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChildRevision(Base):
    """An immutable content snapshot for one child."""

    __tablename__ = "child_revision"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("child_id", "revision_number", name="uq_child_revision_child_number"),
        CheckConstraint("revision_number >= 1", name="ck_child_revision_number_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    child_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    response_content: Mapped[str] = mapped_column(Text, nullable=False)
    follow_up_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    business_object: Mapped[str | None] = mapped_column(String(255), nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    feature_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    example: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChildRevisionQuestionVariant(Base):
    """A stable, ordered alternative question for a child revision."""

    __tablename__ = "child_revision_question_variant"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint(
            "child_revision_id",
            "sort_order",
            name="uq_child_revision_question_variant_order",
        ),
        UniqueConstraint(
            "child_revision_id",
            "question_text",
            name="uq_child_revision_question_variant_text",
        ),
        CheckConstraint(
            "sort_order >= 0",
            name="ck_child_revision_question_variant_order_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    child_revision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child_revision.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EvidenceAttachment(Base):
    """A revision-scoped reference to an attachment shown with a knowledge answer.

    An uploaded attachment starts unbound, then is atomically attached to an
    immutable child revision when that revision is submitted. A later revision
    receives a new metadata row that can safely reference the same stored object.
    """

    __tablename__ = "evidence_attachment"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint(
            "child_revision_id",
            "sort_order",
            name="uq_evidence_attachment_revision_order",
        ),
        CheckConstraint(
            "(child_revision_id IS NULL AND sort_order IS NULL) "
            "OR (child_revision_id IS NOT NULL AND sort_order >= 0)",
            name="ck_evidence_attachment_binding_shape",
        ),
        CheckConstraint("size_bytes > 0", name="ck_evidence_attachment_size_positive"),
        CheckConstraint(
            "length(checksum_sha256) = 64",
            name="ck_evidence_attachment_checksum_length",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    child_revision_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child_revision.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WebLink(Base):
    """A revision-scoped related web link shown with a knowledge answer."""

    __tablename__ = "web_link"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint(
            "child_revision_id",
            "sort_order",
            name="uq_web_link_revision_order",
        ),
        UniqueConstraint(
            "child_revision_id",
            "url",
            name="uq_web_link_revision_url",
        ),
        CheckConstraint("sort_order >= 0", name="ck_web_link_order_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    child_revision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child_revision.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewSubmission(Base):
    """A candidate revision unit submitted for one or more knowledge bases."""

    __tablename__ = "review_submission"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint(
            "(submission_kind = 'parent_with_primary' AND parent_revision_id IS NOT NULL) "
            "OR (submission_kind = 'child' AND parent_revision_id IS NULL)",
            name="ck_review_submission_revision_shape",
        ),
        Index(
            "uq_review_submission_open_parent_aggregate",
            "parent_id",
            unique=True,
            postgresql_where=text(
                "submission_kind = 'parent_with_primary' "
                "AND status IN ('pending_review', 'indexing', 'index_failed')"
            ),
            sqlite_where=text(
                "submission_kind = 'parent_with_primary' "
                "AND status IN ('pending_review', 'indexing', 'index_failed')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    submission_kind: Mapped[ReviewSubmissionKind] = mapped_column(
        SqlEnum(
            ReviewSubmissionKind,
            name="review_submission_kind",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [kind.value for kind in enum_class],
        ),
        nullable=False,
    )
    status: Mapped[ReviewSubmissionStatus] = mapped_column(
        SqlEnum(
            ReviewSubmissionStatus,
            name="review_submission_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [value.value for value in enum_class],
        ),
        nullable=False,
        default=ReviewSubmissionStatus.PENDING_REVIEW,
    )
    parent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("parent.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    parent_revision_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("parent_revision.id", ondelete="RESTRICT"),
        nullable=True,
    )
    child_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    child_revision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    submitted_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class ReviewSubmissionTarget(Base):
    """One knowledge-base review target belonging to a candidate submission."""

    __tablename__ = "review_submission_target"
    __mapper_args__ = {"eager_defaults": True}

    review_submission_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_submission.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_base.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )
    status: Mapped[ReviewTargetStatus] = mapped_column(
        SqlEnum(
            ReviewTargetStatus,
            name="review_target_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [value.value for value in enum_class],
        ),
        nullable=False,
        default=ReviewTargetStatus.PENDING_REVIEW,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ReviewDecision(Base):
    """The immutable decision made for one submission target."""

    __tablename__ = "review_decision"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint(
            "review_submission_id",
            "knowledge_base_id",
            name="uq_review_decision_submission_target",
        ),
        ForeignKeyConstraint(
            ["review_submission_id", "knowledge_base_id"],
            [
                "review_submission_target.review_submission_id",
                "review_submission_target.knowledge_base_id",
            ],
            name="fk_review_decision_submission_target",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    review_submission_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, index=True
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    decision: Mapped[ReviewDecisionKind] = mapped_column(
        SqlEnum(
            ReviewDecisionKind,
            name="review_decision_kind",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [value.value for value in enum_class],
        ),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class ChildKnowledgeBasePublication(Base):
    """The durable publication slot for a child in one knowledge base."""

    __tablename__ = "child_knowledge_base_publication"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint(
            "helpful_count >= 0",
            name="ck_child_publication_helpful_count_nonnegative",
        ),
        CheckConstraint(
            "status != 'published' OR active_revision_id IS NOT NULL",
            name="ck_child_publication_published_requires_active_revision",
        ),
    )

    child_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_base.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    status: Mapped[ChildPublicationStatus] = mapped_column(
        SqlEnum(
            ChildPublicationStatus,
            name="child_publication_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [value.value for value in enum_class],
        ),
        nullable=False,
        default=ChildPublicationStatus.PENDING,
    )
    active_revision_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child_revision.id", ondelete="RESTRICT"),
        nullable=True,
    )
    pending_submission_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_submission.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    helpful_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class IndexJob(Base):
    """Durable outbox/worker record for derived index operations."""

    __tablename__ = "index_job"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_index_job_idempotency_key"),
        CheckConstraint("attempt_count >= 0", name="ck_index_job_attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="ck_index_job_max_attempts_positive"),
        CheckConstraint(
            "job_kind != 'index_target' OR "
            "(review_submission_id IS NOT NULL AND knowledge_base_id IS NOT NULL "
            "AND child_id IS NOT NULL AND child_revision_id IS NOT NULL)",
            name="ck_index_job_target_fields_required",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    job_kind: Mapped[IndexJobKind] = mapped_column(
        SqlEnum(
            IndexJobKind,
            name="index_job_kind",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [value.value for value in enum_class],
        ),
        nullable=False,
    )
    status: Mapped[IndexJobStatus] = mapped_column(
        SqlEnum(
            IndexJobStatus,
            name="index_job_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [value.value for value in enum_class],
        ),
        nullable=False,
        default=IndexJobStatus.PENDING,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    review_submission_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_submission.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    knowledge_base_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_base.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    child_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    child_revision_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child_revision.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SearchEvent(Base):
    """A persisted search context used for result tracing and feedback idempotency."""

    __tablename__ = "search_event"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    query_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_keywords: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(nullable=True)
    ocr_model_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ocr_image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    query_mode: Mapped[SearchQueryMode] = mapped_column(
        SqlEnum(
            SearchQueryMode,
            name="search_query_mode",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [value.value for value in enum_class],
        ),
        nullable=False,
        default=SearchQueryMode.TEXT,
    )
    knowledge_base_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_base.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    no_match: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class SearchResultItem(Base):
    """The exact published revision returned for one search event."""

    __tablename__ = "search_result_item"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("search_event_id", "rank", name="uq_search_result_item_event_rank"),
        UniqueConstraint(
            "search_event_id",
            "child_id",
            "knowledge_base_id",
            "child_revision_id",
            name="uq_search_result_item_event_revision",
        ),
        CheckConstraint("rank >= 1", name="ck_search_result_item_rank_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    search_event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("search_event.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(nullable=False)
    child_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_base.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    child_revision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("parent.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    parent_revision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("parent_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    match_reason: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class HelpfulFeedbackEvent(Base):
    """An explicit, idempotent helpful signal for a returned result."""

    __tablename__ = "helpful_feedback_event"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "search_event_id",
            "child_id",
            "knowledge_base_id",
            "child_revision_id",
            name="uq_helpful_feedback_user_event_revision",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    search_event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("search_event.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    search_result_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("search_result_item.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    child_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child.id", ondelete="RESTRICT"),
        nullable=False,
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_base.id", ondelete="RESTRICT"),
        nullable=False,
    )
    child_revision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("child_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
