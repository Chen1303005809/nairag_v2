"""The server-owned fixed taxonomy used by knowledge-content forms.

Keeping this list in the API prevents a generated proposal from silently
introducing a value that cannot be used by the review and search flows.  The
web client reads it through ``/knowledge-content/taxonomy`` and only keeps the
small fallback list needed while an older API is rolling out.
"""

from __future__ import annotations

from collections.abc import Iterable

PARENT_TYPE_OPTIONS = (
    "问题反馈",
    "需求提交",
    "配置项咨询",
)

QUESTION_TYPE_OPTIONS = (
    "功能故障类",
    "终端/管理平台功能咨询类",
    "对账/账单数据类",
    "账户迁仓类",
    "穿透式测试/飞套报告类",
)

BUSINESS_OBJECT_OPTIONS = (
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
)

PURPOSE_OPTIONS = (
    "企业微信咨询",
    "400 电话咨询",
    "需求节点核实",
    "内部培训",
    "审计合规",
)

CUSTOMER_TYPE_OPTIONS = (
    "个人客户",
    "私募公司",
    "期货公司",
    "经纪公司风险子",
    "证券公司",
    "产业客户",
)


def taxonomy_options() -> dict[str, list[str]]:
    """Return JSON-ready taxonomy options without exposing mutable globals."""

    return {
        "parent_types": list(PARENT_TYPE_OPTIONS),
        "question_types": list(QUESTION_TYPE_OPTIONS),
        "business_objects": list(BUSINESS_OBJECT_OPTIONS),
        "purposes": list(PURPOSE_OPTIONS),
        "customer_types": list(CUSTOMER_TYPE_OPTIONS),
    }


def allowed_taxonomy_values(field_name: str) -> set[str]:
    options: dict[str, Iterable[str]] = {
        "question_type": QUESTION_TYPE_OPTIONS,
        "business_object": BUSINESS_OBJECT_OPTIONS,
        "purpose": PURPOSE_OPTIONS,
        "customer_type": CUSTOMER_TYPE_OPTIONS,
    }
    return set(options[field_name])


def is_allowed_parent_type(value: str | None) -> bool:
    return value is not None and value in PARENT_TYPE_OPTIONS


def is_allowed_taxonomy_value(field_name: str, value: str | None) -> bool:
    return value is not None and value in allowed_taxonomy_values(field_name)
