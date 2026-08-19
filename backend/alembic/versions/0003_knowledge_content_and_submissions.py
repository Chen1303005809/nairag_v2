"""create immutable knowledge content and review submission tables

Revision ID: 0003_knowledge_content_and_submissions
Revises: 0002_knowledge_bases_and_reviewer_assignments
Create Date: 2026-08-19 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_knowledge_content"
down_revision: str | None = "0002_knowledge_bases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "parent",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_account.id"],
            name="fk_parent_created_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_parent"),
    )
    op.create_table(
        "parent_revision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("canonical_keyword", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("revision_number >= 1", name="ck_parent_revision_number_positive"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_account.id"],
            name="fk_parent_revision_created_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["parent.id"],
            name="fk_parent_revision_parent_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_parent_revision"),
        sa.UniqueConstraint(
            "parent_id",
            "revision_number",
            name="uq_parent_revision_parent_number",
        ),
    )
    op.create_index("ix_parent_revision_parent_id", "parent_revision", ["parent_id"], unique=False)
    op.create_table(
        "parent_lexical_rule",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("parent_revision_id", sa.Uuid(), nullable=False),
        sa.Column(
            "rule_type",
            sa.Enum(
                "alias",
                "regex",
                name="parent_lexical_rule_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("rule_value", sa.String(length=512), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_parent_lexical_rule_order_nonnegative"),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["parent_revision.id"],
            name="fk_parent_lexical_rule_parent_revision_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_parent_lexical_rule"),
        sa.UniqueConstraint(
            "parent_revision_id",
            "sort_order",
            name="uq_parent_lexical_rule_revision_order",
        ),
        sa.UniqueConstraint(
            "parent_revision_id",
            "rule_type",
            "rule_value",
            name="uq_parent_lexical_rule_revision_value",
        ),
    )
    op.create_index(
        "ix_parent_lexical_rule_parent_revision_id",
        "parent_lexical_rule",
        ["parent_revision_id"],
        unique=False,
    )
    op.create_table(
        "child",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_account.id"],
            name="fk_child_created_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["parent.id"],
            name="fk_child_parent_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_child"),
    )
    op.create_index("ix_child_parent_id", "child", ["parent_id"], unique=False)
    op.create_index(
        "uq_child_one_primary_per_parent",
        "child",
        ["parent_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
        sqlite_where=sa.text("is_primary"),
    )
    op.create_table(
        "child_revision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("child_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("response_content", sa.Text(), nullable=False),
        sa.Column("follow_up_guidance", sa.Text(), nullable=True),
        sa.Column("question_type", sa.String(length=255), nullable=True),
        sa.Column("business_object", sa.String(length=255), nullable=True),
        sa.Column("purpose", sa.String(length=255), nullable=True),
        sa.Column("customer_type", sa.String(length=255), nullable=True),
        sa.Column("feature_explanation", sa.Text(), nullable=True),
        sa.Column("example", sa.Text(), nullable=True),
        sa.Column("internal_notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("revision_number >= 1", name="ck_child_revision_number_positive"),
        sa.ForeignKeyConstraint(
            ["child_id"],
            ["child.id"],
            name="fk_child_revision_child_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_account.id"],
            name="fk_child_revision_created_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_child_revision"),
        sa.UniqueConstraint("child_id", "revision_number", name="uq_child_revision_child_number"),
    )
    op.create_index("ix_child_revision_child_id", "child_revision", ["child_id"], unique=False)
    op.create_table(
        "child_revision_question_variant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("child_revision_id", sa.Uuid(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_child_revision_question_variant_order_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["child_revision_id"],
            ["child_revision.id"],
            name="fk_child_revision_question_variant_child_revision_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_child_revision_question_variant"),
        sa.UniqueConstraint(
            "child_revision_id",
            "sort_order",
            name="uq_child_revision_question_variant_order",
        ),
        sa.UniqueConstraint(
            "child_revision_id",
            "question_text",
            name="uq_child_revision_question_variant_text",
        ),
    )
    op.create_index(
        "ix_child_revision_question_variant_child_revision_id",
        "child_revision_question_variant",
        ["child_revision_id"],
        unique=False,
    )
    op.create_table(
        "review_submission",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "submission_kind",
            sa.Enum(
                "parent_with_primary",
                "child",
                name="review_submission_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending_review",
                "indexing",
                "published",
                "rejected",
                "index_failed",
                name="review_submission_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("parent_id", sa.Uuid(), nullable=False),
        sa.Column("parent_revision_id", sa.Uuid(), nullable=True),
        sa.Column("child_id", sa.Uuid(), nullable=False),
        sa.Column("child_revision_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(submission_kind = 'parent_with_primary' AND parent_revision_id IS NOT NULL) "
            "OR (submission_kind = 'child' AND parent_revision_id IS NULL)",
            name="ck_review_submission_revision_shape",
        ),
        sa.ForeignKeyConstraint(
            ["child_id"], ["child.id"], name="fk_review_submission_child_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["child_revision_id"],
            ["child_revision.id"],
            name="fk_review_submission_child_revision_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["parent.id"], name="fk_review_submission_parent_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["parent_revision.id"],
            name="fk_review_submission_parent_revision_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["user_account.id"],
            name="fk_review_submission_submitted_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_submission"),
    )
    op.create_index(
        "ix_review_submission_child_id",
        "review_submission",
        ["child_id"],
        unique=False,
    )
    op.create_index(
        "ix_review_submission_parent_id",
        "review_submission",
        ["parent_id"],
        unique=False,
    )
    op.create_index(
        "ix_review_submission_submitted_at",
        "review_submission",
        ["submitted_at"],
        unique=False,
    )
    op.create_index(
        "ix_review_submission_submitted_by_user_id",
        "review_submission",
        ["submitted_by_user_id"],
        unique=False,
    )
    op.create_index(
        "uq_review_submission_open_parent_aggregate",
        "review_submission",
        ["parent_id"],
        unique=True,
        postgresql_where=sa.text(
            "submission_kind = 'parent_with_primary' "
            "AND status IN ('pending_review', 'indexing', 'index_failed')"
        ),
        sqlite_where=sa.text(
            "submission_kind = 'parent_with_primary' "
            "AND status IN ('pending_review', 'indexing', 'index_failed')"
        ),
    )
    op.create_table(
        "review_submission_target",
        sa.Column("review_submission_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending_review",
                "approved",
                "rejected",
                "indexing",
                "published",
                "index_failed",
                name="review_target_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_base.id"],
            name="fk_review_submission_target_knowledge_base_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["review_submission_id"],
            ["review_submission.id"],
            name="fk_review_submission_target_review_submission_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "review_submission_id",
            "knowledge_base_id",
            name="pk_review_submission_target",
        ),
    )
    op.create_index(
        "ix_review_submission_target_knowledge_base_id",
        "review_submission_target",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_table(
        "child_knowledge_base_publication",
        sa.Column("child_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "published",
                "archived",
                name="child_publication_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("active_revision_id", sa.Uuid(), nullable=True),
        sa.Column("pending_submission_id", sa.Uuid(), nullable=True),
        sa.Column("helpful_count", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by_user_id", sa.Uuid(), nullable=True),
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
            "helpful_count >= 0",
            name="ck_child_publication_helpful_count_nonnegative",
        ),
        sa.CheckConstraint(
            "status != 'published' OR active_revision_id IS NOT NULL",
            name="ck_child_publication_published_requires_active_revision",
        ),
        sa.ForeignKeyConstraint(
            ["active_revision_id"],
            ["child_revision.id"],
            name="fk_child_publication_active_revision_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["archived_by_user_id"],
            ["user_account.id"],
            name="fk_child_publication_archived_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["child_id"],
            ["child.id"],
            name="fk_child_publication_child_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_base.id"],
            name="fk_child_publication_knowledge_base_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pending_submission_id"],
            ["review_submission.id"],
            name="fk_child_publication_pending_submission_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "child_id",
            "knowledge_base_id",
            name="pk_child_knowledge_base_publication",
        ),
    )
    op.create_index(
        "ix_child_knowledge_base_publication_pending_submission_id",
        "child_knowledge_base_publication",
        ["pending_submission_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_child_knowledge_base_publication_pending_submission_id",
        table_name="child_knowledge_base_publication",
    )
    op.drop_table("child_knowledge_base_publication")
    op.drop_index(
        "ix_review_submission_target_knowledge_base_id",
        table_name="review_submission_target",
    )
    op.drop_table("review_submission_target")
    op.drop_index("uq_review_submission_open_parent_aggregate", table_name="review_submission")
    op.drop_index("ix_review_submission_submitted_by_user_id", table_name="review_submission")
    op.drop_index("ix_review_submission_submitted_at", table_name="review_submission")
    op.drop_index("ix_review_submission_parent_id", table_name="review_submission")
    op.drop_index("ix_review_submission_child_id", table_name="review_submission")
    op.drop_table("review_submission")
    op.drop_index(
        "ix_child_revision_question_variant_child_revision_id",
        table_name="child_revision_question_variant",
    )
    op.drop_table("child_revision_question_variant")
    op.drop_index("ix_child_revision_child_id", table_name="child_revision")
    op.drop_table("child_revision")
    op.drop_index("uq_child_one_primary_per_parent", table_name="child")
    op.drop_index("ix_child_parent_id", table_name="child")
    op.drop_table("child")
    op.drop_index("ix_parent_lexical_rule_parent_revision_id", table_name="parent_lexical_rule")
    op.drop_table("parent_lexical_rule")
    op.drop_index("ix_parent_revision_parent_id", table_name="parent_revision")
    op.drop_table("parent_revision")
    op.drop_table("parent")
