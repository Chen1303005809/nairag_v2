"""record trusted OCR metadata on search events

Revision ID: 0006_ocr_search_metadata
Revises: 0005_index_jobs
Create Date: 2026-08-20 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_ocr_search_metadata"
down_revision: str | None = "0005_index_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("search_event", sa.Column("ocr_keywords", sa.JSON(), nullable=True))
    op.add_column("search_event", sa.Column("ocr_confidence", sa.Float(), nullable=True))
    op.add_column(
        "search_event",
        sa.Column("ocr_model_version", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "search_event",
        sa.Column("ocr_image_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("search_event", "ocr_image_sha256")
    op.drop_column("search_event", "ocr_model_version")
    op.drop_column("search_event", "ocr_confidence")
    op.drop_column("search_event", "ocr_keywords")
