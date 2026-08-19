from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase, ReviewerKnowledgeBase
from app.models.knowledge_content import (
    Child,
    ChildKnowledgeBasePublication,
    ChildPublicationStatus,
    ChildRevision,
    ChildRevisionQuestionVariant,
    Parent,
    ParentLexicalRule,
    ParentRevision,
    ReviewDecision,
    ReviewDecisionKind,
    ReviewSubmission,
    ReviewSubmissionKind,
    ReviewSubmissionStatus,
    ReviewSubmissionTarget,
    ReviewTargetStatus,
)
from app.models.user_account import UserAccount, UserRole
from app.schemas.knowledge_content import ChildContentInput, ParentContentInput
from app.services.index_jobs import enqueue_index_jobs_for_submission


class ParentNotFoundError(Exception):
    pass


class ParentNotAvailableError(Exception):
    pass


class ChildNotFoundError(Exception):
    pass


class PrimaryChildRevisionError(Exception):
    pass


class TargetKnowledgeBaseUnavailableError(Exception):
    pass


class TargetKnowledgeBaseNotAllowedError(Exception):
    pass


class PendingSubmissionExistsError(Exception):
    pass


class ReviewAccessDeniedError(Exception):
    pass


class ReviewTargetNotFoundError(Exception):
    pass


class ReviewDecisionAlreadyExistsError(Exception):
    pass


class ReviewTargetStateError(Exception):
    pass


class ReviewPublicationNotFoundError(Exception):
    pass


class ReviewPublicationNotReadyError(Exception):
    pass


class ReviewPublicationConflictError(Exception):
    pass


@dataclass(frozen=True)
class AvailableParentDetails:
    parent: Parent
    parent_revision: ParentRevision
    primary_child: Child
    knowledge_bases: list[KnowledgeBase]


@dataclass(frozen=True)
class SubmissionDetails:
    submission: ReviewSubmission
    title: str
    targets: list[tuple[ReviewSubmissionTarget, KnowledgeBase]]


@dataclass(frozen=True)
class ReviewQueueDetails:
    submission: ReviewSubmission
    target: ReviewSubmissionTarget
    knowledge_base: KnowledgeBase
    submitter: UserAccount
    parent_revision: ParentRevision | None
    parent_lexical_rules: list[ParentLexicalRule]
    child_revision: ChildRevision
    child_question_variants: list[ChildRevisionQuestionVariant]


async def _get_parent(
    session: AsyncSession,
    parent_id: UUID,
    *,
    lock: bool = False,
) -> Parent:
    statement = select(Parent).where(Parent.id == parent_id)
    if lock:
        statement = statement.with_for_update()
    parent = await session.scalar(statement)
    if parent is None:
        raise ParentNotFoundError(parent_id)
    return parent


async def _get_primary_child(session: AsyncSession, parent_id: UUID) -> Child:
    child = await session.scalar(
        select(Child).where(Child.parent_id == parent_id, Child.is_primary.is_(True))
    )
    if child is None:
        raise ParentNotAvailableError(parent_id)
    return child


async def _latest_published_parent_submission(
    session: AsyncSession,
    parent_id: UUID,
) -> ReviewSubmission | None:
    statement = (
        select(ReviewSubmission)
        .where(
            ReviewSubmission.parent_id == parent_id,
            ReviewSubmission.submission_kind == ReviewSubmissionKind.PARENT_WITH_PRIMARY,
            ReviewSubmission.status == ReviewSubmissionStatus.PUBLISHED,
        )
        .order_by(ReviewSubmission.submitted_at.desc(), ReviewSubmission.id.desc())
    )
    return await session.scalar(statement)


async def _published_primary_knowledge_bases(
    session: AsyncSession,
    primary_child_id: UUID,
) -> list[KnowledgeBase]:
    statement = (
        select(KnowledgeBase)
        .join(
            ChildKnowledgeBasePublication,
            ChildKnowledgeBasePublication.knowledge_base_id == KnowledgeBase.id,
        )
        .where(
            ChildKnowledgeBasePublication.child_id == primary_child_id,
            ChildKnowledgeBasePublication.status == ChildPublicationStatus.PUBLISHED,
            ChildKnowledgeBasePublication.active_revision_id.is_not(None),
            KnowledgeBase.is_active.is_(True),
        )
        .order_by(KnowledgeBase.name, KnowledgeBase.logical_key)
    )
    return list((await session.scalars(statement)).all())


