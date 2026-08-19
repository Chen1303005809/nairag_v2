from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.knowledge_content import SearchQueryMode


class SearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=4_000)
    ocr_text: str | None = Field(default=None, max_length=4_000)
    knowledge_base_id: UUID | None = None
    limit: int = Field(default=10, ge=1, le=20)

    @field_validator("query", "ocr_text")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def require_query_channel(self) -> SearchRequest:
        if self.query is None and self.ocr_text is None:
            raise ValueError("至少提供文本问题或 OCR 文本")
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
