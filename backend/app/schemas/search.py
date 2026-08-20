from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.knowledge_content import SearchQueryMode


class SearchRequest(BaseModel):
    retrieval_mode: Literal["vector", "field_filter"] = "vector"
    query: str | None = Field(default=None, max_length=4_000)
    ocr_text: str | None = Field(default=None, max_length=4_000)
    knowledge_base_id: UUID | None = None
    parent_type: str | None = Field(default=None, max_length=120)
    question_type: str | None = Field(default=None, max_length=255)
    business_object: str | None = Field(default=None, max_length=255)
    purpose: str | None = Field(default=None, max_length=255)
    customer_type: str | None = Field(default=None, max_length=255)
    limit: int = Field(default=10, ge=1, le=20)

    @field_validator(
        "query",
        "ocr_text",
        "parent_type",
        "question_type",
        "business_object",
        "purpose",
        "customer_type",
    )
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def validate_retrieval_mode(self) -> SearchRequest:
        has_text_query = self.query is not None or self.ocr_text is not None
        has_field_filter = any(
            (
                self.parent_type,
                self.question_type,
                self.business_object,
                self.purpose,
                self.customer_type,
            )
        )
        if self.retrieval_mode == "vector":
            if not has_text_query:
                raise ValueError("向量检索至少提供文本问题或 OCR 文本")
            if has_field_filter:
                raise ValueError("向量检索不支持字段筛选条件，请使用字段筛选方式")
        elif has_text_query:
            raise ValueError("字段筛选不支持文本问题或 OCR 文本，请使用向量检索方式")
        return self


class SearchResultResponse(BaseModel):
    result_item_id: UUID
    rank: int
    score: float
    child_id: UUID
    knowledge_base_id: UUID
    knowledge_base_name: str
    child_revision_id: UUID
    question: str
    response_content: str
    question_variants: list[str]
    follow_up_guidance: str | None
    question_type: str | None
    business_object: str | None
    purpose: str | None
    customer_type: str | None
    feature_explanation: str | None
    example: str | None
    internal_notes: str | None
    helpful_count: int
    match_reason: str


class SearchParentGroupResponse(BaseModel):
    parent_id: UUID
    parent_name: str
    canonical_keyword: str
    children: list[SearchResultResponse]


class SearchResponse(BaseModel):
    search_event_id: UUID
    query_mode: SearchQueryMode
    no_match: bool
    no_match_guidance: str | None
    groups: list[SearchParentGroupResponse]


class HelpfulFeedbackRequest(BaseModel):
    result_item_id: UUID


class HelpfulFeedbackResponse(BaseModel):
    accepted: bool
    already_recorded: bool
    helpful_count: int
