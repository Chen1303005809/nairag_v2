from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_content import ReviewDecision
from app.models.review_history_access import ReviewHistoryAccess
from app.models.user_account import UserAccount, UserRole


class ReviewHistoryTargetNotFoundError(Exception):
    pass


class ReviewHistorySelfGrantError(Exception):
    pass


class ReviewHistoryViewerInactiveError(Exception):
    pass


@dataclass(frozen=True)
class ReviewHistoryAccessWithUser:
    access: ReviewHistoryAccess
    reviewer: UserAccount


async def list_review_history_targets(session: AsyncSession) -> list[UserAccount]:
    """Return every account with a recorded review decision, including inactive accounts."""

    statement = (
        select(UserAccount)
        .join(ReviewDecision, ReviewDecision.decided_by_user_id == UserAccount.id)
        .distinct()
        .order_by(UserAccount.display_name, UserAccount.username)
    )
    return list((await session.scalars(statement)).all())


async def list_review_history_access(
    session: AsyncSession,
    *,
    viewer_user_id: UUID,
) -> list[ReviewHistoryAccessWithUser]:
    statement = (
        select(ReviewHistoryAccess, UserAccount)
        .join(UserAccount, UserAccount.id == ReviewHistoryAccess.reviewer_user_id)
        .where(ReviewHistoryAccess.viewer_user_id == viewer_user_id)
        .order_by(UserAccount.display_name, UserAccount.username)
    )
    return [
        ReviewHistoryAccessWithUser(access=access, reviewer=reviewer)
        for access, reviewer in (await session.execute(statement)).all()
    ]


async def list_viewable_review_history_subjects(
    session: AsyncSession,
    *,
    viewer_user_id: UUID,
    viewer_role: UserRole,
) -> list[tuple[UserAccount, bool]]:
    subjects: dict[UUID, tuple[UserAccount, bool]] = {}
    if viewer_role in {UserRole.REVIEW_ADMIN, UserRole.SYSTEM_ADMIN}:
        viewer = await session.get(UserAccount, viewer_user_id)
        if viewer is not None:
            subjects[viewer.id] = (viewer, True)

    statement = (
        select(UserAccount)
        .join(ReviewHistoryAccess, ReviewHistoryAccess.reviewer_user_id == UserAccount.id)
        .where(ReviewHistoryAccess.viewer_user_id == viewer_user_id)
        .order_by(UserAccount.display_name, UserAccount.username)
    )
    for reviewer in (await session.scalars(statement)).all():
        subjects[reviewer.id] = (reviewer, False)
    return sorted(
        subjects.values(),
        key=lambda item: (not item[1], item[0].display_name.casefold(), item[0].username),
    )


async def has_review_history_access(
    session: AsyncSession,
    *,
    viewer_user_id: UUID,
    reviewer_user_id: UUID,
) -> bool:
    access = await session.get(ReviewHistoryAccess, (viewer_user_id, reviewer_user_id))
    return access is not None


async def replace_review_history_access(
    session: AsyncSession,
    *,
    viewer: UserAccount,
    reviewer_user_ids: set[UUID],
    granted_by_user_id: UUID,
) -> tuple[list[ReviewHistoryAccessWithUser], set[UUID], set[UUID]]:
    if viewer.id in reviewer_user_ids:
        raise ReviewHistorySelfGrantError

    current_rows = list(
        (
            await session.scalars(
                select(ReviewHistoryAccess).where(
                    ReviewHistoryAccess.viewer_user_id == viewer.id
                )
            )
        ).all()
    )
    current_ids = {row.reviewer_user_id for row in current_rows}
    added_ids = reviewer_user_ids - current_ids
    removed_ids = current_ids - reviewer_user_ids

    if added_ids and not viewer.is_active:
        raise ReviewHistoryViewerInactiveError

    if added_ids:
        valid_ids = set(
            (
                await session.scalars(
                    select(ReviewDecision.decided_by_user_id)
                    .where(ReviewDecision.decided_by_user_id.in_(added_ids))
                    .distinct()
                )
            ).all()
        )
        if valid_ids != added_ids:
            raise ReviewHistoryTargetNotFoundError

    for row in current_rows:
        if row.reviewer_user_id in removed_ids:
            await session.delete(row)
    for reviewer_user_id in added_ids:
        session.add(
            ReviewHistoryAccess(
                viewer_user_id=viewer.id,
                reviewer_user_id=reviewer_user_id,
                granted_by_user_id=granted_by_user_id,
            )
        )
    await session.flush()
    accesses = await list_review_history_access(session, viewer_user_id=viewer.id)
    return accesses, added_ids, removed_ids