async def get_available_parent_details(
    session: AsyncSession,
    parent_id: UUID,
    *,
    lock: bool = False,
) -> AvailableParentDetails:
    parent = await _get_parent(session, parent_id, lock=lock)
    latest_submission = await _latest_published_parent_submission(session, parent.id)
    if latest_submission is None or latest_submission.parent_revision_id is None:
        raise ParentNotAvailableError(parent.id)

    parent_revision = await session.get(ParentRevision, latest_submission.parent_revision_id)
    primary_child = await _get_primary_child(session, parent.id)
    knowledge_bases = await _published_primary_knowledge_bases(session, primary_child.id)
    if (
        parent_revision is None
        or latest_submission.child_id != primary_child.id
        or not knowledge_bases
    ):
        raise ParentNotAvailableError(parent.id)

    return AvailableParentDetails(
        parent=parent,
        parent_revision=parent_revision,
        primary_child=primary_child,
        knowledge_bases=knowledge_bases,
    )


async def list_available_parents(session: AsyncSession) -> list[AvailableParentDetails]:
    parent_ids = list((await session.scalars(select(Parent.id).order_by(Parent.created_at))).all())
    available: list[AvailableParentDetails] = []
    for parent_id in parent_ids:
        try:
            available.append(await get_available_parent_details(session, parent_id))
        except ParentNotAvailableError:
            continue
    return available


async def _active_knowledge_bases_by_id(
    session: AsyncSession,
    knowledge_base_ids: list[UUID],
) -> dict[UUID, KnowledgeBase]:
    statement = select(KnowledgeBase).where(
        KnowledgeBase.id.in_(knowledge_base_ids),
        KnowledgeBase.is_active.is_(True),
    )
    knowledge_bases = list((await session.scalars(statement)).all())
    by_id = {knowledge_base.id: knowledge_base for knowledge_base in knowledge_bases}
    if len(by_id) != len(knowledge_base_ids):
        raise TargetKnowledgeBaseUnavailableError()
    return by_id


def _assert_target_subset(
    knowledge_base_ids: list[UUID],
    allowed_knowledge_bases: list[KnowledgeBase],
) -> dict[UUID, KnowledgeBase]:
    allowed_by_id = {
        knowledge_base.id: knowledge_base for knowledge_base in allowed_knowledge_bases
    }
    if any(knowledge_base_id not in allowed_by_id for knowledge_base_id in knowledge_base_ids):
        raise TargetKnowledgeBaseNotAllowedError()
    return {
        knowledge_base_id: allowed_by_id[knowledge_base_id]
        for knowledge_base_id in knowledge_base_ids
    }


async def _next_parent_revision_number(session: AsyncSession, parent_id: UUID) -> int:
    latest = await session.scalar(
        select(func.max(ParentRevision.revision_number)).where(
            ParentRevision.parent_id == parent_id
        )
    )
    return int(latest or 0) + 1


async def _next_child_revision_number(session: AsyncSession, child_id: UUID) -> int:
    latest = await session.scalar(
        select(func.max(ChildRevision.revision_number)).where(ChildRevision.child_id == child_id)
    )
    return int(latest or 0) + 1


async def _create_parent_revision(
    session: AsyncSession,
    *,
    parent_id: UUID,
    content: ParentContentInput,
    created_by_user_id: UUID,
) -> ParentRevision:
    parent_revision = ParentRevision(
        parent_id=parent_id,
        revision_number=await _next_parent_revision_number(session, parent_id),
        name=content.name,
        canonical_keyword=content.canonical_keyword,
        created_by_user_id=created_by_user_id,
    )
    session.add(parent_revision)
    await session.flush()
    session.add_all(
        ParentLexicalRule(
            parent_revision_id=parent_revision.id,
            rule_type=rule.rule_type,
            rule_value=rule.rule_value,
            sort_order=index,
        )
        for index, rule in enumerate(content.lexical_rules)
    )
    return parent_revision


async def _create_child_revision(
    session: AsyncSession,
    *,
    child_id: UUID,
    content: ChildContentInput,
    created_by_user_id: UUID,
) -> ChildRevision:
    child_revision = ChildRevision(
        child_id=child_id,
        revision_number=await _next_child_revision_number(session, child_id),
        question=content.question,
        response_content=content.response_content,
        follow_up_guidance=content.follow_up_guidance,
        question_type=content.question_type,
        business_object=content.business_object,
        purpose=content.purpose,
        customer_type=content.customer_type,
        feature_explanation=content.feature_explanation,
        example=content.example,
        internal_notes=content.internal_notes,
        created_by_user_id=created_by_user_id,
    )
    session.add(child_revision)
    await session.flush()
    session.add_all(
        ChildRevisionQuestionVariant(
            child_revision_id=child_revision.id,
            question_text=question_variant,
            sort_order=index,
        )
        for index, question_variant in enumerate(content.question_variants)
    )
    return child_revision


