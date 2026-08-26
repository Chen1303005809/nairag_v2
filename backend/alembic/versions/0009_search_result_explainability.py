"""Store the field that produced a search result.

Revision ID: 0009_search_explainability
Revises: 0008_fast_upload_drafts
Create Date: 2026-08-25 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_search_explainability"
down_revision: str | None = "0008_fast_upload_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "search_result_item",
        sa.Column("matched_field", sa.String(length=80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("search_result_item", "matched_field")
