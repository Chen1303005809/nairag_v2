"""add durable index jobs and search feedback events

Revision ID: 0005_index_jobs_and_search_events
Revises: 0004_review_decisions
Create Date: 2026-08-19 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_index_jobs_and_search_events"
down_revision: str | None = "0004_review_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "index_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "job_kind",
            sa.Enum(
                "index_target",
                "clean_publication",
                name="index_job_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "succeeded",
                "failed",
                name="index_job_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("review_submission_id", sa.Uuid(), nullable=True),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=True),
        sa.Column("child_id", sa.Uuid(), nullable=True),
        sa.Column("child_revision_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
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
            "attempt_count >= 0", name="ck_index_job_attempt_count_nonnegative"
        ),
        sa.CheckConstraint("max_attempts >= 1", name="ck_index_job_max_attempts_positive"),
        sa.CheckConstraint(
            "job_kind != 'index_target' OR "
            "(review_submission_id IS NOT NULL AND knowledge_base_id IS NOT NULL "
            "AND child_id IS NOT NULL AND child_revision_id IS NOT NULL)",
            name="ck_index_job_target_fields_required",
        ),
        sa.ForeignKeyConstraint(
            ["review_submission_id"],
            ["review_submission.id"],
            name="fk_index_job_review_submission_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_base.id"],
            name="fk_index_job_knowledge_base_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["child_id"],
            ["child.id"],
            name="fk_index_job_child_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["child_revision_id"],
            ["child_revision.id"],
            name="fk_index_job_child_revision_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_index_job"),
        sa.UniqueConstraint("idempotency_key", name="uq_index_job_idempotency_key"),
    )
    op.create_index("ix_index_job_status", "index_job", ["status"], unique=False)
    op.create_index(
        "ix_index_job_review_submission_id", "index_job", ["review_submission_id"], unique=False
    )
    op.create_index(
        "ix_index_job_knowledge_base_id", "index_job", ["knowledge_base_id"], unique=False
    )
    op.create_index("ix_index_job_child_id", "index_job", ["child_id"], unique=False)
    op.create_index(
        "ix_index_job_child_revision_id", "index_job", ["child_revision_id"], unique=False
    )
    op.create_index("ix_index_job_available_at", "index_job", ["available_at"], unique=False)
    op.create_index("ix_index_job_created_at", "index_job", ["created_at"], unique=False)

    op.create_table(
        "search_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=True),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column(
            "query_mode",
            sa.Enum(
                "text",
                "image",
                "mixed",
                name="search_query_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=True),
        sa.Column("no_match", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_account.id"], name="fk_search_event_user_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_base.id"],
            name="fk_search_event_knowledge_base_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_event"),
    )
    op.create_index("ix_search_event_user_id", "search_event", ["user_id"], unique=False)
    op.create_index(
        "ix_search_event_knowledge_base_id", "search_event", ["knowledge_base_id"], unique=False
    )
    op.create_index("ix_search_event_created_at", "search_event", ["created_at"], unique=False)

    op.create_table(
        "search_result_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("search_event_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("child_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("child_revision_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=False),
        sa.Column("parent_revision_id", sa.Uuid(), nullable=False),
        sa.Column("match_reason", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("rank >= 1", name="ck_search_result_item_rank_positive"),
        sa.ForeignKeyConstraint(
            ["search_event_id"],
            ["search_event.id"],
            name="fk_search_result_item_search_event_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["child_id"], ["child.id"], name="fk_search_result_item_child_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_base.id"],
            name="fk_search_result_item_knowledge_base_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["child_revision_id"],
            ["child_revision.id"],
            name="fk_search_result_item_child_revision_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["parent.id"],
            name="fk_search_result_item_parent_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["parent_revision.id"],
            name="fk_search_result_item_parent_revision_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_result_item"),
        sa.UniqueConstraint("search_event_id", "rank", name="uq_search_result_item_event_rank"),
        sa.UniqueConstraint(
            "search_event_id",
            "child_id",
            "knowledge_base_id",
            "child_revision_id",
            name="uq_search_result_item_event_revision",
        ),
    )
    op.create_index(
        "ix_search_result_item_search_event_id",
        "search_result_item",
        ["search_event_id"],
        unique=False,
    )
    op.create_index(
        "ix_search_result_item_child_id",
        "search_result_item",
        ["child_id"],
        unique=False,
    )
    op.create_index(
        "ix_search_result_item_knowledge_base_id",
        "search_result_item",
        ["knowledge_base_id"],
        unique=False,
    )

    op.create_table(
        "helpful_feedback_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("search_event_id", sa.Uuid(), nullable=False),
        sa.Column("search_result_item_id", sa.Uuid(), nullable=False),
        sa.Column("child_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("child_revision_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.id"],
            name="fk_helpful_feedback_event_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["search_event_id"],
            ["search_event.id"],
            name="fk_helpful_feedback_event_search_event_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["search_result_item_id"],
            ["search_result_item.id"],
            name="fk_helpful_feedback_event_search_result_item_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["child_id"],
            ["child.id"],
            name="fk_helpful_feedback_event_child_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_base.id"],
            name="fk_helpful_feedback_event_knowledge_base_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["child_revision_id"],
            ["child_revision.id"],
            name="fk_helpful_feedback_event_child_revision_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_helpful_feedback_event"),
        sa.UniqueConstraint(
            "user_id",
            "search_event_id",
            "child_id",
            "knowledge_base_id",
            "child_revision_id",
            name="uq_helpful_feedback_user_event_revision",
        ),
    )
    op.create_index(
        "ix_helpful_feedback_event_user_id",
        "helpful_feedback_event",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_helpful_feedback_event_search_event_id",
        "helpful_feedback_event",
        ["search_event_id"],
        unique=False,
    )
    op.create_index(
        "ix_helpful_feedback_event_search_result_item_id",
        "helpful_feedback_event",
        ["search_result_item_id"],
        unique=False,
    )
    op.create_index(
        "ix_helpful_feedback_event_created_at",
        "helpful_feedback_event",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_helpful_feedback_event_created_at", table_name="helpful_feedback_event")
    op.drop_index(
        "ix_helpful_feedback_event_search_result_item_id", table_name="helpful_feedback_event"
    )
    op.drop_index("ix_helpful_feedback_event_search_event_id", table_name="helpful_feedback_event")
    op.drop_index("ix_helpful_feedback_event_user_id", table_name="helpful_feedback_event")
    op.drop_table("helpful_feedback_event")
    op.drop_index("ix_search_result_item_knowledge_base_id", table_name="search_result_item")
    op.drop_index("ix_search_result_item_child_id", table_name="search_result_item")
    op.drop_index("ix_search_result_item_search_event_id", table_name="search_result_item")
    op.drop_table("search_result_item")
    op.drop_index("ix_search_event_created_at", table_name="search_event")
    op.drop_index("ix_search_event_knowledge_base_id", table_name="search_event")
    op.drop_index("ix_search_event_user_id", table_name="search_event")
    op.drop_table("search_event")
    op.drop_index("ix_index_job_created_at", table_name="index_job")
    op.drop_index("ix_index_job_available_at", table_name="index_job")
    op.drop_index("ix_index_job_child_revision_id", table_name="index_job")
    op.drop_index("ix_index_job_child_id", table_name="index_job")
    op.drop_index("ix_index_job_knowledge_base_id", table_name="index_job")
    op.drop_index("ix_index_job_review_submission_id", table_name="index_job")
    op.drop_index("ix_index_job_status", table_name="index_job")
    op.drop_table("index_job")
