from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.knowledge_base import KnowledgeBase, ReviewerKnowledgeBase
from app.models.knowledge_content import (
    Child,
    ChildKnowledgeBasePublication,
    ChildPublicationStatus,
    ChildRevision,
    ChildRevisionQuestionVariant,
    EvidenceAttachment,
    IndexJob,
    IndexJobKind,
    IndexJobStatus,
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
    WebLink,
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


class SubmissionNotFoundError(Exception):
    pass


class SubmissionNotEditableError(Exception):
    pass


class RejectedTargetNotAllowedError(Exception):
    pass


class AttachmentNotFoundError(Exception):
    pass


class AttachmentNotAllowedError(Exception):
    pass


MAX_ATTACHMENT_TOTAL_BYTES = 100 * 1024 * 1024


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
    parent_revision: ParentRevision | None = None
    parent_lexical_rules: list[ParentLexicalRule] = field(default_factory=list)
    child_revision: ChildRevision | None = None
    child_question_variants: list[ChildRevisionQuestionVariant] = field(default_factory=list)
    child_attachments: list[EvidenceAttachment] = field(default_factory=list)
    child_web_links: list[WebLink] = field(default_factory=list)
    submitter: UserAccount | None = None
    target_reviews: dict[UUID, TargetReviewDetails] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetReviewDetails:
    decision: ReviewDecision
    reviewer: UserAccount


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
    child_attachments: list[EvidenceAttachment]
    child_web_links: list[WebLink]
    review_decision: ReviewDecision | None = None
    reviewer: UserAccount | None = None


@dataclass(frozen=True)
class ManagedKnowledgeDetails:
    publication: ChildKnowledgeBasePublication
    child: Child
    knowledge_base: KnowledgeBase
    parent_name: str
    parent_revision: ParentRevision | None
    child_revision: ChildRevision
    child_question_variants: list[ChildRevisionQuestionVariant]
    child_attachments: list[EvidenceAttachment]
    child_web_links: list[WebLink]
    submitter: UserAccount
    submitted_at: datetime
    embedded_at: datetime | None


@dataclass(frozen=True)
class EditableContentDetails:
    child: Child
    parent_name: str
    parent_revision: ParentRevision | None
    parent_lexical_rules: list[ParentLexicalRule]
    child_revision: ChildRevision
    child_question_variants: list[ChildRevisionQuestionVariant]
    child_attachments: list[EvidenceAttachment]
    child_web_links: list[WebLink]
    knowledge_bases: list[KnowledgeBase]


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


async def _load_child_revision_evidence(
    session: AsyncSession,
    child_revision_ids: set[UUID],
) -> tuple[dict[UUID, list[EvidenceAttachment]], dict[UUID, list[WebLink]]]:
    """Load the ordered, revision-scoped evidence fields in two bounded queries."""

    if not child_revision_ids:
        return {}, {}

    attachments_by_revision: dict[UUID, list[EvidenceAttachment]] = {}
    attachments = await session.scalars(
        select(EvidenceAttachment)
        .where(EvidenceAttachment.child_revision_id.in_(child_revision_ids))
        .order_by(EvidenceAttachment.child_revision_id, EvidenceAttachment.sort_order)
    )
    for attachment in attachments:
        if attachment.child_revision_id is not None:
            attachments_by_revision.setdefault(attachment.child_revision_id, []).append(attachment)

    web_links_by_revision: dict[UUID, list[WebLink]] = {}
    web_links = await session.scalars(
        select(WebLink)
        .where(WebLink.child_revision_id.in_(child_revision_ids))
        .order_by(WebLink.child_revision_id, WebLink.sort_order)
    )
    for web_link in web_links:
        web_links_by_revision.setdefault(web_link.child_revision_id, []).append(web_link)
    return attachments_by_revision, web_links_by_revision


async def _bind_attachments_to_child_revision(
    session: AsyncSession,
    *,
    child_id: UUID,
    child_revision: ChildRevision,
    attachment_ids: list[UUID],
    submitted_by_user_id: UUID,
) -> None:
    """Bind staged uploads or copy published references into a new revision.

    A new revision never mutates evidence already attached to an older revision.
    It receives a fresh metadata row that points at the same object when an
    author retains an existing attachment.
    """

    if not attachment_ids:
        return

    attachments = list(
        (
            await session.scalars(
                select(EvidenceAttachment)
                .where(EvidenceAttachment.id.in_(attachment_ids))
                .with_for_update()
            )
        ).all()
    )
    attachments_by_id = {attachment.id: attachment for attachment in attachments}
    if len(attachments_by_id) != len(attachment_ids):
        raise AttachmentNotFoundError()

    total_bytes = sum(
        attachments_by_id[attachment_id].size_bytes for attachment_id in attachment_ids
    )
    if total_bytes > MAX_ATTACHMENT_TOTAL_BYTES:
        raise AttachmentNotAllowedError()

    bound_attachment_ids = {
        attachment.child_revision_id
        for attachment in attachments
        if attachment.child_revision_id is not None
    }
    source_children: dict[UUID, UUID] = {}
    if bound_attachment_ids:
        source_rows = await session.execute(
            select(ChildRevision.id, ChildRevision.child_id).where(
                ChildRevision.id.in_(bound_attachment_ids)
            )
        )
        source_children = dict(source_rows.all())
    published_source_revision_ids = set(
        (
            await session.scalars(
                select(ChildKnowledgeBasePublication.active_revision_id).where(
                    ChildKnowledgeBasePublication.status == ChildPublicationStatus.PUBLISHED,
                    ChildKnowledgeBasePublication.active_revision_id.in_(bound_attachment_ids),
                )
            )
        ).all()
    )

    for sort_order, attachment_id in enumerate(attachment_ids):
        attachment = attachments_by_id[attachment_id]
        if attachment.child_revision_id is None:
            if attachment.uploaded_by_user_id != submitted_by_user_id:
                raise AttachmentNotAllowedError()
            attachment.child_revision_id = child_revision.id
            attachment.sort_order = sort_order
            continue

        source_revision_id = attachment.child_revision_id
        if source_children.get(source_revision_id) != child_id:
            raise AttachmentNotAllowedError()
        if (
            attachment.uploaded_by_user_id != submitted_by_user_id
            and source_revision_id not in published_source_revision_ids
        ):
            raise AttachmentNotAllowedError()
        session.add(
            EvidenceAttachment(
                child_revision_id=child_revision.id,
                name=attachment.name,
                storage_key=attachment.storage_key,
                content_type=attachment.content_type,
                size_bytes=attachment.size_bytes,
                checksum_sha256=attachment.checksum_sha256,
                uploaded_by_user_id=attachment.uploaded_by_user_id,
                sort_order=sort_order,
            )
        )


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
    await _bind_attachments_to_child_revision(
        session,
        child_id=child_id,
        child_revision=child_revision,
        attachment_ids=content.attachments,
        submitted_by_user_id=created_by_user_id,
    )
    session.add_all(
        WebLink(
            child_revision_id=child_revision.id,
            title=web_link.title,
            url=web_link.url,
            sort_order=index,
        )
        for index, web_link in enumerate(content.web_links)
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


async def _get_locked_resubmission_source(
    session: AsyncSession,
    *,
    review_submission_id: UUID,
    submitted_by_user_id: UUID,
) -> tuple[ReviewSubmission, list[ReviewSubmissionTarget]]:
    submission = await session.scalar(
        select(ReviewSubmission)
        .where(
            ReviewSubmission.id == review_submission_id,
            ReviewSubmission.submitted_by_user_id == submitted_by_user_id,
        )
        .with_for_update()
    )
    if submission is None:
        raise SubmissionNotFoundError(review_submission_id)

    targets = await _locked_submission_targets(session, review_submission_id)
    if not any(target.status == ReviewTargetStatus.REJECTED for target in targets):
        raise SubmissionNotEditableError(review_submission_id)
    return submission, targets


async def resubmit_rejected_parent_aggregate(
    session: AsyncSession,
    *,
    review_submission_id: UUID,
    parent_content: ParentContentInput,
    primary_child_content: ChildContentInput,
    submitted_by_user_id: UUID,
) -> SubmissionDetails:
    source, source_targets = await _get_locked_resubmission_source(
        session,
        review_submission_id=review_submission_id,
        submitted_by_user_id=submitted_by_user_id,
    )
    if source.submission_kind != ReviewSubmissionKind.PARENT_WITH_PRIMARY:
        raise SubmissionNotEditableError(review_submission_id)

    parent = await _get_parent(session, source.parent_id, lock=True)
    primary_child = await session.scalar(
        select(Child)
        .where(
            Child.id == source.child_id,
            Child.parent_id == parent.id,
            Child.is_primary.is_(True),
        )
        .with_for_update()
    )
    if primary_child is None:
        raise ParentNotAvailableError(parent.id)

    await _assert_no_open_parent_aggregate_submission(session, parent.id)
    knowledge_base_ids = [target.knowledge_base_id for target in source_targets]
    knowledge_bases_by_id = await _active_knowledge_bases_by_id(session, knowledge_base_ids)
    publications = await _locked_publications(
        session,
        child_id=primary_child.id,
        knowledge_base_ids=knowledge_base_ids,
    )
    if len(publications) != len(knowledge_base_ids):
        raise ParentNotAvailableError(parent.id)

    parent_revision = await _create_parent_revision(
        session,
        parent_id=parent.id,
        content=parent_content,
        created_by_user_id=submitted_by_user_id,
    )
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
    for publication in publications.values():
        publication.pending_submission_id = submission.id

    return SubmissionDetails(
        submission=submission,
        title=parent_revision.name,
        targets=[(target, knowledge_bases_by_id[target.knowledge_base_id]) for target in targets],
    )


async def resubmit_rejected_child(
    session: AsyncSession,
    *,
    review_submission_id: UUID,
    child_content: ChildContentInput,
    knowledge_base_ids: list[UUID],
    submitted_by_user_id: UUID,
) -> SubmissionDetails:
    source, source_targets = await _get_locked_resubmission_source(
        session,
        review_submission_id=review_submission_id,
        submitted_by_user_id=submitted_by_user_id,
    )
    if source.submission_kind != ReviewSubmissionKind.CHILD:
        raise SubmissionNotEditableError(review_submission_id)

    rejected_target_ids = {
        target.knowledge_base_id
        for target in source_targets
        if target.status == ReviewTargetStatus.REJECTED
    }
    if not set(knowledge_base_ids).issubset(rejected_target_ids):
        raise RejectedTargetNotAllowedError(review_submission_id)

    return await submit_child_revision(
        session,
        child_id=source.child_id,
        child_content=child_content,
        knowledge_base_ids=knowledge_base_ids,
        submitted_by_user_id=submitted_by_user_id,
    )


async def list_submissions_by_author(
    session: AsyncSession,
    submitted_by_user_id: UUID,
) -> list[SubmissionDetails]:
    statement = (
        select(ReviewSubmission, ParentRevision, ChildRevision, UserAccount)
        .outerjoin(ParentRevision, ParentRevision.id == ReviewSubmission.parent_revision_id)
        .join(ChildRevision, ChildRevision.id == ReviewSubmission.child_revision_id)
        .join(UserAccount, UserAccount.id == ReviewSubmission.submitted_by_user_id)
        .where(ReviewSubmission.submitted_by_user_id == submitted_by_user_id)
        .order_by(ReviewSubmission.submitted_at.desc(), ReviewSubmission.id.desc())
    )
    rows = (await session.execute(statement)).all()
    if not rows:
        return []

    submission_ids = [
        submission.id for submission, _parent_revision, _child_revision, _submitter in rows
    ]
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

    parent_revision_ids = {
        parent_revision.id
        for _submission, parent_revision, _child_revision, _submitter in rows
        if parent_revision is not None
    }
    rules_by_revision: dict[UUID, list[ParentLexicalRule]] = {}
    if parent_revision_ids:
        rule_rows = await session.scalars(
            select(ParentLexicalRule)
            .where(ParentLexicalRule.parent_revision_id.in_(parent_revision_ids))
            .order_by(ParentLexicalRule.parent_revision_id, ParentLexicalRule.sort_order)
        )
        for rule in rule_rows:
            rules_by_revision.setdefault(rule.parent_revision_id, []).append(rule)

    child_revision_ids = {
        child_revision.id for _submission, _parent_revision, child_revision, _submitter in rows
    }
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
    attachments_by_revision, web_links_by_revision = await _load_child_revision_evidence(
        session,
        child_revision_ids,
    )

    reviewer = aliased(UserAccount)
    decision_rows = (
        await session.execute(
            select(ReviewDecision, reviewer)
            .join(reviewer, reviewer.id == ReviewDecision.decided_by_user_id)
            .where(ReviewDecision.review_submission_id.in_(submission_ids))
        )
    ).all()
    reviews_by_target = {
        (decision.review_submission_id, decision.knowledge_base_id): TargetReviewDetails(
            decision=decision,
            reviewer=reviewer_account,
        )
        for decision, reviewer_account in decision_rows
    }

    return [
        SubmissionDetails(
            submission=submission,
            title=parent_revision.name if parent_revision is not None else child_revision.question,
            targets=targets_by_submission_id.get(submission.id, []),
            parent_revision=parent_revision,
            parent_lexical_rules=(
                rules_by_revision.get(parent_revision.id, []) if parent_revision is not None else []
            ),
            child_revision=child_revision,
            child_question_variants=variants_by_revision.get(child_revision.id, []),
            child_attachments=attachments_by_revision.get(child_revision.id, []),
            child_web_links=web_links_by_revision.get(child_revision.id, []),
            submitter=submitter,
            target_reviews={
                target.knowledge_base_id: reviews_by_target[
                    (submission.id, target.knowledge_base_id)
                ]
                for target, _knowledge_base in targets_by_submission_id.get(submission.id, [])
                if (submission.id, target.knowledge_base_id) in reviews_by_target
            },
        )
        for submission, parent_revision, child_revision, submitter in rows
    ]


async def _current_published_parent_revisions(
    session: AsyncSession,
    parent_ids: set[UUID],
) -> dict[UUID, ParentRevision]:
    """Resolve the globally active parent revision for each requested parent."""

    revisions: dict[UUID, ParentRevision] = {}
    for parent_id in parent_ids:
        submission = await _latest_published_parent_submission(session, parent_id)
        if submission is None or submission.parent_revision_id is None:
            continue
        revision = await session.get(ParentRevision, submission.parent_revision_id)
        if revision is not None:
            revisions[parent_id] = revision
    return revisions


async def list_managed_knowledge_entries(
    session: AsyncSession,
    *,
    include_archived: bool = True,
) -> list[ManagedKnowledgeDetails]:
    """List every current knowledge publication with its source and index evidence."""

    statuses = [ChildPublicationStatus.PUBLISHED]
    if include_archived:
        statuses.append(ChildPublicationStatus.ARCHIVED)
    rows = (
        await session.execute(
            select(
                ChildKnowledgeBasePublication,
                Child,
                ChildRevision,
                KnowledgeBase,
            )
            .join(Child, Child.id == ChildKnowledgeBasePublication.child_id)
            .join(
                ChildRevision,
                ChildRevision.id == ChildKnowledgeBasePublication.active_revision_id,
            )
            .join(
                KnowledgeBase,
                KnowledgeBase.id == ChildKnowledgeBasePublication.knowledge_base_id,
            )
            .where(ChildKnowledgeBasePublication.status.in_(statuses))
            .order_by(
                KnowledgeBase.name,
                KnowledgeBase.logical_key,
                Child.parent_id,
                Child.is_primary.desc(),
                ChildRevision.question,
            )
        )
    ).all()
    if not rows:
        return []

    child_revision_ids = {child_revision.id for _publication, _child, child_revision, _kb in rows}
    parent_ids = {child.parent_id for _publication, child, _child_revision, _kb in rows}

    submission_rows = (
        await session.execute(
            select(ReviewSubmission, UserAccount)
            .join(UserAccount, UserAccount.id == ReviewSubmission.submitted_by_user_id)
            .where(ReviewSubmission.child_revision_id.in_(child_revision_ids))
            .order_by(ReviewSubmission.submitted_at.desc(), ReviewSubmission.id.desc())
        )
    ).all()
    source_by_revision: dict[UUID, tuple[ReviewSubmission, UserAccount]] = {}
    for submission, submitter in submission_rows:
        source_by_revision.setdefault(submission.child_revision_id, (submission, submitter))

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
    attachments_by_revision, web_links_by_revision = await _load_child_revision_evidence(
        session,
        child_revision_ids,
    )

    embedded_at_by_revision_and_knowledge_base: dict[tuple[UUID, UUID], datetime] = {}
    index_rows = (
        await session.execute(
            select(
                IndexJob.child_revision_id,
                IndexJob.knowledge_base_id,
                func.max(IndexJob.completed_at),
            )
            .where(
                IndexJob.job_kind == IndexJobKind.INDEX_TARGET,
                IndexJob.status == IndexJobStatus.SUCCEEDED,
                IndexJob.child_revision_id.in_(child_revision_ids),
                IndexJob.completed_at.is_not(None),
            )
            .group_by(IndexJob.child_revision_id, IndexJob.knowledge_base_id)
        )
    ).all()
    for child_revision_id, knowledge_base_id, embedded_at in index_rows:
        if (
            child_revision_id is not None
            and knowledge_base_id is not None
            and embedded_at is not None
        ):
            embedded_at_by_revision_and_knowledge_base[(child_revision_id, knowledge_base_id)] = (
                embedded_at
            )

    parent_revisions = await _current_published_parent_revisions(session, parent_ids)
    managed_entries: list[ManagedKnowledgeDetails] = []
    for publication, child, child_revision, knowledge_base in rows:
        source = source_by_revision.get(child_revision.id)
        if source is None:
            # Published revisions are always created through a submission.  Skipping
            # corrupt legacy rows is safer than inventing an uploader or timestamp.
            continue
        submission, submitter = source
        parent_revision = parent_revisions.get(child.parent_id)
        managed_entries.append(
            ManagedKnowledgeDetails(
                publication=publication,
                child=child,
                knowledge_base=knowledge_base,
                parent_name=parent_revision.name if parent_revision is not None else "未命名父类",
                parent_revision=parent_revision,
                child_revision=child_revision,
                child_question_variants=variants_by_revision.get(child_revision.id, []),
                child_attachments=attachments_by_revision.get(child_revision.id, []),
                child_web_links=web_links_by_revision.get(child_revision.id, []),
                submitter=submitter,
                submitted_at=submission.submitted_at,
                embedded_at=embedded_at_by_revision_and_knowledge_base.get(
                    (child_revision.id, knowledge_base.id)
                ),
            )
        )
    return managed_entries


async def list_editable_content_entries(
    session: AsyncSession,
) -> list[EditableContentDetails]:
    """Group published content into the exact revisions that users can revise."""

    managed_entries = [
        entry
        for entry in await list_managed_knowledge_entries(session, include_archived=False)
        if entry.knowledge_base.is_active
    ]
    grouped_entries: dict[tuple[UUID, UUID], list[ManagedKnowledgeDetails]] = {}
    for entry in managed_entries:
        grouped_entries.setdefault(
            (entry.child.id, entry.child_revision.id),
            [],
        ).append(entry)

    primary_parent_revision_ids = {
        entry.parent_revision.id
        for entries in grouped_entries.values()
        for entry in entries
        if entry.child.is_primary and entry.parent_revision is not None
    }
    rules_by_revision: dict[UUID, list[ParentLexicalRule]] = {}
    if primary_parent_revision_ids:
        rule_rows = await session.scalars(
            select(ParentLexicalRule)
            .where(ParentLexicalRule.parent_revision_id.in_(primary_parent_revision_ids))
            .order_by(ParentLexicalRule.parent_revision_id, ParentLexicalRule.sort_order)
        )
        for rule in rule_rows:
            rules_by_revision.setdefault(rule.parent_revision_id, []).append(rule)

    editable_entries: list[EditableContentDetails] = []
    for entries in grouped_entries.values():
        first = entries[0]
        parent_revision = first.parent_revision if first.child.is_primary else None
        editable_entries.append(
            EditableContentDetails(
                child=first.child,
                parent_name=first.parent_name,
                parent_revision=parent_revision,
                parent_lexical_rules=(
                    rules_by_revision.get(parent_revision.id, [])
                    if parent_revision is not None
                    else []
                ),
                child_revision=first.child_revision,
                child_question_variants=first.child_question_variants,
                child_attachments=first.child_attachments,
                child_web_links=first.child_web_links,
                knowledge_bases=[entry.knowledge_base for entry in entries],
            )
        )
    return sorted(
        editable_entries,
        key=lambda entry: (
            entry.parent_name.casefold(),
            not entry.child.is_primary,
            entry.child_revision.question.casefold(),
            str(entry.child.id),
        ),
    )


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
        parent_revision.id
        for _target, _submission, _kb, _user, parent_revision, _child in rows
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
    attachments_by_revision, web_links_by_revision = await _load_child_revision_evidence(
        session,
        child_revision_ids,
    )

    return [
        ReviewQueueDetails(
            submission=submission,
            target=target,
            knowledge_base=knowledge_base,
            submitter=submitter,
            parent_revision=parent_revision,
            parent_lexical_rules=(
                rules_by_revision.get(parent_revision.id, []) if parent_revision is not None else []
            ),
            child_revision=child_revision,
            child_question_variants=variants_by_revision.get(child_revision.id, []),
            child_attachments=attachments_by_revision.get(child_revision.id, []),
            child_web_links=web_links_by_revision.get(child_revision.id, []),
        )
        for target, submission, knowledge_base, submitter, parent_revision, child_revision in rows
    ]


async def list_review_history(
    session: AsyncSession,
    *,
    reviewer_user_id: UUID,
) -> list[ReviewQueueDetails]:
    """Return immutable decisions made by one reviewer, including their source content.

    This deliberately does not depend on the reviewer's *current* assignment. A
    removed assignment must not erase that administrator's historical audit trail.
    """

    submitter_account = aliased(UserAccount)
    reviewer_account = aliased(UserAccount)
    statement = (
        select(
            ReviewDecision,
            ReviewSubmissionTarget,
            ReviewSubmission,
            KnowledgeBase,
            submitter_account,
            reviewer_account,
            ParentRevision,
            ChildRevision,
        )
        .join(
            ReviewSubmissionTarget,
            (ReviewSubmissionTarget.review_submission_id == ReviewDecision.review_submission_id)
            & (ReviewSubmissionTarget.knowledge_base_id == ReviewDecision.knowledge_base_id),
        )
        .join(
            ReviewSubmission,
            ReviewSubmission.id == ReviewDecision.review_submission_id,
        )
        .join(KnowledgeBase, KnowledgeBase.id == ReviewDecision.knowledge_base_id)
        .join(submitter_account, submitter_account.id == ReviewSubmission.submitted_by_user_id)
        .join(reviewer_account, reviewer_account.id == ReviewDecision.decided_by_user_id)
        .outerjoin(ParentRevision, ParentRevision.id == ReviewSubmission.parent_revision_id)
        .join(ChildRevision, ChildRevision.id == ReviewSubmission.child_revision_id)
        .where(ReviewDecision.decided_by_user_id == reviewer_user_id)
        .order_by(ReviewDecision.decided_at.desc(), ReviewDecision.id.desc())
    )
    rows = (await session.execute(statement)).all()
    if not rows:
        return []

    parent_revision_ids = {
        parent_revision.id
        for (
            _decision,
            _target,
            _submission,
            _knowledge_base,
            _submitter,
            _reviewer,
            parent_revision,
            _child_revision,
        ) in rows
        if parent_revision is not None
    }
    rules_by_revision: dict[UUID, list[ParentLexicalRule]] = {}
    if parent_revision_ids:
        rule_rows = await session.scalars(
            select(ParentLexicalRule)
            .where(ParentLexicalRule.parent_revision_id.in_(parent_revision_ids))
            .order_by(ParentLexicalRule.parent_revision_id, ParentLexicalRule.sort_order)
        )
        for rule in rule_rows:
            rules_by_revision.setdefault(rule.parent_revision_id, []).append(rule)

    child_revision_ids = {
        child_revision.id
        for (
            _decision,
            _target,
            _submission,
            _knowledge_base,
            _submitter,
            _reviewer,
            _parent_revision,
            child_revision,
        ) in rows
    }
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
    attachments_by_revision, web_links_by_revision = await _load_child_revision_evidence(
        session,
        child_revision_ids,
    )

    return [
        ReviewQueueDetails(
            submission=submission,
            target=target,
            knowledge_base=knowledge_base,
            submitter=submitter,
            parent_revision=parent_revision,
            parent_lexical_rules=(
                rules_by_revision.get(parent_revision.id, []) if parent_revision is not None else []
            ),
            child_revision=child_revision,
            child_question_variants=variants_by_revision.get(child_revision.id, []),
            child_attachments=attachments_by_revision.get(child_revision.id, []),
            child_web_links=web_links_by_revision.get(child_revision.id, []),
            review_decision=decision,
            reviewer=reviewer,
        )
        for (
            decision,
            target,
            submission,
            knowledge_base,
            submitter,
            reviewer,
            parent_revision,
            child_revision,
        ) in rows
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
