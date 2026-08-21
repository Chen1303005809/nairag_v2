"""add creator-private drafts and intelligent ingestion batches

Revision ID: 0008_fast_upload_drafts
Revises: 0007_child_revision_evidence
Create Date: 2026-08-21 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_fast_upload_drafts"
down_revision: str | None = "0007_child_revision_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intelligent_ingestion_batch",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "processing",
                "completed",
                "completed_with_warnings",
                "failed",
                name="intelligent_ingestion_batch_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("normalized_messages", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("generated_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("rejection_reasons", sa.JSON(), nullable=False),
        sa.Column("model_version", sa.String(length=120), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_input_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_ingestion_batch_attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name="ck_ingestion_batch_max_attempts_positive",
        ),
        sa.CheckConstraint(
            "status NOT IN ('completed', 'completed_with_warnings', 'failed') "
            "OR normalized_messages IS NULL",
            name="ck_ingestion_batch_finished_raw_input_purged",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["user_account.id"],
            name="fk_ingestion_batch_owner_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_intelligent_ingestion_batch_owner_user_id",
        "intelligent_ingestion_batch",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_intelligent_ingestion_batch_status",
        "intelligent_ingestion_batch",
        ["status"],
    )
    op.create_index(
        "ix_intelligent_ingestion_batch_available_at",
        "intelligent_ingestion_batch",
        ["available_at"],
    )
    op.create_index(
        "ix_intelligent_ingestion_batch_created_at",
        "intelligent_ingestion_batch",
        ["created_at"],
    )

    op.create_table(
        "knowledge_draft",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "manual_saved",
                "intelligent_generated",
                name="knowledge_draft_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("ingestion_batch_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("response_content", sa.Text(), nullable=True),
        sa.Column("question_variants", sa.JSON(), nullable=False),
        sa.Column("follow_up_guidance", sa.Text(), nullable=True),
        sa.Column("question_type", sa.String(length=255), nullable=True),
        sa.Column("business_object", sa.String(length=255), nullable=True),
        sa.Column("purpose", sa.String(length=255), nullable=True),
        sa.Column("customer_type", sa.String(length=255), nullable=True),
        sa.Column("feature_explanation", sa.Text(), nullable=True),
        sa.Column("example", sa.Text(), nullable=True),
        sa.Column("internal_notes", sa.Text(), nullable=True),
        sa.Column("attachments", sa.JSON(), nullable=False),
        sa.Column("web_links", sa.JSON(), nullable=False),
        sa.Column("knowledge_base_ids", sa.JSON(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_version", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "candidate_fingerprint IS NULL OR length(candidate_fingerprint) = 64",
            name="ck_knowledge_draft_candidate_fingerprint_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["user_account.id"],
            name="fk_knowledge_draft_owner_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["parent.id"],
            name="fk_knowledge_draft_parent_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_batch_id"],
            ["intelligent_ingestion_batch.id"],
            name="fk_knowledge_draft_ingestion_batch_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "ingestion_batch_id",
            "candidate_fingerprint",
            name="uq_knowledge_draft_ingestion_candidate",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_draft_owner_user_id", "knowledge_draft", ["owner_user_id"])
    op.create_index("ix_knowledge_draft_parent_id", "knowledge_draft", ["parent_id"])
    op.create_index(
        "ix_knowledge_draft_ingestion_batch_id", "knowledge_draft", ["ingestion_batch_id"]
    )
    op.create_index("ix_knowledge_draft_created_at", "knowledge_draft", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_draft_created_at", table_name="knowledge_draft")
    op.drop_index("ix_knowledge_draft_ingestion_batch_id", table_name="knowledge_draft")
    op.drop_index("ix_knowledge_draft_parent_id", table_name="knowledge_draft")
    op.drop_index("ix_knowledge_draft_owner_user_id", table_name="knowledge_draft")
    op.drop_table("knowledge_draft")
    op.drop_index(
        "ix_intelligent_ingestion_batch_created_at", table_name="intelligent_ingestion_batch"
    )
    op.drop_index(
        "ix_intelligent_ingestion_batch_available_at", table_name="intelligent_ingestion_batch"
    )
    op.drop_index("ix_intelligent_ingestion_batch_status", table_name="intelligent_ingestion_batch")
    op.drop_index(
        "ix_intelligent_ingestion_batch_owner_user_id", table_name="intelligent_ingestion_batch"
    )
    op.drop_table("intelligent_ingestion_batch")