async def _create_submission(
    session: AsyncSession,
    *,
    submission_kind: ReviewSubmissionKind,
    parent_id: UUID,
    parent_revision_id: UUID | None,
    child_id: UUID,
    child_revision_id: UUID,
    submitted_by_user_id: UUID,
    knowledge_base_ids: list[UUID],
) -> tuple[ReviewSubmission, list[ReviewSubmissionTarget]]:
    submission = ReviewSubmission(
        submission_kind=submission_kind,
        status=ReviewSubmissionStatus.PENDING_REVIEW,
        parent_id=parent_id,
        parent_revision_id=parent_revision_id,
        child_id=child_id,
        child_revision_id=child_revision_id,
        submitted_by_user_id=submitted_by_user_id,
    )
    session.add(submission)
    await session.flush()
    targets = [
        ReviewSubmissionTarget(
            review_submission_id=submission.id,
            knowledge_base_id=knowledge_base_id,
            status=ReviewTargetStatus.PENDING_REVIEW,
        )
        for knowledge_base_id in knowledge_base_ids
    ]
    session.add_all(targets)
    return submission, targets


async def _assert_no_open_parent_aggregate_submission(
    session: AsyncSession,
    parent_id: UUID,
) -> None:
    open_submission = await session.scalar(
        select(ReviewSubmission.id).where(
            ReviewSubmission.parent_id == parent_id,
            ReviewSubmission.submission_kind == ReviewSubmissionKind.PARENT_WITH_PRIMARY,
            ReviewSubmission.status.in_(
                (
                    ReviewSubmissionStatus.PENDING_REVIEW,
                    ReviewSubmissionStatus.INDEXING,
                    ReviewSubmissionStatus.INDEX_FAILED,
                )
            ),
        )
    )
    if open_submission is not None:
        raise PendingSubmissionExistsError()


async def _locked_publications(
    session: AsyncSession,
    *,
    child_id: UUID,
    knowledge_base_ids: list[UUID],
) -> dict[UUID, ChildKnowledgeBasePublication]:
    statement = (
        select(ChildKnowledgeBasePublication)
        .where(
            ChildKnowledgeBasePublication.child_id == child_id,
            ChildKnowledgeBasePublication.knowledge_base_id.in_(knowledge_base_ids),
        )
        .with_for_update()
    )
    publications = list((await session.scalars(statement)).all())
    by_knowledge_base_id = {
        publication.knowledge_base_id: publication for publication in publications
    }
    if any(publication.pending_submission_id is not None for publication in publications):
        raise PendingSubmissionExistsError()
    return by_knowledge_base_id


async def submit_new_parent_aggregate(
    session: AsyncSession,
    *,
    parent_content: ParentContentInput,
    primary_child_content: ChildContentInput,
    knowledge_base_ids: list[UUID],
    submitted_by_user_id: UUID,
) -> SubmissionDetails:
    knowledge_bases_by_id = await _active_knowledge_bases_by_id(session, knowledge_base_ids)
    parent = Parent(created_by_user_id=submitted_by_user_id)
    session.add(parent)
    await session.flush()
    parent_revision = await _create_parent_revision(
        session,
        parent_id=parent.id,
        content=parent_content,
        created_by_user_id=submitted_by_user_id,
    )
    primary_child = Child(
        parent_id=parent.id,
        is_primary=True,
        created_by_user_id=submitted_by_user_id,
    )
    session.add(primary_child)
    await session.flush()
    child_revision = await _create_child_revision(
        session,
        child_id=primary_child.id,
        content=primary_child_content,
        created_by_user_id=submitted_by_user_id,
    )
    submission, targets = await _create_submission(
        session,
        submission_kind=ReviewSubmissionKind.PARENT_WITH_PRIMARY,
        parent_id=parent.id,
        parent_revision_id=parent_revision.id,
        child_id=primary_child.id,
        child_revision_id=child_revision.id,
        submitted_by_user_id=submitted_by_user_id,
        knowledge_base_ids=knowledge_base_ids,
    )
    session.add_all(
        ChildKnowledgeBasePublication(
            child_id=primary_child.id,
            knowledge_base_id=knowledge_base_id,
            status=ChildPublicationStatus.PENDING,
            pending_submission_id=submission.id,
        )
        for knowledge_base_id in knowledge_base_ids
    )
    return SubmissionDetails(
        submission=submission,
        title=parent_revision.name,
        targets=[(target, knowledge_bases_by_id[target.knowledge_base_id]) for target in targets],
    )


