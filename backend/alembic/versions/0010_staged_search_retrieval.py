"""store staged retrieval decisions and degradation state

Revision ID: 0010_staged_search
Revises: 0009_search_explainability
Create Date: 2026-08-27 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_staged_search"
down_revision: str | None = "0009_search_explainability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "search_event",
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("search_event", sa.Column("degradation_reasons", sa.JSON(), nullable=True))
    op.add_column("search_result_item", sa.Column("hybrid_score", sa.Float(), nullable=True))
    op.add_column("search_result_item", sa.Column("rerank_score", sa.Float(), nullable=True))
    op.add_column(
        "search_result_item",
        sa.Column("selection_stage", sa.String(length=40), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "search_result_item",
        sa.Column("helpful_count_at_search", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("search_result_item", "helpful_count_at_search")
    op.drop_column("search_result_item", "selection_stage")
    op.drop_column("search_result_item", "rerank_score")
    op.drop_column("search_result_item", "hybrid_score")
    op.drop_column("search_event", "degradation_reasons")
    op.drop_column("search_event", "degraded")
