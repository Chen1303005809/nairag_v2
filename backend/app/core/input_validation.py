from __future__ import annotations

import re

from app.core.config import Settings, get_settings

_USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


def normalize_username(value: str) -> str:
    username = value.strip().lower()
    if not _USERNAME_PATTERN.fullmatch(username):
        raise ValueError(
            "用户名须为 3–64 个小写字母、数字、点、下划线或连字符，且以字母或数字开头"
        )
    return username


def normalize_display_name(value: str) -> str:
    display_name = value.strip()
    if not display_name:
        raise ValueError("显示名称不能为空")
    if len(display_name) > 120:
        raise ValueError("显示名称不能超过 120 个字符")
    return display_name


def validate_password(value: str, settings: Settings | None = None) -> str:
    active_settings = settings or get_settings()
    if not active_settings.password_min_length <= len(value) <= active_settings.password_max_length:
        minimum = active_settings.password_min_length
        maximum = active_settings.password_max_length
        raise ValueError(
            f"密码长度须为 {minimum}–{maximum} 个字符"
        )
    if not value.strip():
        raise ValueError("密码不能只包含空白字符")
    return value