async def submit_new_child(
    session: AsyncSession,
    *,
    parent_id: UUID,
    child_content: ChildContentInput,
    knowledge_base_ids: list[UUID],
    submitted_by_user_id: UUID,
) -> SubmissionDetails:
    parent_details = await get_available_parent_details(session, parent_id, lock=True)
    knowledge_bases_by_id = _assert_target_subset(
        knowledge_base_ids,
        parent_details.knowledge_bases,
    )
    child = Child(
        parent_id=parent_details.parent.id,
        is_primary=False,
        created_by_user_id=submitted_by_user_id,
    )
    session.add(child)
    await session.flush()
    child_revision = await _create_child_revision(
        session,
        child_id=child.id,
        content=child_content,
        created_by_user_id=submitted_by_user_id,
    )
    submission, targets = await _create_submission(
        session,
        submission_kind=ReviewSubmissionKind.CHILD,
        parent_id=parent_details.parent.id,
        parent_revision_id=None,
        child_id=child.id,
        child_revision_id=child_revision.id,
        submitted_by_user_id=submitted_by_user_id,
        knowledge_base_ids=knowledge_base_ids,
    )
    session.add_all(
        ChildKnowledgeBasePublication(
            child_id=child.id,
            knowledge_base_id=knowledge_base_id,
            status=ChildPublicationStatus.PENDING,
            pending_submission_id=submission.id,
        )
        for knowledge_base_id in knowledge_base_ids
    )
    return SubmissionDetails(
        submission=submission,
        title=child_revision.question,
        targets=[(target, knowledge_bases_by_id[target.knowledge_base_id]) for target in targets],
    )


async def submit_parent_aggregate_revision(
    session: AsyncSession,
    *,
    parent_id: UUID,
    parent_content: ParentContentInput,
    primary_child_content: ChildContentInput,
    submitted_by_user_id: UUID,
) -> SubmissionDetails:
    parent_details = await get_available_parent_details(session, parent_id, lock=True)
    await _assert_no_open_parent_aggregate_submission(session, parent_details.parent.id)
    knowledge_base_ids = [knowledge_base.id for knowledge_base in parent_details.knowledge_bases]
    publications = await _locked_publications(
        session,
        child_id=parent_details.primary_child.id,
        knowledge_base_ids=knowledge_base_ids,
    )
    if len(publications) != len(knowledge_base_ids):
        raise ParentNotAvailableError(parent_details.parent.id)

    parent_revision = await _create_parent_revision(
        session,
        parent_id=parent_details.parent.id,
        content=parent_content,
        created_by_user_id=submitted_by_user_id,
    )
    child_revision = await _create_child_revision(
        session,
        child_id=parent_details.primary_child.id,
        content=primary_child_content,
        created_by_user_id=submitted_by_user_id,
    )
    submission, targets = await _create_submission(
        session,
        submission_kind=ReviewSubmissionKind.PARENT_WITH_PRIMARY,
        parent_id=parent_details.parent.id,
        parent_revision_id=parent_revision.id,
        child_id=parent_details.primary_child.id,
        child_revision_id=child_revision.id,
        submitted_by_user_id=submitted_by_user_id,
        knowledge_base_ids=knowledge_base_ids,
    )
    for publication in publications.values():
        publication.pending_submission_id = submission.id
    return SubmissionDetails(
        submission=submission,
        title=parent_revision.name,
        targets=[
            (target, parent_details.knowledge_bases[index]) for index, target in enumerate(targets)
        ],
    )


async def submit_child_revision(
    session: AsyncSession,
    *,
    child_id: UUID,
    child_content: ChildContentInput,
    knowledge_base_ids: list[UUID],
    submitted_by_user_id: UUID,
) -> SubmissionDetails:
    child = await session.scalar(select(Child).where(Child.id == child_id).with_for_update())
    if child is None:
        raise ChildNotFoundError(child_id)
    if child.is_primary:
        raise PrimaryChildRevisionError(child_id)

    parent_details = await get_available_parent_details(session, child.parent_id, lock=True)
    knowledge_bases_by_id = _assert_target_subset(
        knowledge_base_ids,
        parent_details.knowledge_bases,
    )
    publications = await _locked_publications(
        session,
        child_id=child.id,
        knowledge_base_ids=knowledge_base_ids,
    )
    if any(
        publication.status == ChildPublicationStatus.ARCHIVED
        for publication in publications.values()
    ):
        raise TargetKnowledgeBaseNotAllowedError()

    child_revision = await _create_child_revision(
        session,
        child_id=child.id,
        content=child_content,
        created_by_user_id=submitted_by_user_id,
    )
    submission, targets = await _create_submission(
        session,
        submission_kind=ReviewSubmissionKind.CHILD,
        parent_id=child.parent_id,
        parent_revision_id=None,
        child_id=child.id,
        child_revision_id=child_revision.id,
        submitted_by_user_id=submitted_by_user_id,
        knowledge_base_ids=knowledge_base_ids,
    )
    for knowledge_base_id in knowledge_base_ids:
        publication = publications.get(knowledge_base_id)
        if publication is None:
            session.add(
                ChildKnowledgeBasePublication(
                    child_id=child.id,
                    knowledge_base_id=knowledge_base_id,
                    status=ChildPublicationStatus.PENDING,
                    pending_submission_id=submission.id,
                )
            )
        else:
            publication.pending_submission_id = submission.id
    return SubmissionDetails(
        submission=submission,
        title=child_revision.question,
        targets=[(target, knowledge_bases_by_id[target.knowledge_base_id]) for target in targets],
    )


