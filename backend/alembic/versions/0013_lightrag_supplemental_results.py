"""persist immutable LightRAG supplemental-result snapshots

Revision ID: 0013_lightrag_supplemental
Revises: 0012_result_annotations
Create Date: 2026-08-31 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_lightrag_supplemental"
down_revision: str | None = "0012_result_annotations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RESULT_KIND = sa.Enum(
    "knowledge",
    "supplement",
    name="search_result_kind",
    native_enum=False,
    # The shape constraint below also restricts the discriminator values. A
    # second implicit enum check complicates SQLite's batch migration without
    # adding protection.
    create_constraint=False,
)


def upgrade() -> None:
    # Existing rows are all platform knowledge results.  Add their explicit
    # discriminator before relaxing the platform-revision foreign keys.
    op.add_column(
        "search_result_item",
        sa.Column(
            "result_kind",
            _RESULT_KIND,
            nullable=False,
            server_default="knowledge",
        ),
    )
    op.add_column(
        "search_result_item",
        sa.Column("supplement_source_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "search_result_item",
        sa.Column("supplement_title", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "search_result_item",
        sa.Column("supplement_content", sa.Text(), nullable=True),
    )
    op.add_column(
        "search_result_item",
        sa.Column("supplement_citation_metadata", sa.JSON(), nullable=True),
    )
    op.add_column(
        "search_event",
        sa.Column("supplemental_retrieval_status", sa.String(length=40), nullable=True),
    )
    with op.batch_alter_table("search_result_item") as batch_op:
        batch_op.alter_column(
            "result_kind",
            existing_type=_RESULT_KIND,
            server_default=None,
        )
        for column_name in (
            "child_id",
            "knowledge_base_id",
            "child_revision_id",
            "parent_id",
            "parent_revision_id",
        ):
            batch_op.alter_column(column_name, existing_type=sa.Uuid(), nullable=True)
        batch_op.create_check_constraint(
            "ck_search_result_item_kind_shape",
            "(result_kind = 'knowledge' "
            "AND child_id IS NOT NULL AND knowledge_base_id IS NOT NULL "
            "AND child_revision_id IS NOT NULL AND parent_id IS NOT NULL "
            "AND parent_revision_id IS NOT NULL "
            "AND supplement_source_hash IS NULL AND supplement_title IS NULL "
            "AND supplement_content IS NULL AND supplement_citation_metadata IS NULL) "
            "OR (result_kind = 'supplement' "
            "AND child_id IS NULL AND knowledge_base_id IS NULL "
            "AND child_revision_id IS NULL AND parent_id IS NULL "
            "AND parent_revision_id IS NULL "
            "AND supplement_source_hash IS NOT NULL AND supplement_title IS NOT NULL "
            "AND supplement_content IS NOT NULL)",
        )


def downgrade() -> None:
    # A downgrade intentionally refuses to erase persisted supplemental search
    # history.  Operators can choose to remove those rows explicitly before
    # rolling back this schema revision.
    connection = op.get_bind()
    supplemental_count = connection.execute(
        sa.text("SELECT count(*) FROM search_result_item WHERE result_kind = 'supplement'")
    ).scalar_one()
    if supplemental_count:
        raise RuntimeError(
            "cannot downgrade while supplemental search-result snapshots exist"
        )
    with op.batch_alter_table("search_result_item") as batch_op:
        batch_op.drop_constraint("ck_search_result_item_kind_shape", type_="check")
        for column_name in (
            "parent_revision_id",
            "parent_id",
            "child_revision_id",
            "knowledge_base_id",
            "child_id",
        ):
            batch_op.alter_column(column_name, existing_type=sa.Uuid(), nullable=False)
    op.drop_column("search_result_item", "supplement_citation_metadata")
    op.drop_column("search_result_item", "supplement_content")
    op.drop_column("search_result_item", "supplement_title")
    op.drop_column("search_result_item", "supplement_source_hash")
    op.drop_column("search_result_item", "result_kind")
    op.drop_column("search_event", "supplemental_retrieval_status")
