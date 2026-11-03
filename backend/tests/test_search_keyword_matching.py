from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.knowledge_content import ParentLexicalRuleType
from app.services.search import _parent_keyword_match


def candidate(
    canonical_keyword: str,
    *rules: tuple[ParentLexicalRuleType, str],
) -> SimpleNamespace:
    return SimpleNamespace(
        parent_revision=SimpleNamespace(canonical_keyword=canonical_keyword),
        lexical_rules=[
            SimpleNamespace(rule_type=rule_type, rule_value=rule_value)
            for rule_type, rule_value in rules
        ],
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("登录失败", (3, "canonical_keyword")),
        ("密x登", None),
        ("密", None),
    ],
)
def test_canonical_keyword_requires_a_contiguous_two_character_fuzzy_match(
    query: str,
    expected: tuple[int, str] | None,
) -> None:
    assert _parent_keyword_match(query, candidate("密码登录")) == expected


def test_single_character_canonical_keyword_still_matches_exactly() -> None:
    assert _parent_keyword_match("账单", candidate("账")) == (3, "canonical_keyword")


def test_alias_supports_two_character_fuzzy_match() -> None:
    item = candidate(
        "用户管理",
        (ParentLexicalRuleType.ALIAS, "凭据验证"),
    )

    assert _parent_keyword_match("验证流程", item) == (2, "alias_keyword")


def test_canonical_keyword_priority_over_alias_is_preserved() -> None:
    item = candidate(
        "用户验证",
        (ParentLexicalRuleType.ALIAS, "验证码"),
    )

    assert _parent_keyword_match("验证码", item) == (3, "canonical_keyword")


def test_alias_priority_over_regex_is_preserved() -> None:
    item = candidate(
        "完全不同",
        (ParentLexicalRuleType.REGEX, r"^验证码异常$"),
        (ParentLexicalRuleType.ALIAS, "验证码故障"),
    )

    assert _parent_keyword_match("验证码异常", item) == (2, "alias_keyword")


def test_regex_behavior_remains_exactly_controlled_by_its_pattern() -> None:
    item = candidate(
        "完全不同",
        (ParentLexicalRuleType.REGEX, r"^验证码失败$"),
    )

    assert _parent_keyword_match("验证码失败", item) == (1, "regex_keyword")
    assert _parent_keyword_match("验证码", item) is None
