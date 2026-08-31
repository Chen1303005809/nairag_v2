"""replace interaction-level annotations with completed result-level reviews

Revision ID: 0012_result_annotations
Revises: 0011_search_annotation
Create Date: 2026-08-31 00:00:00

The prior ``search_annotation_feedback`` table is deliberately retained as
legacy audit data. Its aggregate labels cannot be truthfully mapped to one or
more individual visible results, so new reads and writes use the two tables
created below.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_result_annotations"
down_revision: str | None = "0011_search_annotation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_annotation_review",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("search_interaction_id", sa.Uuid(), nullable=False),
        sa.Column("reviewed_result_count", sa.Integer(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reviewed_result_count >= 0",
            name="ck_search_annotation_review_result_count",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["user_account.id"],
            name="fk_search_annotation_review_submitted_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["search_interaction_id"],
            ["search_interaction.id"],
            name="fk_search_annotation_review_search_interaction_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_annotation_review"),
        sa.UniqueConstraint(
            "search_interaction_id",
            name="uq_search_annotation_review_interaction",
        ),
    )
    op.create_index(
        "ix_search_annotation_review_submitted_by_user_id",
        "search_annotation_review",
        ["submitted_by_user_id"],
    )
    op.create_index(
        "ix_search_annotation_review_search_interaction_id",
        "search_annotation_review",
        ["search_interaction_id"],
    )
    op.create_index(
        "ix_search_annotation_review_submitted_at",
        "search_annotation_review",
        ["submitted_at"],
    )

    op.create_table(
        "search_annotation_result_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("search_annotation_review_id", sa.Uuid(), nullable=False),
        sa.Column("search_result_item_id", sa.Uuid(), nullable=False),
        sa.Column(
            "feedback_type",
            sa.Enum(
                "high_score_irrelevant",
                "low_score_relevant",
                "normal",
                "other",
                name="search_annotation_result_label",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("other_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(feedback_type = 'other' AND other_note IS NOT NULL "
            "AND length(trim(other_note)) BETWEEN 1 AND 4000) OR "
            "(feedback_type IN ('high_score_irrelevant', 'low_score_relevant', 'normal') "
            "AND other_note IS NULL)",
            name="ck_search_annotation_result_feedback_other_note",
        ),
        sa.ForeignKeyConstraint(
            ["search_annotation_review_id"],
            ["search_annotation_review.id"],
            name="fk_search_annotation_result_feedback_review_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["search_result_item_id"],
            ["search_result_item.id"],
            name="fk_search_annotation_result_feedback_result_item_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_annotation_result_feedback"),
        sa.UniqueConstraint(
            "search_annotation_review_id",
            "search_result_item_id",
            name="uq_search_annotation_result_feedback_review_result",
        ),
    )
    op.create_index(
        "ix_search_annotation_result_feedback_review_id",
        "search_annotation_result_feedback",
        ["search_annotation_review_id"],
    )
    op.create_index(
        "ix_search_annotation_result_feedback_result_item_id",
        "search_annotation_result_feedback",
        ["search_result_item_id"],
    )
    op.create_index(
        "ix_search_annotation_result_feedback_type",
        "search_annotation_result_feedback",
        ["feedback_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_search_annotation_result_feedback_type",
        table_name="search_annotation_result_feedback",
    )
    op.drop_index(
        "ix_search_annotation_result_feedback_result_item_id",
        table_name="search_annotation_result_feedback",
    )
    op.drop_index(
        "ix_search_annotation_result_feedback_review_id",
        table_name="search_annotation_result_feedback",
    )
    op.drop_table("search_annotation_result_feedback")

    op.drop_index(
        "ix_search_annotation_review_submitted_at",
        table_name="search_annotation_review",
    )
    op.drop_index(
        "ix_search_annotation_review_search_interaction_id",
        table_name="search_annotation_review",
    )
    op.drop_index(
        "ix_search_annotation_review_submitted_by_user_id",
        table_name="search_annotation_review",
    )
    op.drop_table("search_annotation_review")
