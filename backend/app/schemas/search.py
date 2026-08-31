from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.knowledge_content import (
    SearchAnnotationResultLabel,
    SearchInteractionType,
    SearchQueryMode,
)
from app.schemas.knowledge_content import EvidenceAttachmentResponse, WebLinkInput
from app.services.conversation import NormalizedConversationMessage


class SearchRequest(BaseModel):
    retrieval_mode: Literal["vector", "field_filter"] = "vector"
    query: str | None = Field(default=None, max_length=4_000)
    ocr_text: str | None = Field(default=None, max_length=4_000)
    ocr_recognition_token: str | None = Field(default=None, max_length=32_000)
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
        if self.ocr_text is not None and self.ocr_recognition_token is not None:
            raise ValueError("OCR 文本与 OCR 识别凭据不能同时提供")
        has_text_query = (
            self.query is not None
            or self.ocr_text is not None
            or self.ocr_recognition_token is not None
        )
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


class OcrRecognitionResponse(BaseModel):
    text: str
    keywords: list[str]
    confidence: float
    model_version: str
    recognition_token: str


class SearchResultResponse(BaseModel):
    result_item_id: UUID
    rank: int
    score: float
    hybrid_score: float | None
    rerank_score: float | None
    selection_stage: str
    helpful_count_at_search: int
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
    attachments: list[EvidenceAttachmentResponse]
    web_links: list[WebLinkInput]
    helpful_count: int
    match_reason: str
    matched_field: str | None


class SearchParentGroupResponse(BaseModel):
    parent_id: UUID
    parent_name: str
    canonical_keyword: str
    children: list[SearchResultResponse]


class SearchResponse(BaseModel):
    search_event_id: UUID
    search_interaction_id: UUID | None
    query_mode: SearchQueryMode
    no_match: bool
    no_match_guidance: str | None
    degraded: bool
    degradation_reasons: list[str]
    groups: list[SearchParentGroupResponse]


class HelpfulFeedbackRequest(BaseModel):
    result_item_id: UUID


class HelpfulFeedbackResponse(BaseModel):
    accepted: bool
    already_recorded: bool
    helpful_count: int


class SearchAnnotationResultFeedbackInput(BaseModel):
    search_result_item_id: UUID
    feedback_type: SearchAnnotationResultLabel
    other_note: str | None = None

    @field_validator("other_note")
    @classmethod
    def normalize_other_note(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_other_note(self) -> SearchAnnotationResultFeedbackInput:
        if self.feedback_type == SearchAnnotationResultLabel.OTHER:
            if not self.other_note:
                raise ValueError("选择“其他”时必须填写说明")
            if len(self.other_note) > 4_000:
                raise ValueError("其他说明不能超过 4000 字符")
        elif self.other_note is not None:
            raise ValueError("该标注类型不接受其他说明")
        return self


class SearchAnnotationReviewRequest(BaseModel):
    result_feedbacks: list[SearchAnnotationResultFeedbackInput] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_unique_result_items(self) -> SearchAnnotationReviewRequest:
        result_item_ids = [item.search_result_item_id for item in self.result_feedbacks]
        if len(result_item_ids) != len(set(result_item_ids)):
            raise ValueError("同一检索结果只能标注一次")
        return self


class SearchAnnotationResultFeedbackResponse(BaseModel):
    search_result_item_id: UUID
    feedback_type: SearchAnnotationResultLabel
    other_note: str | None


class SearchAnnotationReviewResponse(BaseModel):
    accepted: bool
    already_recorded: bool
    reviewed_result_count: int
    submitted_at: datetime
    result_feedbacks: list[SearchAnnotationResultFeedbackResponse]


class ConversationSearchRequest(BaseModel):
    messages: list[NormalizedConversationMessage] = Field(min_length=1, max_length=1_000)
    knowledge_base_id: UUID | None = None
    limit: int = Field(default=10, ge=1, le=20)


class ConversationSearchResultResponse(SearchResultResponse):
    search_event_id: UUID
    matched_queries: list[str]


class ConversationSearchParentGroupResponse(BaseModel):
    parent_id: UUID
    parent_name: str
    canonical_keyword: str
    children: list[ConversationSearchResultResponse]


class ConversationSearchResponse(BaseModel):
    search_interaction_id: UUID | None
    queries: list[str]
    total_candidates: int
    no_query_guidance: str | None
    no_match: bool
    no_match_guidance: str | None
    degraded: bool
    degradation_reasons: list[str]
    groups: list[ConversationSearchParentGroupResponse]


class QueryBatchRequest(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=5)
    knowledge_base_id: UUID | None = None
    limit: int = Field(default=10, ge=1, le=20)

    @field_validator("queries")
    @classmethod
    def normalize_queries(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            query = " ".join(value.split())
            if not query:
                raise ValueError("查询语句不能为空")
            if len(query) > 4_000:
                raise ValueError("查询长度不能超过 4000 字符")
            normalized.append(query)
        return normalized


class AnnotationFeedbackUserResponse(BaseModel):
    id: UUID
    username: str
    display_name: str


class AnnotationFeedbackSummaryResponse(BaseModel):
    completed_review_count: int
    annotated_result_count: int
    high_score_irrelevant_count: int
    low_score_relevant_count: int
    normal_count: int
    other_count: int


class AnnotationFeedbackListItemResponse(BaseModel):
    id: UUID
    submitted_by: AnnotationFeedbackUserResponse
    interaction_type: SearchInteractionType
    queries: list[str]
    target_knowledge_base_id: UUID | None
    target_knowledge_base_name: str | None
    high_score_irrelevant_count: int
    low_score_relevant_count: int
    normal_count: int
    other_count: int
    searched_at: datetime
    submitted_at: datetime
    result_count: int


class AnnotationFeedbackPageResponse(BaseModel):
    items: list[AnnotationFeedbackListItemResponse]
    total: int
    page: int
    page_size: int


class AnnotationFeedbackResultDetailResponse(BaseModel):
    result_item_id: UUID
    rank: int
    score: float
    hybrid_score: float | None
    rerank_score: float | None
    selection_stage: str
    matched_field: str | None
    parent_name: str
    question: str
    knowledge_base_id: UUID
    knowledge_base_name: str
    matched_queries: list[str]
    feedback_type: SearchAnnotationResultLabel
    other_note: str | None


class AnnotationFeedbackQueryDetailResponse(BaseModel):
    search_event_id: UUID
    query_order: int
    query_text: str | None
    ocr_text: str | None
    no_match: bool
    results: list[AnnotationFeedbackResultDetailResponse]


class AnnotationFeedbackDetailResponse(AnnotationFeedbackListItemResponse):
    no_match: bool
    degraded: bool
    degradation_reasons: list[str]
    query_details: list[AnnotationFeedbackQueryDetailResponse]
