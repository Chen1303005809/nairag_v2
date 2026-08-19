from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthenticatedSession,
    require_csrf,
    require_fully_authenticated_session,
)
from app.db.session import get_db_session
from app.models.user_account import UserRole
from app.schemas.knowledge_content import (
    AvailableKnowledgeBaseResponse,
    AvailableParentResponse,
    CreateChildRevisionSubmissionRequest,
    CreateChildSubmissionRequest,
    CreateParentRevisionSubmissionRequest,
    CreateParentSubmissionRequest,
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
from app.services.knowledge_content import (
    AvailableParentDetails,
    ChildNotFoundError,
    ParentNotAvailableError,
    ParentNotFoundError,
    PendingSubmissionExistsError,
    PrimaryChildRevisionError,
    ReviewAccessDeniedError,
    ReviewDecisionAlreadyExistsError,
    ReviewPublicationNotFoundError,
    ReviewPublicationNotReadyError,
    ReviewQueueDetails,
    ReviewTargetNotFoundError,
    ReviewTargetStateError,
    SubmissionDetails,
    TargetKnowledgeBaseNotAllowedError,
    TargetKnowledgeBaseUnavailableError,
    archive_publication,
    decide_review_target,
    get_publication,
    list_available_parents,
    list_review_queue,
    list_submissions_by_author,
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


def as_submission_response(details: SubmissionDetails) -> ReviewSubmissionResponse:
    submission = details.submission
    return ReviewSubmissionResponse(
        id=submission.id,
        submission_kind=submission.submission_kind,
        status=submission.status,
        parent_id=submission.parent_id,
        parent_revision_id=submission.parent_revision_id,
        child_id=submission.child_id,
        child_revision_id=submission.child_revision_id,
        title=details.title,
        targets=[
            ReviewSubmissionTargetResponse(
                id=knowledge_base.id,
                logical_key=knowledge_base.logical_key,
                name=knowledge_base.name,
                status=target.status,
            )
            for target, knowledge_base in details.targets
        ],
        submitted_at=submission.submitted_at,
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
    parent_revision = details.parent_revision
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
        submitter=ReviewSubmitterResponse(
            id=details.submitter.id,
            username=details.submitter.username,
            display_name=details.submitter.display_name,
        ),
        parent_revision=(
            ReviewParentRevisionResponse(
                id=parent_revision.id,
                revision_number=parent_revision.revision_number,
                name=parent_revision.name,
                canonical_keyword=parent_revision.canonical_keyword,
                lexical_rules=[
                    ParentLexicalRuleInput(
                        rule_type=rule.rule_type,
                        rule_value=rule.rule_value,
                    )
                    for rule in details.parent_lexical_rules
                ],
            )
            if parent_revision is not None
            else None
        ),
        child_revision=ReviewChildRevisionResponse(
            id=details.child_revision.id,
            revision_number=details.child_revision.revision_number,
            question=details.child_revision.question,
            response_content=details.child_revision.response_content,
            question_variants=[
                variant.question_text for variant in details.child_question_variants
            ],
            follow_up_guidance=details.child_revision.follow_up_guidance,
            question_type=details.child_revision.question_type,
            business_object=details.child_revision.business_object,
            purpose=details.child_revision.purpose,
            customer_type=details.child_revision.customer_type,
            feature_explanation=details.child_revision.feature_explanation,
            example=details.child_revision.example,
            internal_notes=details.child_revision.internal_notes,
        ),
        submitted_at=details.submission.submitted_at,
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
    return as_submission_response(submission)


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
    return as_submission_response(submission)


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
    return as_submission_response(submission)


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
    return as_submission_response(submission)


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
    record_audit_event(
        session,
        event_type="content.publication_archived",
        actor_user_id=user.user.id,
        target_type="child_knowledge_base_publication",
        target_id=child_id,
        payload={"knowledge_base_id": str(knowledge_base_id)},
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
