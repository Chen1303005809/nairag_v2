from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.knowledge_content import (
    ChildPublicationStatus,
    ParentLexicalRuleType,
    ReviewDecisionKind,
    ReviewSubmissionKind,
    ReviewSubmissionStatus,
    ReviewTargetStatus,
)


def normalize_required_content(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label}不能为空")
    return normalized


def normalize_optional_content(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def normalize_http_url(value: str, label: str) -> str:
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if (
        not normalized
        or any(character.isspace() for character in normalized)
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
    ):
        raise ValueError(f"{label}必须是有效的 http 或 https 链接")
    return normalized


class ParentLexicalRuleInput(BaseModel):
    rule_type: ParentLexicalRuleType
    rule_value: str = Field(min_length=1, max_length=512)

    @field_validator("rule_value")
    @classmethod
    def validate_rule_value(cls, value: str) -> str:
        return normalize_required_content(value, "词法规则")

    @model_validator(mode="after")
    def validate_regular_expression(self) -> ParentLexicalRuleInput:
        if self.rule_type == ParentLexicalRuleType.REGEX:
            try:
                re.compile(self.rule_value)
            except re.error as exc:
                raise ValueError("受控正则表达式格式无效") from exc
        return self


class ParentContentInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    canonical_keyword: str = Field(min_length=1, max_length=255)
    lexical_rules: list[ParentLexicalRuleInput] = Field(default_factory=list, max_length=50)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_required_content(value, "父类名称")

    @field_validator("canonical_keyword")
    @classmethod
    def validate_canonical_keyword(cls, value: str) -> str:
        return normalize_required_content(value, "规范关键词")

    @model_validator(mode="after")
    def validate_lexical_rules(self) -> ParentContentInput:
        canonical = self.canonical_keyword.casefold()
        seen: set[tuple[ParentLexicalRuleType, str]] = set()
        for rule in self.lexical_rules:
            normalized_value = (
                rule.rule_value.casefold()
                if rule.rule_type == ParentLexicalRuleType.ALIAS
                else rule.rule_value
            )
            if rule.rule_type == ParentLexicalRuleType.ALIAS and normalized_value == canonical:
                raise ValueError("别名不能与规范关键词相同")
            identity = (rule.rule_type, normalized_value)
            if identity in seen:
                raise ValueError("词法规则不能重复")
            seen.add(identity)
        return self


class EvidenceAttachmentResponse(BaseModel):
    id: UUID
    name: str
    content_type: str
    size_bytes: int


class WebLinkInput(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=2_048)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return normalize_required_content(value, "网页链接标题")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return normalize_http_url(value, "网页链接")


class ChildContentInput(BaseModel):
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
    attachments: list[UUID] = Field(default_factory=list, max_length=10)
    web_links: list[WebLinkInput] = Field(default_factory=list, max_length=20)

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
    def validate_optional_content(cls, value: str | None) -> str | None:
        return normalize_optional_content(value)

    @field_validator("question_variants")
    @classmethod
    def validate_question_variants(cls, values: list[str]) -> list[str]:
        normalized_values = [normalize_required_content(value, "同义问句") for value in values]
        if len({value.casefold() for value in normalized_values}) != len(normalized_values):
            raise ValueError("同义问句不能重复")
        return normalized_values

    @field_validator("attachments")
    @classmethod
    def validate_attachments(
        cls,
        values: list[UUID],
    ) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("附件不能重复")
        return values

    @field_validator("web_links")
    @classmethod
    def validate_web_links(cls, values: list[WebLinkInput]) -> list[WebLinkInput]:
        if len({web_link.url.casefold() for web_link in values}) != len(values):
            raise ValueError("网页链接不能重复")
        return values

    @model_validator(mode="after")
    def prevent_question_as_its_own_variant(self) -> ChildContentInput:
        if self.question.casefold() in {value.casefold() for value in self.question_variants}:
            raise ValueError("同义问句不能与主问题相同")
        return self


class KnowledgeBaseTargetingRequest(BaseModel):
    knowledge_base_ids: list[UUID] = Field(min_length=1, max_length=20)

    @field_validator("knowledge_base_ids")
    @classmethod
    def validate_unique_knowledge_base_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("目标知识库不能重复")
        return values


class CreateParentSubmissionRequest(KnowledgeBaseTargetingRequest):
    parent: ParentContentInput
    primary_child: ChildContentInput


class CreateChildSubmissionRequest(KnowledgeBaseTargetingRequest):
    parent_id: UUID
    child: ChildContentInput


class CreateParentRevisionSubmissionRequest(BaseModel):
    parent: ParentContentInput
    primary_child: ChildContentInput


class CreateChildRevisionSubmissionRequest(KnowledgeBaseTargetingRequest):
    child: ChildContentInput


class AvailableKnowledgeBaseResponse(BaseModel):
    id: UUID
    logical_key: str
    name: str


class AvailableParentResponse(BaseModel):
    id: UUID
    name: str
    canonical_keyword: str
    primary_child_id: UUID
    available_knowledge_bases: list[AvailableKnowledgeBaseResponse]


class ReviewSubmitterResponse(BaseModel):
    id: UUID
    username: str
    display_name: str


class ReviewSubmissionTargetResponse(AvailableKnowledgeBaseResponse):
    status: ReviewTargetStatus
    review_comment: str | None = None
    reviewer: ReviewSubmitterResponse | None = None
    reviewed_at: datetime | None = None
    review_decision: ReviewDecisionKind | None = None


class ReviewParentRevisionResponse(BaseModel):
    id: UUID
    revision_number: int
    name: str
    canonical_keyword: str
    lexical_rules: list[ParentLexicalRuleInput]


class ReviewChildRevisionResponse(BaseModel):
    id: UUID
    revision_number: int
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
    attachments: list[EvidenceAttachmentResponse]
    web_links: list[WebLinkInput]


class ReviewSubmissionResponse(BaseModel):
    id: UUID
    submission_kind: ReviewSubmissionKind
    status: ReviewSubmissionStatus
    parent_id: UUID
    parent_revision_id: UUID | None
    child_id: UUID
    child_revision_id: UUID
    title: str
    submitter: ReviewSubmitterResponse
    targets: list[ReviewSubmissionTargetResponse]
    submitted_at: datetime
    parent_revision: ReviewParentRevisionResponse | None = None
    child_revision: ReviewChildRevisionResponse | None = None


class ReviewDecisionRequest(BaseModel):
    decision: ReviewDecisionKind
    comment: str | None = Field(default=None, max_length=4_000)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str | None) -> str | None:
        return normalize_optional_content(value)


