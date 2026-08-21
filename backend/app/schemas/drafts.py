from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.knowledge_content import (
    EvidenceAttachmentResponse,
    WebLinkInput,
    normalize_optional_content,
)


class KnowledgeDraftInput(BaseModel):
    parent_id: UUID | None = None
    question: str | None = Field(default=None, max_length=4_000)
    response_content: str | None = Field(default=None, max_length=16_000)
    question_variants: list[str] = Field(default_factory=list, max_length=50)
    follow_up_guidance: str | None = Field(default=None, max_length=4_000)
    question_type: str | None = Field(default=None, max_length=255)
    business_object: str | None = Field(default=None, max_length=255)
    purpose: str | None = Field(default=None, max_length=255)
    customer_type: str | None = Field(default=None, max_length=255)
    feature_explanation: str | None = Field(default=None, max_length=4_000)
    example: str | None = Field(default=None, max_length=4_000)
    internal_notes: str | None = Field(default=None, max_length=4_000)
    attachments: list[UUID] = Field(default_factory=list, max_length=10)
    web_links: list[WebLinkInput] = Field(default_factory=list, max_length=20)
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @field_validator(
        "question",
        "response_content",
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
    def validate_variants(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("同义问句不能重复")
        return normalized

    @field_validator("attachments")
    @classmethod
    def validate_attachments(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("附件不能重复")
        return values

    @field_validator("web_links")
    @classmethod
    def validate_web_links(cls, values: list[WebLinkInput]) -> list[WebLinkInput]:
        if len({value.url.casefold() for value in values}) != len(values):
            raise ValueError("网页链接不能重复")
        return values

    @field_validator("knowledge_base_ids")
    @classmethod
    def validate_knowledge_base_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("目标知识库不能重复")
        return values

    @model_validator(mode="after")
    def validate_has_content(self) -> KnowledgeDraftInput:
        has_business_content = any(
            (
                self.question,
                self.response_content,
                self.question_variants,
                self.follow_up_guidance,
                self.question_type,
                self.business_object,
                self.purpose,
                self.customer_type,
                self.feature_explanation,
                self.example,
                self.internal_notes,
                self.attachments,
                self.web_links,
            )
        )
        if not has_business_content:
            raise ValueError("草稿至少需要一个非空业务字段")
        if self.question and self.question.casefold() in {
            value.casefold() for value in self.question_variants
        }:
            raise ValueError("同义问句不能与主问题相同")
        return self


class KnowledgeDraftResponse(BaseModel):
    id: UUID
    source: str
    parent_id: UUID | None
    ingestion_batch_id: UUID | None
    question: str | None
    response_content: str | None
    question_variants: list[str]
    follow_up_guidance: str | None
    question_type: str | None
    business_object: str | None
    purpose: str | None
    customer_type: str | None
    feature_explanation: str | None
    example: str | None
    internal_notes: str | None
    attachments: list[EvidenceAttachmentResponse]
    web_links: list[WebLinkInput]
    knowledge_base_ids: list[UUID]
    source_hash: str | None
    extracted_at: str | None
    model_version: str | None
    created_at: str
    updated_at: str
