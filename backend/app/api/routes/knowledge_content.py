from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedSession,
    require_csrf,
    require_fully_authenticated_session,
    require_system_administrator,
)
from app.db.session import get_db_session
from app.models.knowledge_content import (
    ChildRevision,
    ChildRevisionQuestionVariant,
    ParentLexicalRule,
    ParentRevision,
)
from app.models.user_account import UserAccount, UserRole
from app.schemas.knowledge_content import (
    AvailableKnowledgeBaseResponse,
    AvailableParentResponse,
    CreateChildRevisionSubmissionRequest,
    CreateChildSubmissionRequest,
    CreateParentRevisionSubmissionRequest,
    CreateParentSubmissionRequest,
    EditableContentEntryResponse,
    ManagedKnowledgeEntryResponse,
    ParentLexicalRuleInput,
    PublicationResponse,
    ReviewChildRevisionResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    ReviewParentRevisionResponse,
    ReviewQueueItemResponse,
    ReviewSubmissionResponse,
    ReviewSubmissionTargetResponse,
    ReviewSubmitterResponse,
)
from app.services.index_jobs import enqueue_clean_publication_job
from app.services.knowledge_content import (
    AvailableParentDetails,
    ChildNotFoundError,
    EditableContentDetails,
    ManagedKnowledgeDetails,
    ParentNotAvailableError,
    ParentNotFoundError,
    PendingSubmissionExistsError,
    PrimaryChildRevisionError,
    RejectedTargetNotAllowedError,
    ReviewAccessDeniedError,
    ReviewDecisionAlreadyExistsError,
    ReviewPublicationNotFoundError,
    ReviewPublicationNotReadyError,
    ReviewQueueDetails,
    ReviewTargetNotFoundError,
    ReviewTargetStateError,
    SubmissionDetails,
    SubmissionNotEditableError,
    SubmissionNotFoundError,
    TargetKnowledgeBaseNotAllowedError,
    TargetKnowledgeBaseUnavailableError,
    archive_publication,
    decide_review_target,
    get_publication,
    list_available_parents,
    list_editable_content_entries,
    list_managed_knowledge_entries,
    list_review_history,
    list_review_queue,
    list_submissions_by_author,
    resubmit_rejected_child,
    resubmit_rejected_parent_aggregate,
    submit_child_revision,
    submit_new_child,
    submit_new_parent_aggregate,
    submit_parent_aggregate_revision,
)
from app.services.users import record_audit_event

router = APIRouter(prefix="/knowledge-content", tags=["knowledge content"])


def as_available_parent_response(details: AvailableParentDetails) -> AvailableParentResponse:
    return AvailableParentResponse(
        id=details.parent.id,
        name=details.parent_revision.name,
        canonical_keyword=details.parent_revision.canonical_keyword,
        primary_child_id=details.primary_child.id,
        available_knowledge_bases=[
            AvailableKnowledgeBaseResponse(
                id=knowledge_base.id,
                logical_key=knowledge_base.logical_key,
                name=knowledge_base.name,
            )
            for knowledge_base in details.knowledge_bases
        ],
    )


def as_user_response(user: UserAccount) -> ReviewSubmitterResponse:
    return ReviewSubmitterResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
    )


def as_parent_revision_response(
    parent_revision: ParentRevision | None,
    lexical_rules: list[ParentLexicalRule],
) -> ReviewParentRevisionResponse | None:
    if parent_revision is None:
        return None
    return ReviewParentRevisionResponse(
        id=parent_revision.id,
        revision_number=parent_revision.revision_number,
        name=parent_revision.name,
        canonical_keyword=parent_revision.canonical_keyword,
        lexical_rules=[
            ParentLexicalRuleInput(
                rule_type=rule.rule_type,
                rule_value=rule.rule_value,
            )
            for rule in lexical_rules
        ],
    )


def as_child_revision_response(
    child_revision: ChildRevision,
    question_variants: list[ChildRevisionQuestionVariant],
) -> ReviewChildRevisionResponse:
    return ReviewChildRevisionResponse(
        id=child_revision.id,
        revision_number=child_revision.revision_number,
        question=child_revision.question,
        response_content=child_revision.response_content,
        question_variants=[variant.question_text for variant in question_variants],
        follow_up_guidance=child_revision.follow_up_guidance,
        question_type=child_revision.question_type,
        business_object=child_revision.business_object,
        purpose=child_revision.purpose,
        customer_type=child_revision.customer_type,
        feature_explanation=child_revision.feature_explanation,
        example=child_revision.example,
        internal_notes=child_revision.internal_notes,
    )


