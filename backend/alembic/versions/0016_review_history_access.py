"""add per-user review history access grants

Revision ID: 0016_review_history_access
Revises: 0015_knowledge_taxonomy
Create Date: 2026-10-09 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_review_history_access"
down_revision: str | None = "0015_knowledge_taxonomy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_history_access",
        sa.Column("viewer_user_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_user_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "viewer_user_id != reviewer_user_id",
            name="ck_review_history_access_not_self",
        ),
        sa.ForeignKeyConstraint(
            ["viewer_user_id"],
            ["user_account.id"],
            name="fk_review_history_access_viewer_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"],
            ["user_account.id"],
            name="fk_review_history_access_reviewer_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["user_account.id"],
            name="fk_review_history_access_granted_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "viewer_user_id", "reviewer_user_id", name="pk_review_history_access"
        ),
    )
    op.create_index(
        "ix_review_history_access_reviewer_user_id",
        "review_history_access",
        ["reviewer_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_review_history_access_reviewer_user_id",
        table_name="review_history_access",
    )
    op.drop_table("review_history_access")
