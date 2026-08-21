from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ConversationRole = Literal["customer", "ours"]


class NormalizedConversationMessage(BaseModel):
    """One standardized chat message from any channel adapter."""

    speaker: str = Field(min_length=1, max_length=120)
    role: ConversationRole
    body: str = Field(min_length=1, max_length=4_000)
    sent_at: datetime | None = None

    @field_validator("speaker", "body")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("消息说话人和正文不能为空白")
        return normalized


@dataclass(frozen=True)
class ValidatedConversation:
    messages: list[NormalizedConversationMessage]
    source_hash: str
    transcript: str


class ConversationInputError(ValueError):
    """Raised when a normalized conversation violates deployment limits."""


def validate_conversation(
    messages: list[NormalizedConversationMessage],
    *,
    max_messages: int,
    max_chars: int,
    require_both_parties: bool,
) -> ValidatedConversation:
    if not messages:
        raise ConversationInputError("会话消息不能为空")
    if len(messages) > max_messages:
        raise ConversationInputError(
            f"会话消息数量超过上限（最多 {max_messages} 条），请缩小粘贴范围"
        )

    total_chars = sum(len(message.body) for message in messages)
    if total_chars > max_chars:
        raise ConversationInputError(
            f"会话文字长度超过上限（最多 {max_chars} 字符），请缩小粘贴范围"
        )

    roles = {message.role for message in messages}
    if require_both_parties and roles != {"customer", "ours"}:
        raise ConversationInputError(
            "无法可靠识别客户与我方双方发言，不能生成草稿；请确认粘贴内容包含双方消息"
        )

    return ValidatedConversation(
        messages=list(messages),
        source_hash=_source_hash(messages),
        transcript=render_transcript(messages),
    )


def render_transcript(messages: list[NormalizedConversationMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        role_label = "我方" if message.role == "ours" else "客户"
        sent_at = message.sent_at.isoformat(timespec="seconds") if message.sent_at else ""
        prefix = f"{message.speaker}（{role_label}）"
        if sent_at:
            prefix = f"{prefix} {sent_at}"
        lines.append(f"{prefix}: {message.body}")
    return "\n".join(lines)


def _source_hash(messages: list[NormalizedConversationMessage]) -> str:
    canonical = json.dumps(
        [
            {
                "speaker": message.speaker,
                "role": message.role,
                "body": message.body,
                "sent_at": message.sent_at.isoformat() if message.sent_at else None,
            }
            for message in messages
        ],
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