def as_submission_response(
    details: SubmissionDetails,
    *,
    submitter: UserAccount | None = None,
) -> ReviewSubmissionResponse:
    submission = details.submission
    author = details.submitter or submitter
    if author is None:
        raise RuntimeError("submission response requires its submitter")
    return ReviewSubmissionResponse(
        id=submission.id,
        submission_kind=submission.submission_kind,
        status=submission.status,
        parent_id=submission.parent_id,
        parent_revision_id=submission.parent_revision_id,
        child_id=submission.child_id,
        child_revision_id=submission.child_revision_id,
        title=details.title,
        submitter=as_user_response(author),
        targets=[
            ReviewSubmissionTargetResponse(
                id=knowledge_base.id,
                logical_key=knowledge_base.logical_key,
                name=knowledge_base.name,
                status=target.status,
                review_comment=(
                    details.target_reviews[target.knowledge_base_id].decision.comment
                    if target.knowledge_base_id in details.target_reviews
                    else None
                ),
                reviewer=(
                    as_user_response(details.target_reviews[target.knowledge_base_id].reviewer)
                    if target.knowledge_base_id in details.target_reviews
                    else None
                ),
                reviewed_at=(
                    details.target_reviews[target.knowledge_base_id].decision.decided_at
                    if target.knowledge_base_id in details.target_reviews
                    else None
                ),
                review_decision=(
                    details.target_reviews[target.knowledge_base_id].decision.decision
                    if target.knowledge_base_id in details.target_reviews
                    else None
                ),
            )
            for target, knowledge_base in details.targets
        ],
        submitted_at=submission.submitted_at,
        parent_revision=as_parent_revision_response(
            details.parent_revision,
            details.parent_lexical_rules,
        ),
        child_revision=(
            as_child_revision_response(
                details.child_revision,
                details.child_question_variants,
            )
            if details.child_revision is not None
            else None
        ),
    )