async def list_submissions_by_author(
    session: AsyncSession,
    submitted_by_user_id: UUID,
) -> list[SubmissionDetails]:
    statement = (
        select(ReviewSubmission, ParentRevision.name, ChildRevision.question)
        .outerjoin(ParentRevision, ParentRevision.id == ReviewSubmission.parent_revision_id)
        .join(ChildRevision, ChildRevision.id == ReviewSubmission.child_revision_id)
        .where(ReviewSubmission.submitted_by_user_id == submitted_by_user_id)
        .order_by(ReviewSubmission.submitted_at.desc(), ReviewSubmission.id.desc())
    )
    rows = (await session.execute(statement)).all()
    if not rows:
        return []

    submission_ids = [submission.id for submission, _parent_name, _question in rows]
    target_rows = (
        await session.execute(
            select(ReviewSubmissionTarget, KnowledgeBase)
            .join(KnowledgeBase, KnowledgeBase.id == ReviewSubmissionTarget.knowledge_base_id)
            .where(ReviewSubmissionTarget.review_submission_id.in_(submission_ids))
            .order_by(KnowledgeBase.name, KnowledgeBase.logical_key)
        )
    ).all()
    targets_by_submission_id: dict[UUID, list[tuple[ReviewSubmissionTarget, KnowledgeBase]]] = {}
    for target, knowledge_base in target_rows:
        targets_by_submission_id.setdefault(target.review_submission_id, []).append(
            (target, knowledge_base)
        )

    return [
        SubmissionDetails(
            submission=submission,
            title=parent_name or question,
            targets=targets_by_submission_id.get(submission.id, []),
        )
        for submission, parent_name, question in rows
    ]


async def _reviewer_has_knowledge_base_access(
    session: AsyncSession,
    *,
    reviewer_user_id: UUID,
    knowledge_base_id: UUID,
) -> bool:
    assignment = await session.scalar(
        select(ReviewerKnowledgeBase.knowledge_base_id).where(
            ReviewerKnowledgeBase.reviewer_user_id == reviewer_user_id,
            ReviewerKnowledgeBase.knowledge_base_id == knowledge_base_id,
        )
    )
    return assignment is not None


async def _assert_review_access(
    session: AsyncSession,
    *,
    actor_user_id: UUID,
    actor_role: UserRole,
    knowledge_base_id: UUID,
) -> None:
    if actor_role == UserRole.SYSTEM_ADMIN:
        return
    if actor_role != UserRole.REVIEW_ADMIN or not await _reviewer_has_knowledge_base_access(
        session,
        reviewer_user_id=actor_user_id,
        knowledge_base_id=knowledge_base_id,
    ):
        raise ReviewAccessDeniedError(knowledge_base_id)


async def list_review_queue(
    session: AsyncSession,
    *,
    actor_user_id: UUID,
    actor_role: UserRole,
    knowledge_base_id: UUID | None = None,
) -> list[ReviewQueueDetails]:
    if knowledge_base_id is not None:
        await _assert_review_access(
            session,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            knowledge_base_id=knowledge_base_id,
        )

    statement = (
        select(
            ReviewSubmissionTarget,
            ReviewSubmission,
            KnowledgeBase,
            UserAccount,
            ParentRevision,
            ChildRevision,
        )
        .join(
            ReviewSubmission,
            ReviewSubmission.id == ReviewSubmissionTarget.review_submission_id,
        )
        .join(KnowledgeBase, KnowledgeBase.id == ReviewSubmissionTarget.knowledge_base_id)
        .join(UserAccount, UserAccount.id == ReviewSubmission.submitted_by_user_id)
        .outerjoin(ParentRevision, ParentRevision.id == ReviewSubmission.parent_revision_id)
        .join(ChildRevision, ChildRevision.id == ReviewSubmission.child_revision_id)
        .where(
            ReviewSubmissionTarget.status == ReviewTargetStatus.PENDING_REVIEW,
            ReviewSubmission.status == ReviewSubmissionStatus.PENDING_REVIEW,
        )
        .order_by(ReviewSubmission.submitted_at, ReviewSubmission.id)
    )
    if knowledge_base_id is not None:
        statement = statement.where(ReviewSubmissionTarget.knowledge_base_id == knowledge_base_id)
    elif actor_role != UserRole.SYSTEM_ADMIN:
        statement = statement.where(
            ReviewSubmissionTarget.knowledge_base_id.in_(
                select(ReviewerKnowledgeBase.knowledge_base_id).where(
                    ReviewerKnowledgeBase.reviewer_user_id == actor_user_id
                )
            )
        )

    rows = (await session.execute(statement)).all()
    if not rows:
        return []

    parent_revision_ids = {
        parent_revision.id for _target, _submission, _kb, _user, parent_revision, _child in rows
        if parent_revision is not None
    }
    child_revision_ids = {child_revision.id for *_prefix, child_revision in rows}
    rules_by_revision: dict[UUID, list[ParentLexicalRule]] = {}
    if parent_revision_ids:
        rule_rows = await session.scalars(
            select(ParentLexicalRule)
            .where(ParentLexicalRule.parent_revision_id.in_(parent_revision_ids))
            .order_by(ParentLexicalRule.parent_revision_id, ParentLexicalRule.sort_order)
        )
        for rule in rule_rows:
            rules_by_revision.setdefault(rule.parent_revision_id, []).append(rule)
    variants_by_revision: dict[UUID, list[ChildRevisionQuestionVariant]] = {}
    variant_rows = await session.scalars(
        select(ChildRevisionQuestionVariant)
        .where(ChildRevisionQuestionVariant.child_revision_id.in_(child_revision_ids))
        .order_by(
            ChildRevisionQuestionVariant.child_revision_id,
            ChildRevisionQuestionVariant.sort_order,
        )
    )
    for variant in variant_rows:
        variants_by_revision.setdefault(variant.child_revision_id, []).append(variant)

    return [
        ReviewQueueDetails(
            submission=submission,
            target=target,
            knowledge_base=knowledge_base,
            submitter=submitter,
            parent_revision=parent_revision,
            parent_lexical_rules=(
                rules_by_revision.get(parent_revision.id, [])
                if parent_revision is not None
                else []
            ),
            child_revision=child_revision,
            child_question_variants=variants_by_revision.get(child_revision.id, []),
        )
        for target, submission, knowledge_base, submitter, parent_revision, child_revision in rows
    ]


