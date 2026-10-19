from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # Register model metadata.
from app.db.base import Base
from app.models.knowledge_base import KnowledgeBase, ReviewerKnowledgeBase
from app.models.knowledge_content import (
    Child,
    ChildKnowledgeBasePublication,
    ChildPublicationStatus,
    ChildRevision,
    Parent,
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
from app.services.knowledge_content import (
    ContentRevisionAccessDeniedError,
    list_editable_content_entries,
    submit_child_revision,
    submit_parent_aggregate_revision,
)


async def _create_user(session: AsyncSession, role: UserRole, name: str) -> UserAccount:
    user = UserAccount(
        username=f"{name}-{uuid4().hex[:10]}",
        display_name=name,
        password_hash="x" * 32,
        role=role,
        must_change_password=False,
    )
    session.add(user)
    await session.flush()
    return user


async def _create_published_child(
    session: AsyncSession,
    *,
    parent_id,
    owner: UserAccount,
    knowledge_bases: list[KnowledgeBase],
    question: str,
    is_primary: bool,
    parent_revision_id=None,
) -> tuple[Child, ChildRevision, ReviewSubmission]:
    child = Child(
        parent_id=parent_id,
        is_primary=is_primary,
        created_by_user_id=owner.id,
    )
    session.add(child)
    await session.flush()
    revision = ChildRevision(
        child_id=child.id,
        revision_number=1,
        question=question,
        response_content="请联系管理员。",
        created_by_user_id=owner.id,
    )
    session.add(revision)
    await session.flush()
    submission = ReviewSubmission(
        submission_kind=(
            ReviewSubmissionKind.PARENT_WITH_PRIMARY if is_primary else ReviewSubmissionKind.CHILD
        ),
        status=ReviewSubmissionStatus.PUBLISHED,
        parent_id=parent_id,
        parent_revision_id=parent_revision_id,
        child_id=child.id,
        child_revision_id=revision.id,
        submitted_by_user_id=owner.id,
    )
    session.add(submission)
    await session.flush()
    for knowledge_base in knowledge_bases:
        target = ReviewSubmissionTarget(
            review_submission_id=submission.id,
            knowledge_base_id=knowledge_base.id,
            status=ReviewTargetStatus.PUBLISHED,
        )
        publication = ChildKnowledgeBasePublication(
            child_id=child.id,
            knowledge_base_id=knowledge_base.id,
            status=ChildPublicationStatus.PUBLISHED,
            active_revision_id=revision.id,
        )
        session.add_all(
            [
                target,
                publication,
                ReviewDecision(
                    review_submission_id=submission.id,
                    knowledge_base_id=knowledge_base.id,
                    decision=ReviewDecisionKind.APPROVED,
                    decided_by_user_id=owner.id,
                ),
            ]
        )
    await session.flush()
    return child, revision, submission


@pytest.mark.asyncio
async def test_editable_entries_and_revision_permissions_follow_stable_owners(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'revision-permissions.sqlite3'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            owner = await _create_user(session, UserRole.NORMAL_USER, "知识创建人")
            another_user = await _create_user(session, UserRole.NORMAL_USER, "其他人员")
            reviewer = await _create_user(session, UserRole.REVIEW_ADMIN, "审查管理员")
            system_admin = await _create_user(session, UserRole.SYSTEM_ADMIN, "系统管理员")
            knowledge_bases = [
                KnowledgeBase(
                    logical_key=f"kb-{uuid4().hex[:10]}",
                    name=f"知识库 {index + 1}",
                    current_physical_collection_name=f"collection-{uuid4().hex}",
                    created_by_user_id=owner.id,
                )
                for index in range(2)
            ]
            session.add_all(knowledge_bases)
            await session.flush()
            parent = Parent(created_by_user_id=owner.id)
            session.add(parent)
            await session.flush()
            parent_revision = ParentRevision(
                parent_id=parent.id,
                revision_number=1,
                name="账号问题",
                canonical_keyword="账号",
                created_by_user_id=owner.id,
            )
            session.add(parent_revision)
            await session.flush()
            primary, _primary_revision, _primary_submission = await _create_published_child(
                session,
                parent_id=parent.id,
                owner=owner,
                knowledge_bases=knowledge_bases,
                question="账号如何登录？",
                is_primary=True,
                parent_revision_id=parent_revision.id,
            )
            owned_child, _owned_revision, _owned_submission = await _create_published_child(
                session,
                parent_id=parent.id,
                owner=owner,
                knowledge_bases=knowledge_bases,
                question="自己的子条目如何处理？",
                is_primary=False,
            )
            admin_child, _admin_revision, _admin_submission = await _create_published_child(
                session,
                parent_id=parent.id,
                owner=owner,
                knowledge_bases=knowledge_bases,
                question="管理员可修订的子条目？",
                is_primary=False,
            )
            session.add(
                ReviewerKnowledgeBase(
                    reviewer_user_id=reviewer.id,
                    knowledge_base_id=knowledge_bases[0].id,
                    assigned_by_user_id=system_admin.id,
                )
            )
            await session.commit()

            normal_entries = await list_editable_content_entries(
                session,
                actor_user_id=owner.id,
                actor_role=UserRole.NORMAL_USER,
            )
            assert {entry.child.id for entry in normal_entries} == {
                primary.id,
                owned_child.id,
                admin_child.id,
            }
            assert all(entry.owner.id == owner.id for entry in normal_entries)
            assert (
                await list_editable_content_entries(
                    session,
                    actor_user_id=another_user.id,
                    actor_role=UserRole.NORMAL_USER,
                )
                == []
            )

            reviewer_entries = await list_editable_content_entries(
                session,
                actor_user_id=reviewer.id,
                actor_role=UserRole.REVIEW_ADMIN,
            )
            assert {entry.child.id for entry in reviewer_entries} == {
                primary.id,
                owned_child.id,
                admin_child.id,
            }
            assert all(entry.owner.id == owner.id for entry in reviewer_entries)
            primary_entry = next(entry for entry in reviewer_entries if entry.child.is_primary)
            assert {knowledge_base.id for knowledge_base in primary_entry.knowledge_bases} == {
                item.id for item in knowledge_bases
            }
            ordinary_entry = next(
                entry for entry in reviewer_entries if entry.child.id == admin_child.id
            )
            assert [knowledge_base.id for knowledge_base in ordinary_entry.knowledge_bases] == [
                knowledge_bases[0].id
            ]
            system_entries = await list_editable_content_entries(
                session,
                actor_user_id=system_admin.id,
                actor_role=UserRole.SYSTEM_ADMIN,
            )
            assert {entry.child.id for entry in system_entries} == {
                primary.id,
                owned_child.id,
                admin_child.id,
            }

            child_revision_input = ChildContentInput(
                question="修订后的问题？",
                response_content="修订后的回答。",
            )
            with pytest.raises(ContentRevisionAccessDeniedError):
                await submit_parent_aggregate_revision(
                    session,
                    parent_id=parent.id,
                    parent_content=ParentContentInput(
                        name="账号问题",
                        canonical_keyword="账号",
                    ),
                    primary_child_content=child_revision_input,
                    submitted_by_user_id=another_user.id,
                    actor_role=UserRole.NORMAL_USER,
                )
            with pytest.raises(ContentRevisionAccessDeniedError):
                await submit_child_revision(
                    session,
                    child_id=admin_child.id,
                    child_content=child_revision_input,
                    knowledge_base_ids=[knowledge_bases[1].id],
                    submitted_by_user_id=reviewer.id,
                    actor_role=UserRole.REVIEW_ADMIN,
                )

            own_revision = await submit_child_revision(
                session,
                child_id=owned_child.id,
                child_content=child_revision_input,
                knowledge_base_ids=[knowledge_bases[0].id],
                submitted_by_user_id=owner.id,
                actor_role=UserRole.NORMAL_USER,
            )
            assert own_revision.submission.submitted_by_user_id == owner.id
            reviewer_revision = await submit_child_revision(
                session,
                child_id=admin_child.id,
                child_content=child_revision_input,
                knowledge_base_ids=[knowledge_bases[0].id],
                submitted_by_user_id=reviewer.id,
                actor_role=UserRole.REVIEW_ADMIN,
            )
            assert [target.knowledge_base_id for target, _kb in reviewer_revision.targets] == [
                knowledge_bases[0].id
            ]
            parent_revision_submission = await submit_parent_aggregate_revision(
                session,
                parent_id=parent.id,
                parent_content=ParentContentInput(
                    name="账号问题新修订",
                    canonical_keyword="账号",
                ),
                primary_child_content=child_revision_input,
                submitted_by_user_id=reviewer.id,
                actor_role=UserRole.REVIEW_ADMIN,
            )
            assert {
                target.knowledge_base_id for target, _kb in parent_revision_submission.targets
            } == {item.id for item in knowledge_bases}

            stable_owner = await session.scalar(
                select(Child.created_by_user_id).where(Child.id == admin_child.id)
            )
            assert stable_owner == owner.id
    finally:
        await engine.dispose()
