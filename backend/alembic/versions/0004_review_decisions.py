"""add immutable review decisions

Revision ID: 0004_review_decisions
Revises: 0003_knowledge_content_and_submissions
Create Date: 2026-08-19 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_review_decisions"
down_revision: str | None = "0003_knowledge_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_decision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("review_submission_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum(
                "approved",
                "rejected",
                name="review_decision_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["user_account.id"],
            name="fk_review_decision_decided_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["review_submission_id", "knowledge_base_id"],
            [
                "review_submission_target.review_submission_id",
                "review_submission_target.knowledge_base_id",
            ],
            name="fk_review_decision_submission_target",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_decision"),
        sa.UniqueConstraint(
            "review_submission_id",
            "knowledge_base_id",
            name="uq_review_decision_submission_target",
        ),
    )
    op.create_index(
        "ix_review_decision_review_submission_id",
        "review_decision",
        ["review_submission_id"],
        unique=False,
    )
    op.create_index(
        "ix_review_decision_knowledge_base_id",
        "review_decision",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_index(
        "ix_review_decision_decided_by_user_id",
        "review_decision",
        ["decided_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_review_decision_decided_at",
        "review_decision",
        ["decided_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_review_decision_decided_at", table_name="review_decision")
    op.drop_index("ix_review_decision_decided_by_user_id", table_name="review_decision")
    op.drop_index("ix_review_decision_knowledge_base_id", table_name="review_decision")
    op.drop_index("ix_review_decision_review_submission_id", table_name="review_decision")
    op.drop_table("review_decision")