class ReviewDecisionResponse(BaseModel):
    id: UUID
    review_submission_id: UUID
    knowledge_base_id: UUID
    decision: ReviewDecisionKind
    comment: str | None
    decided_by_user_id: UUID
    decided_at: datetime


class ReviewQueueItemResponse(BaseModel):
    id: UUID
    review_submission_id: UUID
    submission_kind: ReviewSubmissionKind
    submission_status: ReviewSubmissionStatus
    target_status: ReviewTargetStatus
    parent_id: UUID
    parent_revision_id: UUID | None
    child_id: UUID
    child_revision_id: UUID
    knowledge_base_id: UUID
    knowledge_base: AvailableKnowledgeBaseResponse
    submitter: ReviewSubmitterResponse
    reviewer: ReviewSubmitterResponse | None = None
    review_decision: ReviewDecisionKind | None = None
    review_comment: str | None = None
    parent_revision: ReviewParentRevisionResponse | None
    child_revision: ReviewChildRevisionResponse
    submitted_at: datetime
    reviewed_at: datetime | None = None


class ManagedKnowledgeEntryResponse(BaseModel):
    """A knowledge publication as managed by a system administrator."""

    child_id: UUID
    parent_id: UUID
    parent_name: str
    is_primary: bool
    knowledge_base: AvailableKnowledgeBaseResponse
    status: ChildPublicationStatus
    child_revision: ReviewChildRevisionResponse
    uploaded_by: ReviewSubmitterResponse
    uploaded_at: datetime
    embedded_at: datetime | None = None
    archived_at: datetime | None = None


class EditableContentEntryResponse(BaseModel):
    """A currently published revision that can be submitted as a new revision."""

    child_id: UUID
    parent_id: UUID
    parent_name: str
    is_primary: bool
    knowledge_bases: list[AvailableKnowledgeBaseResponse]
    parent_revision: ReviewParentRevisionResponse | None = None
    child_revision: ReviewChildRevisionResponse


class PublicationResponse(BaseModel):
    child_id: UUID
    knowledge_base_id: UUID
    status: ChildPublicationStatus
    active_revision_id: UUID | None
    pending_submission_id: UUID | None
    archived_at: datetime | None
