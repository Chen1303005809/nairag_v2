"""Database-backed options for knowledge-content classification fields."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_content import ChildRevision
from app.models.taxonomy import KnowledgeTaxonomyOption

PARENT_TYPE_OPTIONS = (
    "问题反馈",
    "需求提交",
    "配置项咨询",
)

DEFAULT_TAXONOMY_OPTIONS: dict[str, tuple[str, ...]] = {
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

TAXONOMY_FIELD_LABELS = {
    "question_types": "问题类型",
    "business_objects": "具体功能与模块",
    "purposes": "应用场景",
    "customer_types": "客户类型",
}

CHILD_REVISION_FIELD_BY_TAXONOMY_FIELD = {
    "question_types": ChildRevision.question_type,
    "business_objects": ChildRevision.business_object,
    "purposes": ChildRevision.purpose,
    "customer_types": ChildRevision.customer_type,
}

SINGULAR_TO_PLURAL_FIELD = {
    "question_type": "question_types",
    "business_object": "business_objects",
    "purpose": "purposes",
    "customer_type": "customer_types",
}


class TaxonomyOptionAlreadyExistsError(Exception):
    pass


class TaxonomyOptionNotFoundError(Exception):
    pass


def default_taxonomy_options() -> dict[str, list[str]]:
    """Return a copy of the bootstrap options for tests and LLM fallback use."""

    return {field_key: list(values) for field_key, values in DEFAULT_TAXONOMY_OPTIONS.items()}


async def ensure_default_taxonomy_options(session: AsyncSession) -> None:
    """Seed defaults when a database was created from metadata rather than Alembic."""

    existing_id = await session.scalar(select(KnowledgeTaxonomyOption.id).limit(1))
    if existing_id is not None:
        return

    session.add_all(
        KnowledgeTaxonomyOption(field_key=field_key, value=value, sort_order=sort_order)
        for field_key, values in DEFAULT_TAXONOMY_OPTIONS.items()
        for sort_order, value in enumerate(values)
    )
    await session.flush()


async def _current_option_lists(session: AsyncSession) -> dict[str, list[str]]:
    rows = await session.scalars(
        select(KnowledgeTaxonomyOption).order_by(
            KnowledgeTaxonomyOption.field_key,
            KnowledgeTaxonomyOption.sort_order,
        )
    )
    options = {field_key: [] for field_key in DEFAULT_TAXONOMY_OPTIONS}
    for row in rows:
        if row.field_key in options:
            options[row.field_key].append(row.value)
    if not any(options.values()):
        return default_taxonomy_options()
    return options


async def taxonomy_options(session: AsyncSession) -> dict[str, object]:
    """Return active choices and values still used by historical child revisions."""

    active_options = await _current_option_lists(session)
    search_filter_options: dict[str, list[str]] = {}
    for field_key, column in CHILD_REVISION_FIELD_BY_TAXONOMY_FIELD.items():
        historical_values = await session.scalars(
            select(column).where(column.is_not(None)).distinct().order_by(column)
        )
        active_values = active_options[field_key]
        active_casefolded = {value.casefold() for value in active_values}
        legacy_values = sorted(
            {
                value
                for value in historical_values
                if value is not None and value.casefold() not in active_casefolded
            },
            key=str.casefold,
        )
        search_filter_options[field_key] = [*active_values, *legacy_values]

    return {
        "parent_types": list(PARENT_TYPE_OPTIONS),
        **active_options,
        "search_filters": search_filter_options,
    }


def is_allowed_parent_type(value: str | None) -> bool:
    return value is not None and value in PARENT_TYPE_OPTIONS


def is_allowed_taxonomy_value(
    field_name: str,
    value: str | None,
    options: Mapping[str, object],
) -> bool:
    field_key = SINGULAR_TO_PLURAL_FIELD[field_name]
    values = options.get(field_key)
    return value is not None and isinstance(values, list) and value in values


async def add_taxonomy_option(
    session: AsyncSession,
    *,
    field_key: str,
    value: str,
) -> KnowledgeTaxonomyOption:
    await ensure_default_taxonomy_options(session)
    if field_key not in DEFAULT_TAXONOMY_OPTIONS:
        raise KeyError(field_key)

    rows = await session.scalars(
        select(KnowledgeTaxonomyOption).where(KnowledgeTaxonomyOption.field_key == field_key)
    )
    options = list(rows)
    if any(row.value.casefold() == value.casefold() for row in options):
        raise TaxonomyOptionAlreadyExistsError(value)

    next_order = max((row.sort_order for row in options), default=-1) + 1
    option = KnowledgeTaxonomyOption(field_key=field_key, value=value, sort_order=next_order)
    session.add(option)
    await session.flush()
    return option


async def rename_taxonomy_option(
    session: AsyncSession,
    *,
    field_key: str,
    old_value: str,
    new_value: str,
) -> KnowledgeTaxonomyOption:
    await ensure_default_taxonomy_options(session)
    if field_key not in DEFAULT_TAXONOMY_OPTIONS:
        raise KeyError(field_key)

    rows = await session.scalars(
        select(KnowledgeTaxonomyOption).where(KnowledgeTaxonomyOption.field_key == field_key)
    )
    options = list(rows)
    option = next((row for row in options if row.value == old_value), None)
    if option is None:
        raise TaxonomyOptionNotFoundError(old_value)
    if any(
        row.id != option.id and row.value.casefold() == new_value.casefold()
        for row in options
    ):
        raise TaxonomyOptionAlreadyExistsError(new_value)

    option.value = new_value
    await session.flush()
    return option
