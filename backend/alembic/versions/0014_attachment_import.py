"""add durable DOC/DOCX attachment import batches

Revision ID: 0014_attachment_import
Revises: 0013_lightrag_supplemental
Create Date: 2026-09-01 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_attachment_import"
down_revision: str | None = "0013_lightrag_supplemental"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _knowledge_draft_source_check(values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"source IN ({quoted})"


def upgrade() -> None:
    op.create_table(
        "attachment_ingestion_batch",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "processing",
                "ready",
                "ready_with_warnings",
                "failed",
                "submitted",
                name="attachment_ingestion_batch_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("proposal", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("image_count", sa.Integer(), nullable=False),
        sa.Column("extracted_char_count", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(length=120), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_submission_id", sa.Uuid(), nullable=True),
        sa.Column("final_parent_id", sa.Uuid(), nullable=True),
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
            name="ck_attachment_ingestion_batch_attempt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name="ck_attachment_ingestion_batch_max_attempts_positive",
        ),
        sa.CheckConstraint(
            "status NOT IN ('ready', 'ready_with_warnings', 'failed', 'submitted') "
            "OR lease_owner IS NULL",
            name="ck_attachment_ingestion_batch_finished_not_leased",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["user_account.id"],
            name="fk_attachment_ingestion_batch_owner_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["evidence_attachment.id"],
            name="fk_attachment_ingestion_batch_attachment_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["final_submission_id"],
            ["review_submission.id"],
            name="fk_attachment_ingestion_batch_final_submission_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["final_parent_id"],
            ["parent.id"],
            name="fk_attachment_ingestion_batch_final_parent_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attachment_id", name="uq_attachment_ingestion_batch_attachment_id"),
        sa.UniqueConstraint(
            "final_submission_id",
            name="uq_attachment_ingestion_batch_final_submission_id",
        ),
    )
    op.create_index(
        "ix_attachment_ingestion_batch_owner_user_id",
        "attachment_ingestion_batch",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_attachment_ingestion_batch_status",
        "attachment_ingestion_batch",
        ["status"],
    )
    op.create_index(
        "ix_attachment_ingestion_batch_available_at",
        "attachment_ingestion_batch",
        ["available_at"],
    )
    op.create_index(
        "ix_attachment_ingestion_batch_expires_at",
        "attachment_ingestion_batch",
        ["expires_at"],
    )
    op.create_index(
        "ix_attachment_ingestion_batch_created_at",
        "attachment_ingestion_batch",
        ["created_at"],
    )

    with op.batch_alter_table("knowledge_draft") as batch_op:
        batch_op.drop_constraint("knowledge_draft_source", type_="check")
        batch_op.create_check_constraint(
            "knowledge_draft_source",
            _knowledge_draft_source_check(
                ("manual_saved", "intelligent_generated", "attachment_generated")
            ),
        )
        batch_op.add_column(
            sa.Column("attachment_ingestion_batch_id", sa.Uuid(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_knowledge_draft_attachment_ingestion_batch_id",
            "attachment_ingestion_batch",
            ["attachment_ingestion_batch_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_knowledge_draft_attachment_candidate",
            ["attachment_ingestion_batch_id", "candidate_fingerprint"],
        )
        batch_op.create_index(
            "ix_knowledge_draft_attachment_ingestion_batch_id",
            ["attachment_ingestion_batch_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_draft") as batch_op:
        batch_op.drop_index("ix_knowledge_draft_attachment_ingestion_batch_id")
        batch_op.drop_constraint("uq_knowledge_draft_attachment_candidate", type_="unique")
        batch_op.drop_constraint(
            "fk_knowledge_draft_attachment_ingestion_batch_id",
            type_="foreignkey",
        )
        batch_op.drop_column("attachment_ingestion_batch_id")
        batch_op.drop_constraint("knowledge_draft_source", type_="check")
        batch_op.create_check_constraint(
            "knowledge_draft_source",
            _knowledge_draft_source_check(("manual_saved", "intelligent_generated")),
        )

    op.drop_index("ix_attachment_ingestion_batch_created_at", table_name="attachment_ingestion_batch")
    op.drop_index("ix_attachment_ingestion_batch_expires_at", table_name="attachment_ingestion_batch")
    op.drop_index("ix_attachment_ingestion_batch_available_at", table_name="attachment_ingestion_batch")
    op.drop_index("ix_attachment_ingestion_batch_status", table_name="attachment_ingestion_batch")
    op.drop_index("ix_attachment_ingestion_batch_owner_user_id", table_name="attachment_ingestion_batch")
    op.drop_table("attachment_ingestion_batch")
