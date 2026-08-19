from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.input_validation import normalize_display_name

_KNOWLEDGE_BASE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")


def normalize_knowledge_base_key(value: str) -> str:
    logical_key = value.strip().lower()
    if not _KNOWLEDGE_BASE_KEY_PATTERN.fullmatch(logical_key):
        raise ValueError("逻辑标识须为 3–64 个小写字母、数字、下划线或连字符，并以字母开头")
    return logical_key


def normalize_knowledge_base_description(value: str | None) -> str | None:
    if value is None:
        return None
    description = value.strip()
    if not description:
        return None
    if len(description) > 2_000:
        raise ValueError("知识库说明不能超过 2000 个字符")
    return description


class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    logical_key: str
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ManagedKnowledgeBaseResponse(KnowledgeBaseResponse):
    current_collection_generation: int
    current_physical_collection_name: str
    reviewer_count: int


class CreateKnowledgeBaseRequest(BaseModel):
    logical_key: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)
    is_active: bool = True

    @field_validator("logical_key")
    @classmethod
    def validate_logical_key(cls, value: str) -> str:
        return normalize_knowledge_base_key(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_display_name(value)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        return normalize_knowledge_base_description(value)


class UpdateKnowledgeBaseRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return normalize_display_name(value) if value is not None else value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        return normalize_knowledge_base_description(value)

    @model_validator(mode="after")
    def require_change(self) -> UpdateKnowledgeBaseRequest:
        allowed_fields = {"name", "description", "is_active"}
        if not self.model_fields_set.intersection(allowed_fields):
            raise ValueError("至少提供一个待更新字段")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("知识库名称不能为空")
        return self


class ReviewerAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str
    is_active: bool


class ReviewerAssignmentResponse(BaseModel):
    knowledge_base_id: UUID
    reviewer: ReviewerAccountResponse
    assigned_by_user_id: UUID
    assigned_at: datetime
