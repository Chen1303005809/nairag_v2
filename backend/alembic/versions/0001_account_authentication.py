"""create account and audit tables

Revision ID: 0001_account_authentication
Revises:
Create Date: 2026-08-19 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_account_authentication"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_account",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "normal_user",
                "review_admin",
                "system_admin",
                name="user_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint("length(username) >= 3", name="ck_user_account_username_min_length"),
        sa.CheckConstraint("token_version >= 0", name="ck_user_account_token_version_nonnegative"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["user_account.id"], name="fk_user_account_created_by_user_id"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_account"),
        sa.UniqueConstraint("username", name="uq_user_account_username"),
    )
    op.create_index("ix_user_account_username", "user_account", ["username"], unique=False)

    op.create_table(
        "audit_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("target_type", sa.String(length=80), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["user_account.id"], name="fk_audit_event_actor_user_id"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_event"),
    )
    op.create_index("ix_audit_event_actor_user_id", "audit_event", ["actor_user_id"], unique=False)
    op.create_index("ix_audit_event_event_type", "audit_event", ["event_type"], unique=False)
    op.create_index("ix_audit_event_occurred_at", "audit_event", ["occurred_at"], unique=False)
    op.create_index("ix_audit_event_target_id", "audit_event", ["target_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_event_target_id", table_name="audit_event")
    op.drop_index("ix_audit_event_occurred_at", table_name="audit_event")
    op.drop_index("ix_audit_event_event_type", table_name="audit_event")
    op.drop_index("ix_audit_event_actor_user_id", table_name="audit_event")
    op.drop_table("audit_event")
    op.drop_index("ix_user_account_username", table_name="user_account")
    op.drop_table("user_account")
