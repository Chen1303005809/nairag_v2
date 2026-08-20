"""add revision-scoped knowledge attachments and web links

Revision ID: 0007_child_revision_evidence
Revises: 0006_ocr_search_metadata
Create Date: 2026-08-20 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_child_revision_evidence"
down_revision: str | None = "0006_ocr_search_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_attachment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("child_revision_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(child_revision_id IS NULL AND sort_order IS NULL) "
            "OR (child_revision_id IS NOT NULL AND sort_order >= 0)",
            name="ck_evidence_attachment_binding_shape",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_evidence_attachment_size_positive"),
        sa.CheckConstraint(
            "length(checksum_sha256) = 64",
            name="ck_evidence_attachment_checksum_length",
        ),
        sa.ForeignKeyConstraint(
            ["child_revision_id"],
            ["child_revision.id"],
            name="fk_evidence_attachment_child_revision_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["user_account.id"],
            name="fk_evidence_attachment_uploaded_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_attachment"),
        sa.UniqueConstraint(
            "child_revision_id",
            "sort_order",
            name="uq_evidence_attachment_revision_order",
        ),
    )
    op.create_index(
        "ix_evidence_attachment_child_revision_id",
        "evidence_attachment",
        ["child_revision_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_attachment_uploaded_by_user_id",
        "evidence_attachment",
        ["uploaded_by_user_id"],
        unique=False,
    )
    op.create_table(
        "web_link",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("child_revision_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_web_link_order_nonnegative"),
        sa.ForeignKeyConstraint(
            ["child_revision_id"],
            ["child_revision.id"],
            name="fk_web_link_child_revision_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_web_link"),
        sa.UniqueConstraint(
            "child_revision_id",
            "sort_order",
            name="uq_web_link_revision_order",
        ),
        sa.UniqueConstraint(
            "child_revision_id",
            "url",
            name="uq_web_link_revision_url",
        ),
    )
    op.create_index(
        "ix_web_link_child_revision_id",
        "web_link",
        ["child_revision_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_web_link_child_revision_id", table_name="web_link")
    op.drop_table("web_link")
    op.drop_index("ix_evidence_attachment_child_revision_id", table_name="evidence_attachment")
    op.drop_index("ix_evidence_attachment_uploaded_by_user_id", table_name="evidence_attachment")
    op.drop_table("evidence_attachment")
