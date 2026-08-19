"""create knowledge base and reviewer authorization tables

Revision ID: 0002_knowledge_bases_and_reviewer_assignments
Revises: 0001_account_authentication
Create Date: 2026-08-19 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_knowledge_bases"
down_revision: str | None = "0001_account_authentication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_base",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("logical_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("current_collection_generation", sa.Integer(), nullable=False),
        sa.Column("current_physical_collection_name", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(logical_key) >= 3", name="ck_knowledge_base_logical_key_min_length"
        ),
        sa.CheckConstraint(
            "current_collection_generation >= 1",
            name="ck_knowledge_base_current_collection_generation_positive",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_account.id"],
            name="fk_knowledge_base_created_by_user_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_base"),
        sa.UniqueConstraint("logical_key", name="uq_knowledge_base_logical_key"),
        sa.UniqueConstraint(
            "current_physical_collection_name",
            name="uq_knowledge_base_current_physical_collection_name",
        ),
    )
    op.create_index("ix_knowledge_base_is_active", "knowledge_base", ["is_active"], unique=False)
    op.create_index(
        "ix_knowledge_base_logical_key",
        "knowledge_base",
        ["logical_key"],
        unique=False,
    )

    op.create_table(
        "reviewer_knowledge_base",
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_user_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"],
            ["user_account.id"],
            name="fk_reviewer_knowledge_base_assigned_by_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_base.id"],
            name="fk_reviewer_knowledge_base_knowledge_base_id",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"],
            ["user_account.id"],
            name="fk_reviewer_knowledge_base_reviewer_user_id",
        ),
        sa.PrimaryKeyConstraint(
            "knowledge_base_id",
            "reviewer_user_id",
            name="pk_reviewer_knowledge_base",
        ),
    )
    op.create_index(
        "ix_reviewer_knowledge_base_reviewer_user_id",
        "reviewer_knowledge_base",
        ["reviewer_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reviewer_knowledge_base_reviewer_user_id",
        table_name="reviewer_knowledge_base",
    )
    op.drop_table("reviewer_knowledge_base")
    op.drop_index("ix_knowledge_base_logical_key", table_name="knowledge_base")
    op.drop_index("ix_knowledge_base_is_active", table_name="knowledge_base")
    op.drop_table("knowledge_base")
