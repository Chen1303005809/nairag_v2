from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.intelligent_ingestion import KnowledgeDraft, KnowledgeDraftSource
from app.schemas.drafts import KnowledgeDraftInput
from app.schemas.knowledge_content import ChildContentInput, WebLinkInput
from app.services.knowledge_content import SubmissionDetails, submit_new_child


class DraftNotFoundError(Exception):
    pass


class DraftNotSubmittableError(Exception):
    pass


def _apply_draft_content(draft: KnowledgeDraft, content: KnowledgeDraftInput) -> None:
    draft.parent_id = content.parent_id
    draft.question = content.question
    draft.response_content = content.response_content
    draft.question_variants = content.question_variants
    draft.follow_up_guidance = content.follow_up_guidance
    draft.question_type = content.question_type
    draft.business_object = content.business_object
    draft.purpose = content.purpose
    draft.customer_type = content.customer_type
    draft.feature_explanation = content.feature_explanation
    draft.example = content.example
    draft.internal_notes = content.internal_notes
    draft.attachments = [str(attachment_id) for attachment_id in content.attachments]
    draft.web_links = [web_link.model_dump() for web_link in content.web_links]
    draft.knowledge_base_ids = [
        str(knowledge_base_id) for knowledge_base_id in content.knowledge_base_ids
    ]


async def create_manual_draft(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    content: KnowledgeDraftInput,
) -> KnowledgeDraft:
    draft = KnowledgeDraft(
        owner_user_id=owner_user_id,
        source=KnowledgeDraftSource.MANUAL_SAVED,
    )
    _apply_draft_content(draft, content)
    session.add(draft)
    await session.flush()
    return draft


async def get_draft(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    draft_id: UUID,
) -> KnowledgeDraft:
    draft = await session.scalar(
        select(KnowledgeDraft).where(
            KnowledgeDraft.id == draft_id,
            KnowledgeDraft.owner_user_id == owner_user_id,
        )
    )
    if draft is None:
        raise DraftNotFoundError(draft_id)
    return draft


async def list_drafts(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
) -> list[KnowledgeDraft]:
    return list(
        (
            await session.scalars(
                select(KnowledgeDraft)
                .where(KnowledgeDraft.owner_user_id == owner_user_id)
                .order_by(KnowledgeDraft.updated_at.desc(), KnowledgeDraft.created_at.desc())
                .limit(100)
            )
        ).all()
    )


async def update_draft(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    draft_id: UUID,
    content: KnowledgeDraftInput,
) -> KnowledgeDraft:
    draft = await get_draft(session, owner_user_id=owner_user_id, draft_id=draft_id)
    _apply_draft_content(draft, content)
    await session.flush()
    return draft


async def delete_draft(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    draft_id: UUID,
) -> None:
    draft = await get_draft(session, owner_user_id=owner_user_id, draft_id=draft_id)
    await session.delete(draft)
    await session.flush()


def _draft_child_content(draft: KnowledgeDraft) -> ChildContentInput:
    if not draft.question or not draft.response_content:
        raise DraftNotSubmittableError("草稿提交前必须填写问题和回复内容")
    return ChildContentInput(
        question=draft.question,
        response_content=draft.response_content,
        question_variants=list(draft.question_variants),
        follow_up_guidance=draft.follow_up_guidance,
        question_type=draft.question_type,
        business_object=draft.business_object,
        purpose=draft.purpose,
        customer_type=draft.customer_type,
        feature_explanation=draft.feature_explanation,
        example=draft.example,
        internal_notes=draft.internal_notes,
        attachments=[UUID(value) for value in draft.attachments],
        web_links=[WebLinkInput(**web_link) for web_link in draft.web_links],
    )


async def submit_draft(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    draft_id: UUID,
) -> SubmissionDetails:
    """Submit one draft as a new ordinary child review submission.

    The draft row is deleted in the same transaction once submission succeeds,
    so any domain validation failure leaves the draft untouched.
    """

    draft = await get_draft(session, owner_user_id=owner_user_id, draft_id=draft_id)
    if draft.parent_id is None:
        raise DraftNotSubmittableError("草稿提交前必须选择父类")
    if not draft.knowledge_base_ids:
        raise DraftNotSubmittableError("草稿提交前必须选择目标知识库")

    submission = await submit_new_child(
        session,
        parent_id=draft.parent_id,
        child_content=_draft_child_content(draft),
        knowledge_base_ids=[UUID(value) for value in draft.knowledge_base_ids],
        submitted_by_user_id=owner_user_id,
    )
    await session.delete(draft)
    await session.flush()
    return submission


def draft_source_label(draft: KnowledgeDraft) -> str:
    if draft.source == KnowledgeDraftSource.INTELLIGENT_GENERATED:
        return "智能生成"
    return "手动保存"


def format_draft_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
