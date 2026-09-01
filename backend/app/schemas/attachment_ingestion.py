"""Public schemas for durable DOC/DOCX attachment imports."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.knowledge_content import (
    EvidenceAttachmentResponse,
    ParentContentInput,
    ReviewSubmissionResponse,
    normalize_optional_content,
    normalize_required_content,
)


class TaxonomyOptionsResponse(BaseModel):
    parent_types: list[str]
    question_types: list[str]
    business_objects: list[str]
    purposes: list[str]
    customer_types: list[str]


class AttachmentImportParentProposal(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    canonical_keyword: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_required_content(value, "问题大类")

    @field_validator("canonical_keyword")
    @classmethod
    def validate_keyword(cls, value: str) -> str:
        return normalize_required_content(value, "问题大类关键词")

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        normalized = [normalize_required_content(value, "别名") for value in values]
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("别名不能重复")
        return normalized


class AttachmentImportCandidate(BaseModel):
    """One editable proposal item. ``id`` is stable within a batch."""

    id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=4_000)
    response_content: str = Field(min_length=1, max_length=16_000)
    question_variants: list[str] = Field(default_factory=list, max_length=50)
    follow_up_guidance: str | None = Field(default=None, max_length=4_000)
    question_type: str | None = Field(default=None, max_length=255)
    business_object: str | None = Field(default=None, max_length=255)
    purpose: str | None = Field(default=None, max_length=255)
    customer_type: str | None = Field(default=None, max_length=255)
    feature_explanation: str | None = Field(default=None, max_length=4_000)
    example: str | None = Field(default=None, max_length=4_000)
    internal_notes: str | None = Field(default=None, max_length=4_000)

    @field_validator("id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("小类 ID 不能为空")
        return normalized

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        return normalize_required_content(value, "问题")

    @field_validator("response_content")
    @classmethod
    def validate_response_content(cls, value: str) -> str:
        return normalize_required_content(value, "回复内容")

    @field_validator(
        "follow_up_guidance",
        "question_type",
        "business_object",
        "purpose",
        "customer_type",
        "feature_explanation",
        "example",
        "internal_notes",
    )
    @classmethod
    def normalize_optional(cls, value: str | None) -> str | None:
        return normalize_optional_content(value)

    @field_validator("question_variants")
    @classmethod
    def normalize_variants(cls, values: list[str]) -> list[str]:
        normalized = [normalize_required_content(value, "同义问句") for value in values]
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("同义问句不能重复")
        return normalized

    @model_validator(mode="after")
    def prevent_question_as_variant(self) -> AttachmentImportCandidate:
        if self.question.casefold() in {value.casefold() for value in self.question_variants}:
            raise ValueError("同义问句不能与主问题相同")
        return self


class AttachmentImportSimilarParent(BaseModel):
    id: UUID
    name: str
    canonical_keyword: str
    score: int = Field(ge=0, le=100)
    matched_keyword: str
    available_knowledge_bases: list[UUID] = Field(default_factory=list)


class AttachmentImportProposal(BaseModel):
    parent: AttachmentImportParentProposal
    children: list[AttachmentImportCandidate] = Field(min_length=1, max_length=50)
    recommended_primary_child_id: str
    warnings: list[str] = Field(default_factory=list, max_length=100)
    image_count: int = Field(default=0, ge=0)
    similar_parents: list[AttachmentImportSimilarParent] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_primary_child(self) -> AttachmentImportProposal:
        child_ids = [child.id for child in self.children]
        if len(set(child_ids)) != len(child_ids):
            raise ValueError("小类 ID 不能重复")
        if self.recommended_primary_child_id not in set(child_ids):
            raise ValueError("推荐主小类必须属于小类列表")
        return self


class AttachmentImportBatchResponse(BaseModel):
    id: UUID
    status: str
    attachment: EvidenceAttachmentResponse
    warnings: list[str]
    image_count: int
    extracted_char_count: int
    model_version: str | None
    attempt_count: int
    last_error: str | None
    expires_at: str
    created_at: str
    completed_at: str | None
    submitted_at: str | None
    final_submission_id: UUID | None
    final_parent_id: UUID | None


class AttachmentImportBatchDetailResponse(AttachmentImportBatchResponse):
    proposal: AttachmentImportProposal | None


class ConfirmAttachmentImportRequest(BaseModel):
    target: Literal["new", "existing"]
    parent: ParentContentInput | None = None
    existing_parent_id: UUID | None = None
    primary_child_id: str = Field(min_length=1, max_length=64)
    children: list[AttachmentImportCandidate] = Field(min_length=1, max_length=50)
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @field_validator("primary_child_id")
    @classmethod
    def normalize_primary_child_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("必须选择主小类")
        return normalized

    @field_validator("knowledge_base_ids")
    @classmethod
    def validate_knowledge_base_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("目标知识库不能重复")
        return values

    @model_validator(mode="after")
    def validate_target(self) -> ConfirmAttachmentImportRequest:
        child_ids = [child.id for child in self.children]
        if len(set(child_ids)) != len(child_ids):
            raise ValueError("小类 ID 不能重复")
        if self.primary_child_id not in set(child_ids):
            raise ValueError("主小类必须属于小类列表")
        if self.target == "new":
            if self.parent is None:
                raise ValueError("新建问题大类时必须填写大类信息")
            if self.existing_parent_id is not None:
                raise ValueError("新建问题大类时不能指定已有大类")
            if not self.knowledge_base_ids:
                raise ValueError("新建问题大类时至少选择一个目标知识库")
        else:
            if self.existing_parent_id is None:
                raise ValueError("归并已有大类时必须选择已发布大类")
            if self.parent is not None:
                raise ValueError("归并已有大类时不能修改大类信息")
            if self.knowledge_base_ids:
                raise ValueError("归并已有大类时由系统沿用该大类的已发布知识库")
        return self


class AttachmentImportConfirmResponse(BaseModel):
    submission: ReviewSubmissionResponse
    parent_id: UUID
    created_draft_ids: list[UUID]
