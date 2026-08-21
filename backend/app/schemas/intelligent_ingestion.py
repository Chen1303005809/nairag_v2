from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.drafts import KnowledgeDraftResponse
from app.services.conversation import NormalizedConversationMessage


class CreateIngestionBatchRequest(BaseModel):
    messages: list[NormalizedConversationMessage] = Field(min_length=1, max_length=1_000)


class IngestionBatchResponse(BaseModel):
    id: UUID
    status: str
    message_count: int
    source_hash: str
    generated_count: int
    rejected_count: int
    rejection_reasons: list[dict[str, object]]
    model_version: str | None
    last_error: str | None
    created_at: str
    completed_at: str | None


class IngestionBatchDetailResponse(IngestionBatchResponse):
    drafts: list[KnowledgeDraftResponse]