async def _get_locked_review_target(
    session: AsyncSession,
    *,
    review_submission_id: UUID,
    knowledge_base_id: UUID,
) -> tuple[ReviewSubmissionTarget, ReviewSubmission]:
    row = (
        await session.execute(
            select(ReviewSubmissionTarget, ReviewSubmission)
            .join(
                ReviewSubmission,
                ReviewSubmission.id == ReviewSubmissionTarget.review_submission_id,
            )
            .where(
                ReviewSubmissionTarget.review_submission_id == review_submission_id,
                ReviewSubmissionTarget.knowledge_base_id == knowledge_base_id,
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        raise ReviewTargetNotFoundError(review_submission_id, knowledge_base_id)
    return row


async def _locked_submission_targets(
    session: AsyncSession,
    review_submission_id: UUID,
) -> list[ReviewSubmissionTarget]:
    return list(
        (
            await session.scalars(
                select(ReviewSubmissionTarget)
                .where(ReviewSubmissionTarget.review_submission_id == review_submission_id)
                .order_by(ReviewSubmissionTarget.knowledge_base_id)
                .with_for_update()
            )
        ).all()
    )


async def _locked_publication_for_target(
    session: AsyncSession,
    *,
    child_id: UUID,
    knowledge_base_id: UUID,
) -> ChildKnowledgeBasePublication | None:
    return await session.scalar(
        select(ChildKnowledgeBasePublication)
        .where(
            ChildKnowledgeBasePublication.child_id == child_id,
            ChildKnowledgeBasePublication.knowledge_base_id == knowledge_base_id,
        )
        .with_for_update()
    )


async def _clear_pending_submission_slots(
    session: AsyncSession,
    *,
    child_id: UUID,
    review_submission_id: UUID,
) -> None:
    publications = list(
        (
            await session.scalars(
                select(ChildKnowledgeBasePublication)
                .where(
                    ChildKnowledgeBasePublication.child_id == child_id,
                    ChildKnowledgeBasePublication.pending_submission_id == review_submission_id,
                )
                .with_for_update()
            )
        ).all()
    )
    for publication in publications:
        publication.pending_submission_id = None


def _refresh_submission_status(
    submission: ReviewSubmission,
    targets: list[ReviewSubmissionTarget],
) -> None:
    statuses = {target.status for target in targets}
    if submission.submission_kind == ReviewSubmissionKind.PARENT_WITH_PRIMARY:
        if ReviewTargetStatus.REJECTED in statuses:
            submission.status = ReviewSubmissionStatus.REJECTED
        elif ReviewTargetStatus.PENDING_REVIEW in statuses:
            submission.status = ReviewSubmissionStatus.PENDING_REVIEW
        elif statuses and statuses == {ReviewTargetStatus.PUBLISHED}:
            submission.status = ReviewSubmissionStatus.PUBLISHED
        elif statuses and statuses <= {
            ReviewTargetStatus.APPROVED,
            ReviewTargetStatus.INDEXING,
            ReviewTargetStatus.PUBLISHED,
        }:
            submission.status = ReviewSubmissionStatus.INDEXING
        elif ReviewTargetStatus.INDEX_FAILED in statuses:
            submission.status = ReviewSubmissionStatus.INDEX_FAILED
        else:
            submission.status = ReviewSubmissionStatus.PENDING_REVIEW
        return

    if ReviewTargetStatus.PENDING_REVIEW in statuses:
        submission.status = ReviewSubmissionStatus.PENDING_REVIEW
    elif ReviewTargetStatus.INDEX_FAILED in statuses:
        submission.status = ReviewSubmissionStatus.INDEX_FAILED
    elif ReviewTargetStatus.INDEXING in statuses:
        submission.status = ReviewSubmissionStatus.INDEXING
    elif ReviewTargetStatus.APPROVED in statuses:
        submission.status = ReviewSubmissionStatus.INDEXING
    elif ReviewTargetStatus.PUBLISHED in statuses:
        submission.status = ReviewSubmissionStatus.PUBLISHED
    else:
        submission.status = ReviewSubmissionStatus.REJECTED


async def decide_review_target(
    session: AsyncSession,
    *,
    review_submission_id: UUID,
    knowledge_base_id: UUID,
    actor_user_id: UUID,
    actor_role: UserRole,
    decision: ReviewDecisionKind,
    comment: str | None,
) -> ReviewDecision:
    await _assert_review_access(
        session,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        knowledge_base_id=knowledge_base_id,
    )
    target, submission = await _get_locked_review_target(
        session,
        review_submission_id=review_submission_id,
        knowledge_base_id=knowledge_base_id,
    )
    existing_decision = await session.scalar(
        select(ReviewDecision).where(
            ReviewDecision.review_submission_id == review_submission_id,
            ReviewDecision.knowledge_base_id == knowledge_base_id,
        )
    )
    if existing_decision is not None:
        raise ReviewDecisionAlreadyExistsError(review_submission_id, knowledge_base_id)
    if target.status != ReviewTargetStatus.PENDING_REVIEW:
        raise ReviewTargetStateError(target.status)
    if submission.status not in {
        ReviewSubmissionStatus.PENDING_REVIEW,
        ReviewSubmissionStatus.INDEXING,
        ReviewSubmissionStatus.INDEX_FAILED,
    }:
        raise ReviewTargetStateError(submission.status)

    review_decision = ReviewDecision(
        review_submission_id=review_submission_id,
        knowledge_base_id=knowledge_base_id,
        decision=decision,
        comment=comment,
        decided_by_user_id=actor_user_id,
    )
    session.add(review_decision)
    await session.flush()
    target.status = (
        ReviewTargetStatus.APPROVED
        if decision == ReviewDecisionKind.APPROVED
        else ReviewTargetStatus.REJECTED
    )

    targets = await _locked_submission_targets(session, submission.id)
    if decision == ReviewDecisionKind.REJECTED:
        await _clear_pending_submission_slots(
            session,
            child_id=submission.child_id,
            review_submission_id=submission.id,
        )
    _refresh_submission_status(submission, targets)
    if submission.submission_kind == ReviewSubmissionKind.PARENT_WITH_PRIMARY and decision == (
        ReviewDecisionKind.REJECTED
    ):
        # A parent aggregate can never publish a partial revision.  It is terminal as
        # soon as one target rejects, while the other target rows remain as immutable
        # evidence of the review work that was still outstanding.
        submission.status = ReviewSubmissionStatus.REJECTED
        await _clear_pending_submission_slots(
            session,
            child_id=submission.child_id,
            review_submission_id=submission.id,
        )
    if decision == ReviewDecisionKind.APPROVED:
        await enqueue_index_jobs_for_submission(
            session,
            review_submission_id=submission.id,
        )
    return review_decision


async def publish_approved_target(
    session: AsyncSession,
    *,
    review_submission_id: UUID,
    knowledge_base_id: UUID,
) -> list[ChildKnowledgeBasePublication]:
    """Atomically activate an approved candidate after its index is ready.

    This is intentionally a service entry point rather than a reviewer-facing
    endpoint.  The future indexing worker calls it after validating its idempotency
    key and successful index write.
    """

    target, submission = await _get_locked_review_target(
        session,
        review_submission_id=review_submission_id,
        knowledge_base_id=knowledge_base_id,
    )
    targets = await _locked_submission_targets(session, submission.id)
    if submission.submission_kind == ReviewSubmissionKind.PARENT_WITH_PRIMARY:
        if all(item.status == ReviewTargetStatus.PUBLISHED for item in targets):
            target_knowledge_base_ids = [item.knowledge_base_id for item in targets]
        elif any(
            item.status not in {ReviewTargetStatus.APPROVED, ReviewTargetStatus.INDEXING}
            for item in targets
        ):
            raise ReviewPublicationNotReadyError(submission.id)
        else:
            target_knowledge_base_ids = [item.knowledge_base_id for item in targets]
    else:
        if target.status == ReviewTargetStatus.PUBLISHED:
            publication = await _locked_publication_for_target(
                session,
                child_id=submission.child_id,
                knowledge_base_id=knowledge_base_id,
            )
            if publication is None:
                raise ReviewPublicationNotFoundError(submission.child_id, knowledge_base_id)
            return [publication]
        if target.status not in {ReviewTargetStatus.APPROVED, ReviewTargetStatus.INDEXING}:
            raise ReviewPublicationNotReadyError(submission.id, knowledge_base_id)
        target_knowledge_base_ids = [knowledge_base_id]

    publications: list[ChildKnowledgeBasePublication] = []
    for target_knowledge_base_id in target_knowledge_base_ids:
        publication = await _locked_publication_for_target(
            session,
            child_id=submission.child_id,
            knowledge_base_id=target_knowledge_base_id,
        )
        if publication is None:
            raise ReviewPublicationNotFoundError(
                submission.child_id,
                target_knowledge_base_id,
            )
        if (
            publication.pending_submission_id is not None
            and publication.pending_submission_id != submission.id
        ):
            raise ReviewPublicationConflictError(submission.id)
        publication.status = ChildPublicationStatus.PUBLISHED
        publication.active_revision_id = submission.child_revision_id
        publication.pending_submission_id = None
        publications.append(publication)

    for item in targets:
        if item.knowledge_base_id in target_knowledge_base_ids:
            item.status = ReviewTargetStatus.PUBLISHED
    _refresh_submission_status(submission, targets)
    if submission.submission_kind == ReviewSubmissionKind.PARENT_WITH_PRIMARY:
        submission.status = ReviewSubmissionStatus.PUBLISHED
    return publications


async def start_target_indexing(
    session: AsyncSession,
    *,
    review_submission_id: UUID,
    knowledge_base_id: UUID,
) -> ReviewSubmissionTarget:
    target, submission = await _get_locked_review_target(
        session,
        review_submission_id=review_submission_id,
        knowledge_base_id=knowledge_base_id,
    )
    if target.status != ReviewTargetStatus.APPROVED:
        raise ReviewTargetStateError(target.status)
    target.status = ReviewTargetStatus.INDEXING
    targets = await _locked_submission_targets(session, submission.id)
    _refresh_submission_status(submission, targets)
    return target


async def mark_target_index_failed(
    session: AsyncSession,
    *,
    review_submission_id: UUID,
    knowledge_base_id: UUID,
) -> ReviewSubmissionTarget:
    target, submission = await _get_locked_review_target(
        session,
        review_submission_id=review_submission_id,
        knowledge_base_id=knowledge_base_id,
    )
    if target.status not in {ReviewTargetStatus.APPROVED, ReviewTargetStatus.INDEXING}:
        raise ReviewTargetStateError(target.status)
    target.status = ReviewTargetStatus.INDEX_FAILED
    targets = await _locked_submission_targets(session, submission.id)
    _refresh_submission_status(submission, targets)
    return target


async def retry_failed_target_indexing(
    session: AsyncSession,
    *,
    review_submission_id: UUID,
    knowledge_base_id: UUID,
) -> ReviewSubmissionTarget:
    target, submission = await _get_locked_review_target(
        session,
        review_submission_id=review_submission_id,
        knowledge_base_id=knowledge_base_id,
    )
    if target.status != ReviewTargetStatus.INDEX_FAILED:
        raise ReviewTargetStateError(target.status)
    target.status = ReviewTargetStatus.APPROVED
    targets = await _locked_submission_targets(session, submission.id)
    _refresh_submission_status(submission, targets)
    return target


async def get_publication(
    session: AsyncSession,
    *,
    child_id: UUID,
    knowledge_base_id: UUID,
    lock: bool = False,
) -> ChildKnowledgeBasePublication | None:
    statement = select(ChildKnowledgeBasePublication).where(
        ChildKnowledgeBasePublication.child_id == child_id,
        ChildKnowledgeBasePublication.knowledge_base_id == knowledge_base_id,
    )
    if lock:
        statement = statement.with_for_update()
    return await session.scalar(statement)


async def archive_publication(
    session: AsyncSession,
    *,
    child_id: UUID,
    knowledge_base_id: UUID,
    actor_user_id: UUID,
    actor_role: UserRole,
) -> ChildKnowledgeBasePublication:
    await _assert_review_access(
        session,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        knowledge_base_id=knowledge_base_id,
    )
    publication = await get_publication(
        session,
        child_id=child_id,
        knowledge_base_id=knowledge_base_id,
        lock=True,
    )
    if publication is None:
        raise ReviewPublicationNotFoundError(child_id, knowledge_base_id)
    if publication.pending_submission_id is not None:
        raise PendingSubmissionExistsError()
    if (
        publication.status != ChildPublicationStatus.PUBLISHED
        or publication.active_revision_id is None
    ):
        raise ReviewTargetStateError(publication.status)
    publication.status = ChildPublicationStatus.ARCHIVED
    publication.archived_at = datetime.now(UTC)
    publication.archived_by_user_id = actor_user_id
    return publication
