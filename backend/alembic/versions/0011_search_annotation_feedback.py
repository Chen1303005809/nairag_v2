"""add search interactions and immutable search annotations

Revision ID: 0011_search_annotation
Revises: 0010_staged_search
Create Date: 2026-08-28 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_search_annotation"
down_revision: str | None = "0010_staged_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_interaction",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "interaction_type",
            sa.Enum(
                "vector",
                "quick_search",
                name="search_interaction_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=True),
        sa.Column("no_match", sa.Boolean(), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("degradation_reasons", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.id"],
            name="fk_search_interaction_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_base.id"],
            name="fk_search_interaction_knowledge_base_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_interaction"),
    )
    op.create_index("ix_search_interaction_user_id", "search_interaction", ["user_id"])
    op.create_index(
        "ix_search_interaction_knowledge_base_id",
        "search_interaction",
        ["knowledge_base_id"],
    )
    op.create_index("ix_search_interaction_created_at", "search_interaction", ["created_at"])

    # Batch mode recreates the table on SQLite (which cannot add these
    # constraints in place), while keeping PostgreSQL on ordinary ALTER TABLE
    # statements.  Forcing recreation on PostgreSQL would conflict with the
    # existing result and helpful-feedback foreign keys that reference
    # search_event.
    with op.batch_alter_table("search_event") as batch_op:
        batch_op.add_column(sa.Column("search_interaction_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("query_order", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_search_event_search_interaction_id",
            "search_interaction",
            ["search_interaction_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_search_event_interaction_query_order",
            ["search_interaction_id", "query_order"],
        )
        batch_op.create_check_constraint(
            "ck_search_event_interaction_query_order",
            "(search_interaction_id IS NULL AND query_order IS NULL) OR "
            "(search_interaction_id IS NOT NULL AND query_order IS NOT NULL AND query_order >= 1)",
        )
    op.create_index(
        "ix_search_event_search_interaction_id",
        "search_event",
        ["search_interaction_id"],
    )

    op.create_table(
        "search_annotation_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("search_interaction_id", sa.Uuid(), nullable=False),
        sa.Column(
            "feedback_type",
            sa.Enum(
                "high_score_irrelevant",
                "low_score_relevant",
                "other",
                name="search_annotation_feedback_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("other_note", sa.Text(), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(feedback_type = 'other' AND other_note IS NOT NULL "
            "AND length(trim(other_note)) BETWEEN 1 AND 4000) OR "
            "(feedback_type IN ('high_score_irrelevant', 'low_score_relevant') "
            "AND other_note IS NULL)",
            name="ck_search_annotation_feedback_other_note",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["user_account.id"],
            name="fk_search_annotation_feedback_submitted_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["search_interaction_id"],
            ["search_interaction.id"],
            name="fk_search_annotation_feedback_search_interaction_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_annotation_feedback"),
        sa.UniqueConstraint(
            "search_interaction_id",
            name="uq_search_annotation_feedback_interaction",
        ),
    )
    op.create_index(
        "ix_search_annotation_feedback_submitted_by_user_id",
        "search_annotation_feedback",
        ["submitted_by_user_id"],
    )
    op.create_index(
        "ix_search_annotation_feedback_search_interaction_id",
        "search_annotation_feedback",
        ["search_interaction_id"],
    )
    op.create_index(
        "ix_search_annotation_feedback_submitted_at",
        "search_annotation_feedback",
        ["submitted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_search_annotation_feedback_submitted_at", table_name="search_annotation_feedback")
    op.drop_index(
        "ix_search_annotation_feedback_search_interaction_id",
        table_name="search_annotation_feedback",
    )
    op.drop_index(
        "ix_search_annotation_feedback_submitted_by_user_id",
        table_name="search_annotation_feedback",
    )
    op.drop_table("search_annotation_feedback")

    op.drop_index("ix_search_event_search_interaction_id", table_name="search_event")
    with op.batch_alter_table("search_event") as batch_op:
        batch_op.drop_constraint("ck_search_event_interaction_query_order", type_="check")
        batch_op.drop_constraint("uq_search_event_interaction_query_order", type_="unique")
        batch_op.drop_constraint("fk_search_event_search_interaction_id", type_="foreignkey")
        batch_op.drop_column("query_order")
        batch_op.drop_column("search_interaction_id")

    op.drop_index("ix_search_interaction_created_at", table_name="search_interaction")
    op.drop_index("ix_search_interaction_knowledge_base_id", table_name="search_interaction")
    op.drop_index("ix_search_interaction_user_id", table_name="search_interaction")
    op.drop_table("search_interaction")
