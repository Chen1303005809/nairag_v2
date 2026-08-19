from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase, ReviewerKnowledgeBase
from app.models.user_account import UserAccount, UserRole
from app.schemas.knowledge_bases import normalize_knowledge_base_key


class KnowledgeBaseKeyAlreadyExistsError(Exception):
    pass


class ReviewerNotEligibleError(Exception):
    pass


@dataclass(frozen=True)
class ReviewerAssignmentWithUser:
    assignment: ReviewerKnowledgeBase
    reviewer: UserAccount


def physical_collection_name(logical_key: str, generation: int) -> str:
    """Derive a deterministic, collision-free, Milvus-safe collection name.

    Both ``-`` and ``_`` are valid in a logical key.  They must remain distinct
    after conversion because Milvus collection names accept only letters,
    numbers, and underscores.
    """

    physical_key = logical_key.replace("_", "_u_").replace("-", "_d_")
    return f"nairag_{physical_key}_g{generation}"


async def get_knowledge_base(
    session: AsyncSession,
    knowledge_base_id: UUID,
) -> KnowledgeBase | None:
    return await session.get(KnowledgeBase, knowledge_base_id)


async def create_knowledge_base(
    session: AsyncSession,
    *,
    logical_key: str,
    name: str,
    description: str | None,
    is_active: bool,
    created_by_user_id: UUID,
) -> KnowledgeBase:
    normalized_key = normalize_knowledge_base_key(logical_key)
    existing = await session.scalar(
        select(KnowledgeBase.id).where(KnowledgeBase.logical_key == normalized_key)
    )
    if existing is not None:
        raise KnowledgeBaseKeyAlreadyExistsError(normalized_key)

    generation = 1
    knowledge_base = KnowledgeBase(
        logical_key=normalized_key,
        name=name,
        description=description,
        is_active=is_active,
        current_collection_generation=generation,
        current_physical_collection_name=physical_collection_name(normalized_key, generation),
        created_by_user_id=created_by_user_id,
    )
    session.add(knowledge_base)
    await session.flush()
    return knowledge_base


async def list_active_knowledge_bases(session: AsyncSession) -> list[KnowledgeBase]:
    statement = (
        select(KnowledgeBase)
        .where(KnowledgeBase.is_active.is_(True))
        .order_by(KnowledgeBase.name, KnowledgeBase.logical_key)
    )
    return list((await session.scalars(statement)).all())


async def list_managed_knowledge_bases(
    session: AsyncSession,
) -> list[tuple[KnowledgeBase, int]]:
    statement = (
        select(KnowledgeBase, func.count(ReviewerKnowledgeBase.reviewer_user_id))
        .outerjoin(
            ReviewerKnowledgeBase,
            ReviewerKnowledgeBase.knowledge_base_id == KnowledgeBase.id,
        )
        .group_by(KnowledgeBase.id)
        .order_by(KnowledgeBase.name, KnowledgeBase.logical_key)
    )
    rows = (await session.execute(statement)).all()
    return [
        (knowledge_base, int(reviewer_count))
        for knowledge_base, reviewer_count in rows
    ]


async def list_reviewer_assignments(
    session: AsyncSession,
    knowledge_base_id: UUID,
) -> list[ReviewerAssignmentWithUser]:
    statement = (
        select(ReviewerKnowledgeBase, UserAccount)
        .join(UserAccount, UserAccount.id == ReviewerKnowledgeBase.reviewer_user_id)
        .where(ReviewerKnowledgeBase.knowledge_base_id == knowledge_base_id)
        .order_by(UserAccount.display_name, UserAccount.username)
    )
    return [
        ReviewerAssignmentWithUser(assignment=assignment, reviewer=reviewer)
        for assignment, reviewer in (await session.execute(statement)).all()
    ]


async def list_assigned_knowledge_bases(
    session: AsyncSession,
    reviewer_user_id: UUID,
) -> list[KnowledgeBase]:
    statement = (
        select(KnowledgeBase)
        .join(
            ReviewerKnowledgeBase,
            ReviewerKnowledgeBase.knowledge_base_id == KnowledgeBase.id,
        )
        .where(
            ReviewerKnowledgeBase.reviewer_user_id == reviewer_user_id,
            KnowledgeBase.is_active.is_(True),
        )
        .order_by(KnowledgeBase.name, KnowledgeBase.logical_key)
    )
    return list((await session.scalars(statement)).all())


async def assign_reviewer(
    session: AsyncSession,
    *,
    knowledge_base_id: UUID,
    reviewer_user_id: UUID,
    assigned_by_user_id: UUID,
) -> tuple[ReviewerKnowledgeBase, bool]:
    reviewer = await session.get(UserAccount, reviewer_user_id)
    if reviewer is None or reviewer.role != UserRole.REVIEW_ADMIN or not reviewer.is_active:
        raise ReviewerNotEligibleError(reviewer_user_id)

    existing = await session.get(ReviewerKnowledgeBase, (knowledge_base_id, reviewer_user_id))
    if existing is not None:
        return existing, False

    assignment = ReviewerKnowledgeBase(
        knowledge_base_id=knowledge_base_id,
        reviewer_user_id=reviewer_user_id,
        assigned_by_user_id=assigned_by_user_id,
    )
    session.add(assignment)
    await session.flush()
    return assignment, True


async def remove_reviewer_assignment(
    session: AsyncSession,
    *,
    knowledge_base_id: UUID,
    reviewer_user_id: UUID,
) -> bool:
    assignment = await session.get(ReviewerKnowledgeBase, (knowledge_base_id, reviewer_user_id))
    if assignment is None:
        return False
    await session.delete(assignment)
    await session.flush()
    return True


async def count_reviewer_assignments_for_user(session: AsyncSession, reviewer_user_id: UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(ReviewerKnowledgeBase)
        .where(ReviewerKnowledgeBase.reviewer_user_id == reviewer_user_id)
    )
    return int(count or 0)