def as_content_http_error(error: Exception) -> HTTPException:
    if isinstance(error, ParentNotFoundError | ChildNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="内容不存在")
    if isinstance(error, ParentNotAvailableError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="父类尚未完成可用审核，暂不能创建或修订普通子条目",
        )
    if isinstance(error, TargetKnowledgeBaseUnavailableError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="目标知识库不存在或未启用",
        )
    if isinstance(error, TargetKnowledgeBaseNotAllowedError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="普通子条目的目标知识库必须属于父类主子条目的已发布知识库",
        )
    if isinstance(error, PrimaryChildRevisionError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="主子条目必须随父类一起提交新修订",
        )
    if isinstance(error, PendingSubmissionExistsError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="目标内容已有未结束的候选提交，请等待审核、发布或驳回",
        )
    if isinstance(error, SubmissionNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="投稿不存在")
    if isinstance(error, SubmissionNotEditableError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有包含被驳回目标的投稿才能重新提交审核",
        )
    if isinstance(error, RejectedTargetNotAllowedError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="重新提交普通子条目时只能选择原投稿中被驳回的知识库",
        )
    if isinstance(error, ReviewAccessDeniedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="没有该知识库的审核权限")
    if isinstance(error, ReviewTargetNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="审核目标不存在")
    if isinstance(error, ReviewDecisionAlreadyExistsError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该审核目标已经作出决定")
    if isinstance(error, ReviewTargetStateError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="审核目标当前状态不允许此操作",
        )
    if isinstance(error, ReviewPublicationNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="发布关系不存在")
    if isinstance(error, ReviewPublicationNotReadyError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="候选尚未完成审核或索引")
    raise error


def _require_review_actor(user: AuthenticatedSession) -> None:
    if user.user.role not in {UserRole.REVIEW_ADMIN, UserRole.SYSTEM_ADMIN}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要审核权限")


def as_review_queue_response(details: ReviewQueueDetails) -> ReviewQueueItemResponse:
    return ReviewQueueItemResponse(
        id=details.submission.id,
        review_submission_id=details.submission.id,
        submission_kind=details.submission.submission_kind,
        submission_status=details.submission.status,
        target_status=details.target.status,
        parent_id=details.submission.parent_id,
        parent_revision_id=details.submission.parent_revision_id,
        child_id=details.submission.child_id,
        child_revision_id=details.submission.child_revision_id,
        knowledge_base_id=details.knowledge_base.id,
        knowledge_base=AvailableKnowledgeBaseResponse(
            id=details.knowledge_base.id,
            logical_key=details.knowledge_base.logical_key,
            name=details.knowledge_base.name,
        ),
        submitter=as_user_response(details.submitter),
        reviewer=(as_user_response(details.reviewer) if details.reviewer is not None else None),
        review_decision=(
            details.review_decision.decision if details.review_decision is not None else None
        ),
        review_comment=(
            details.review_decision.comment if details.review_decision is not None else None
        ),
        parent_revision=as_parent_revision_response(
            details.parent_revision,
            details.parent_lexical_rules,
        ),
        child_revision=as_child_revision_response(
            details.child_revision,
            details.child_question_variants,
        ),
        submitted_at=details.submission.submitted_at,
        reviewed_at=(
            details.review_decision.decided_at if details.review_decision is not None else None
        ),
    )


def as_managed_knowledge_response(
    details: ManagedKnowledgeDetails,
) -> ManagedKnowledgeEntryResponse:
    return ManagedKnowledgeEntryResponse(
        child_id=details.child.id,
        parent_id=details.child.parent_id,
        parent_name=details.parent_name,
        is_primary=details.child.is_primary,
        knowledge_base=AvailableKnowledgeBaseResponse(
            id=details.knowledge_base.id,
            logical_key=details.knowledge_base.logical_key,
            name=details.knowledge_base.name,
        ),
        status=details.publication.status,
        child_revision=as_child_revision_response(
            details.child_revision,
            details.child_question_variants,
        ),
        uploaded_by=as_user_response(details.submitter),
        uploaded_at=details.submitted_at,
        embedded_at=details.embedded_at,
        archived_at=details.publication.archived_at,
    )


def as_editable_content_response(details: EditableContentDetails) -> EditableContentEntryResponse:
    return EditableContentEntryResponse(
        child_id=details.child.id,
        parent_id=details.child.parent_id,
        parent_name=details.parent_name,
        is_primary=details.child.is_primary,
        knowledge_bases=[
            AvailableKnowledgeBaseResponse(
                id=knowledge_base.id,
                logical_key=knowledge_base.logical_key,
                name=knowledge_base.name,
            )
            for knowledge_base in details.knowledge_bases
        ],
        parent_revision=as_parent_revision_response(
            details.parent_revision,
            details.parent_lexical_rules,
        ),
        child_revision=as_child_revision_response(
            details.child_revision,
            details.child_question_variants,
        ),
    )


@router.get("/parents/available", response_model=list[AvailableParentResponse])
async def list_selectable_parents(
    _user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[AvailableParentResponse]:
    return [
        as_available_parent_response(parent)
        for parent in await list_available_parents(session)
    ]


@router.get("/submissions/mine", response_model=list[ReviewSubmissionResponse])
async def list_my_content_submissions(
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ReviewSubmissionResponse]:
    submissions = await list_submissions_by_author(session, user.user.id)
    return [as_submission_response(submission) for submission in submissions]


@router.get("/entries/editable", response_model=list[EditableContentEntryResponse])
async def list_currently_editable_content(
    _user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[EditableContentEntryResponse]:
    return [
        as_editable_content_response(entry)
        for entry in await list_editable_content_entries(session)
    ]


@router.get("/admin/knowledge", response_model=list[ManagedKnowledgeEntryResponse])
async def list_all_managed_knowledge(
    _actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ManagedKnowledgeEntryResponse]:
    return [
        as_managed_knowledge_response(entry)
        for entry in await list_managed_knowledge_entries(session)
    ]


@router.post(
    "/parent-submissions",
    response_model=ReviewSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_parent_aggregate_submission(
    body: CreateParentSubmissionRequest,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReviewSubmissionResponse:
    try:
        submission = await submit_new_parent_aggregate(
            session,
            parent_content=body.parent,
            primary_child_content=body.primary_child,
            knowledge_base_ids=body.knowledge_base_ids,
            submitted_by_user_id=user.user.id,
        )
    except (TargetKnowledgeBaseUnavailableError, TargetKnowledgeBaseNotAllowedError) as exc:
        raise as_content_http_error(exc) from exc

    record_audit_event(
        session,
        event_type="content.parent_aggregate_submitted",
        actor_user_id=user.user.id,
        target_type="parent",
        target_id=submission.submission.parent_id,
        payload={
            "review_submission_id": str(submission.submission.id),
            "knowledge_base_ids": [
                str(target.knowledge_base_id) for target, _ in submission.targets
            ],
        },
    )
    await session.commit()
    return as_submission_response(submission, submitter=user.user)


@router.post(
    "/parents/{parent_id}/revisions",
    response_model=ReviewSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_parent_aggregate_revision_submission(
    parent_id: UUID,
    body: CreateParentRevisionSubmissionRequest,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReviewSubmissionResponse:
    try:
        submission = await submit_parent_aggregate_revision(
            session,
            parent_id=parent_id,
            parent_content=body.parent,
            primary_child_content=body.primary_child,
            submitted_by_user_id=user.user.id,
        )
    except (
        ParentNotFoundError,
        ParentNotAvailableError,
        PendingSubmissionExistsError,
    ) as exc:
        raise as_content_http_error(exc) from exc

    record_audit_event(
        session,
        event_type="content.parent_aggregate_revision_submitted",
        actor_user_id=user.user.id,
        target_type="parent",
        target_id=submission.submission.parent_id,
        payload={"review_submission_id": str(submission.submission.id)},
    )
    await session.commit()
    return as_submission_response(submission, submitter=user.user)


@router.post(
    "/child-submissions",
    response_model=ReviewSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_child_submission(
    body: CreateChildSubmissionRequest,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReviewSubmissionResponse:
    try:
        submission = await submit_new_child(
            session,
            parent_id=body.parent_id,
            child_content=body.child,
            knowledge_base_ids=body.knowledge_base_ids,
            submitted_by_user_id=user.user.id,
        )
    except (
        ParentNotFoundError,
        ParentNotAvailableError,
        TargetKnowledgeBaseNotAllowedError,
    ) as exc:
        raise as_content_http_error(exc) from exc

    record_audit_event(
        session,
        event_type="content.child_submitted",
        actor_user_id=user.user.id,
        target_type="child",
        target_id=submission.submission.child_id,
        payload={
            "review_submission_id": str(submission.submission.id),
            "parent_id": str(submission.submission.parent_id),
            "knowledge_base_ids": [
                str(target.knowledge_base_id) for target, _ in submission.targets
            ],
        },
    )
    await session.commit()
    return as_submission_response(submission, submitter=user.user)


@router.post(
    "/children/{child_id}/revisions",
    response_model=ReviewSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_child_revision_submission(
    child_id: UUID,
    body: CreateChildRevisionSubmissionRequest,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReviewSubmissionResponse:
    try:
        submission = await submit_child_revision(
            session,
            child_id=child_id,
            child_content=body.child,
            knowledge_base_ids=body.knowledge_base_ids,
            submitted_by_user_id=user.user.id,
        )
    except (
        ChildNotFoundError,
        ParentNotAvailableError,
        PrimaryChildRevisionError,
        TargetKnowledgeBaseNotAllowedError,
        PendingSubmissionExistsError,
    ) as exc:
        raise as_content_http_error(exc) from exc

    record_audit_event(
        session,
        event_type="content.child_revision_submitted",
        actor_user_id=user.user.id,
        target_type="child",
        target_id=submission.submission.child_id,
        payload={
            "review_submission_id": str(submission.submission.id),
            "knowledge_base_ids": [
                str(target.knowledge_base_id) for target, _ in submission.targets
            ],
        },
    )
    await session.commit()
    return as_submission_response(submission, submitter=user.user)


@router.post(
    "/review-submissions/{review_submission_id}/resubmit-parent",
    response_model=ReviewSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def resubmit_rejected_parent_submission(
    review_submission_id: UUID,
    body: CreateParentRevisionSubmissionRequest,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReviewSubmissionResponse:
    try:
        submission = await resubmit_rejected_parent_aggregate(
            session,
            review_submission_id=review_submission_id,
            parent_content=body.parent,
            primary_child_content=body.primary_child,
            submitted_by_user_id=user.user.id,
        )
    except (
        ParentNotFoundError,
        ParentNotAvailableError,
        PendingSubmissionExistsError,
        SubmissionNotFoundError,
        SubmissionNotEditableError,
        TargetKnowledgeBaseUnavailableError,
    ) as exc:
        raise as_content_http_error(exc) from exc

    record_audit_event(
        session,
        event_type="content.rejected_parent_resubmitted",
        actor_user_id=user.user.id,
        target_type="parent",
        target_id=submission.submission.parent_id,
        payload={
            "source_review_submission_id": str(review_submission_id),
            "review_submission_id": str(submission.submission.id),
        },
    )
    await session.commit()
    return as_submission_response(submission, submitter=user.user)


@router.post(
    "/review-submissions/{review_submission_id}/resubmit-child",
    response_model=ReviewSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def resubmit_rejected_child_submission(
    review_submission_id: UUID,
    body: CreateChildRevisionSubmissionRequest,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReviewSubmissionResponse:
    try:
        submission = await resubmit_rejected_child(
            session,
            review_submission_id=review_submission_id,
            child_content=body.child,
            knowledge_base_ids=body.knowledge_base_ids,
            submitted_by_user_id=user.user.id,
        )
    except (
        ChildNotFoundError,
        ParentNotAvailableError,
        PendingSubmissionExistsError,
        PrimaryChildRevisionError,
        RejectedTargetNotAllowedError,
        SubmissionNotFoundError,
        SubmissionNotEditableError,
        TargetKnowledgeBaseNotAllowedError,
    ) as exc:
        raise as_content_http_error(exc) from exc

    record_audit_event(
        session,
        event_type="content.rejected_child_resubmitted",
        actor_user_id=user.user.id,
        target_type="child",
        target_id=submission.submission.child_id,
        payload={
            "source_review_submission_id": str(review_submission_id),
            "review_submission_id": str(submission.submission.id),
            "knowledge_base_ids": [
                str(target.knowledge_base_id) for target, _ in submission.targets
            ],
        },
    )
    await session.commit()
    return as_submission_response(submission, submitter=user.user)


@router.get(
    "/review-queue",
    response_model=list[ReviewQueueItemResponse],
)
@router.get(
    "/reviews/queue",
    response_model=list[ReviewQueueItemResponse],
    include_in_schema=False,
)
async def list_content_review_queue(
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    knowledge_base_id: UUID | None = None,
) -> list[ReviewQueueItemResponse]:
    _require_review_actor(user)
    try:
        queue = await list_review_queue(
            session,
            actor_user_id=user.user.id,
            actor_role=user.user.role,
            knowledge_base_id=knowledge_base_id,
        )
    except ReviewAccessDeniedError as exc:
        raise as_content_http_error(exc) from exc
    return [as_review_queue_response(item) for item in queue]


@router.get(
    "/review-history",
    response_model=list[ReviewQueueItemResponse],
)
@router.get(
    "/reviews/history",
    response_model=list[ReviewQueueItemResponse],
    include_in_schema=False,
)
async def list_current_reviewer_history(
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ReviewQueueItemResponse]:
    _require_review_actor(user)
    history = await list_review_history(session, reviewer_user_id=user.user.id)
    return [as_review_queue_response(item) for item in history]


@router.post(
    "/review-submissions/{review_submission_id}/targets/{knowledge_base_id}/decision",
    response_model=ReviewDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/review-queue/{review_submission_id}/targets/{knowledge_base_id}/decision",
    response_model=ReviewDecisionResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def decide_content_review_target(
    review_submission_id: UUID,
    knowledge_base_id: UUID,
    body: ReviewDecisionRequest,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReviewDecisionResponse:
    _require_review_actor(user)
    try:
        decision = await decide_review_target(
            session,
            review_submission_id=review_submission_id,
            knowledge_base_id=knowledge_base_id,
            actor_user_id=user.user.id,
            actor_role=user.user.role,
            decision=body.decision,
            comment=body.comment,
        )
    except (
        ReviewAccessDeniedError,
        ReviewTargetNotFoundError,
        ReviewDecisionAlreadyExistsError,
        ReviewTargetStateError,
    ) as exc:
        raise as_content_http_error(exc) from exc

    record_audit_event(
        session,
        event_type="content.review_decision_recorded",
        actor_user_id=user.user.id,
        target_type="review_submission_target",
        target_id=review_submission_id,
        payload={
            "review_submission_id": str(review_submission_id),
            "knowledge_base_id": str(knowledge_base_id),
            "decision": decision.decision.value,
            "comment": body.comment,
        },
    )
    await session.commit()
    return ReviewDecisionResponse(
        id=decision.id,
        review_submission_id=decision.review_submission_id,
        knowledge_base_id=decision.knowledge_base_id,
        decision=decision.decision,
        comment=decision.comment,
        decided_by_user_id=decision.decided_by_user_id,
        decided_at=decision.decided_at,
    )


@router.get(
    "/children/{child_id}/publications/{knowledge_base_id}",
    response_model=PublicationResponse,
)
async def read_child_publication(
    child_id: UUID,
    knowledge_base_id: UUID,
    _user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicationResponse:
    publication = await get_publication(
        session,
        child_id=child_id,
        knowledge_base_id=knowledge_base_id,
    )
    if publication is None:
        raise as_content_http_error(ReviewPublicationNotFoundError(child_id, knowledge_base_id))
    return PublicationResponse(
        child_id=publication.child_id,
        knowledge_base_id=publication.knowledge_base_id,
        status=publication.status.value,
        active_revision_id=publication.active_revision_id,
        pending_submission_id=publication.pending_submission_id,
        archived_at=publication.archived_at,
    )


@router.post(
    "/children/{child_id}/publications/{knowledge_base_id}/archive",
    response_model=PublicationResponse,
)
async def archive_child_publication(
    child_id: UUID,
    knowledge_base_id: UUID,
    user: Annotated[AuthenticatedSession, Depends(require_fully_authenticated_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicationResponse:
    _require_review_actor(user)
    try:
        publication = await archive_publication(
            session,
            child_id=child_id,
            knowledge_base_id=knowledge_base_id,
            actor_user_id=user.user.id,
            actor_role=user.user.role,
        )
    except (
        ReviewAccessDeniedError,
        ReviewPublicationNotFoundError,
        PendingSubmissionExistsError,
        ReviewTargetStateError,
    ) as exc:
        raise as_content_http_error(exc) from exc
    cleanup_job = await enqueue_clean_publication_job(
        session,
        child_id=publication.child_id,
        knowledge_base_id=publication.knowledge_base_id,
    )
    record_audit_event(
        session,
        event_type="content.publication_archived",
        actor_user_id=user.user.id,
        target_type="child_knowledge_base_publication",
        target_id=child_id,
        payload={
            "knowledge_base_id": str(knowledge_base_id),
            "cleanup_index_job_id": str(cleanup_job.id),
        },
    )
    await session.commit()
    return PublicationResponse(
        child_id=publication.child_id,
        knowledge_base_id=publication.knowledge_base_id,
        status=publication.status.value,
        active_revision_id=publication.active_revision_id,
        pending_submission_id=publication.pending_submission_id,
        archived_at=publication.archived_at,
    )


@router.delete(
    "/admin/knowledge/{child_id}/knowledge-bases/{knowledge_base_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_managed_knowledge(
    child_id: UUID,
    knowledge_base_id: UUID,
    actor: Annotated[AuthenticatedSession, Depends(require_system_administrator)],
    _csrf: Annotated[None, Depends(require_csrf)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """Remove a knowledge publication from retrieval and enqueue vector cleanup.

    The durable revision and its audit evidence remain intact; "delete" is an
    archival operation so an administrator can still trace what was removed.
    """

    try:
        publication = await archive_publication(
            session,
            child_id=child_id,
            knowledge_base_id=knowledge_base_id,
            actor_user_id=actor.user.id,
            actor_role=actor.user.role,
        )
    except (
        ReviewAccessDeniedError,
        ReviewPublicationNotFoundError,
        PendingSubmissionExistsError,
        ReviewTargetStateError,
    ) as exc:
        raise as_content_http_error(exc) from exc

    cleanup_job = await enqueue_clean_publication_job(
        session,
        child_id=publication.child_id,
        knowledge_base_id=publication.knowledge_base_id,
    )
    record_audit_event(
        session,
        event_type="content.managed_knowledge_deleted",
        actor_user_id=actor.user.id,
        target_type="child_knowledge_base_publication",
        target_id=child_id,
        payload={
            "knowledge_base_id": str(knowledge_base_id),
            "cleanup_index_job_id": str(cleanup_job.id),
        },
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
