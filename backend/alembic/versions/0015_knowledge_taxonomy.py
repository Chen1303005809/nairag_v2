"""add editable knowledge taxonomy options

Revision ID: 0015_knowledge_taxonomy
Revises: 0014_attachment_import
Create Date: 2026-10-08 00:00:00

"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "0015_knowledge_taxonomy"
down_revision: str | None = "0014_attachment_import"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


DEFAULT_OPTIONS: dict[str, tuple[str, ...]] = {
    "question_types": (
        "功能故障类",
        "终端/管理平台功能咨询类",
        "对账/账单数据类",
        "账户迁仓类",
        "穿透式测试/飞套报告类",
    ),
    "business_objects": (
        "基础知识与算法",
        "对应平台使用说明书",
        "随心易交易终端",
        "管理平台&风控终端配置",
        "企业版交易终端相关配置",
        "程序化接入",
        "仓位、资金比对及处理",
        "账户建立&账户迁移",
        "绩效系统使用",
        "服务器硬件配置&需求确认单",
        "测试报告&白皮书",
    ),
    "purposes": (
        "企业微信咨询",
        "400 电话咨询",
        "需求节点核实",
        "内部培训",
        "审计合规",
    ),
    "customer_types": (
        "个人客户",
        "私募公司",
        "期货公司",
        "经纪公司风险子",
        "证券公司",
        "产业客户",
    ),
}


def upgrade() -> None:
    op.create_table(
        "knowledge_taxonomy_option",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("field_key", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_knowledge_taxonomy_order_nonnegative"),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_taxonomy_option"),
        sa.UniqueConstraint("field_key", "sort_order", name="uq_knowledge_taxonomy_field_order"),
        sa.UniqueConstraint("field_key", "value", name="uq_knowledge_taxonomy_field_value"),
    )
    op.create_index(
        "ix_knowledge_taxonomy_option_field_key",
        "knowledge_taxonomy_option",
        ["field_key"],
        unique=False,
    )
    table = sa.table(
        "knowledge_taxonomy_option",
        sa.column("id", sa.Uuid()),
        sa.column("field_key", sa.String(length=32)),
        sa.column("value", sa.String(length=255)),
        sa.column("sort_order", sa.Integer()),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": uuid4(),
                "field_key": field_key,
                "value": value,
                "sort_order": sort_order,
            }
            for field_key, values in DEFAULT_OPTIONS.items()
            for sort_order, value in enumerate(values)
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_taxonomy_option_field_key", table_name="knowledge_taxonomy_option")
    op.drop_table("knowledge_taxonomy_option")
