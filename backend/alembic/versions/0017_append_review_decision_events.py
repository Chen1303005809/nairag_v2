"""allow append-only review decision history and withdrawn targets

Revision ID: 0017_review_decision_events
Revises: 0016_review_history_access
Create Date: 2026-10-16 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_review_decision_events"
down_revision: str | None = "0016_review_history_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("review_decision") as batch_op:
        batch_op.drop_constraint("uq_review_decision_submission_target", type_="unique")
        batch_op.drop_constraint("fk_review_decision_submission_target", type_="foreignkey")
        batch_op.create_index(
            "ix_review_decision_target_history",
            ["review_submission_id", "knowledge_base_id", "decided_at"],
            unique=False,
        )

    with op.batch_alter_table("review_submission_target") as batch_op:
        batch_op.drop_constraint("review_target_status", type_="check")
        batch_op.create_check_constraint(
            "review_target_status",
            "status IN ('pending_review', 'approved', 'rejected', 'withdrawn', "
            "'indexing', 'published', 'index_failed')",
        )

    with op.batch_alter_table("review_decision") as batch_op:
        batch_op.create_foreign_key(
            "fk_review_decision_submission_target",
            "review_submission_target",
            ["review_submission_id", "knowledge_base_id"],
            ["review_submission_id", "knowledge_base_id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    connection = op.get_bind()
    duplicate_event = connection.execute(
        sa.text(
            "SELECT 1 FROM review_decision "
            "GROUP BY review_submission_id, knowledge_base_id "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    withdrawn_target = connection.execute(
        sa.text("SELECT 1 FROM review_submission_target WHERE status = 'withdrawn' LIMIT 1")
    ).first()
    if duplicate_event is not None or withdrawn_target is not None:
        raise RuntimeError(
            "cannot downgrade append-only review history while duplicate decisions or "
            "withdrawn targets exist"
        )

    with op.batch_alter_table("review_decision") as batch_op:
        batch_op.drop_constraint("fk_review_decision_submission_target", type_="foreignkey")
        batch_op.drop_index("ix_review_decision_target_history")
        batch_op.create_unique_constraint(
            "uq_review_decision_submission_target",
            ["review_submission_id", "knowledge_base_id"],
        )

    with op.batch_alter_table("review_submission_target") as batch_op:
        batch_op.drop_constraint("review_target_status", type_="check")
        batch_op.create_check_constraint(
            "review_target_status",
            "status IN ('pending_review', 'approved', 'rejected', 'indexing', 'published', "
            "'index_failed')",
        )

    with op.batch_alter_table("review_decision") as batch_op:
        batch_op.create_foreign_key(
            "fk_review_decision_submission_target",
            "review_submission_target",
            ["review_submission_id", "knowledge_base_id"],
            ["review_submission_id", "knowledge_base_id"],
            ondelete="RESTRICT",
        )
