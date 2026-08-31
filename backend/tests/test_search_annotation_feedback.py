from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # Register model metadata.
from app.core.config import Settings
from app.core.security import create_session_token
from app.db.base import Base
from app.main import create_app
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_content import (
    Child,
    ChildKnowledgeBasePublication,
    ChildPublicationStatus,
    ChildRevision,
    Parent,
    ParentRevision,
    ReviewSubmission,
    ReviewSubmissionKind,
    ReviewSubmissionStatus,
    SearchAnnotationResultFeedback,
    SearchAnnotationResultLabel,
    SearchAnnotationReview,
    SearchEvent,
    SearchInteractionType,
    SearchQueryMode,
)
from app.models.user_account import UserAccount, UserRole
from app.schemas.search import SearchAnnotationReviewRequest
from app.services.ocr import OcrRecognition
from app.services.search import SearchDetails, search_published_content
from app.services.search_annotations import (
    ResultFeedbackInput,
    SearchAnnotationReviewConflictError,
    SearchAnnotationReviewInputError,
    SearchAnnotationReviewUnavailableError,
    get_annotation_feedback_detail,
    get_annotation_feedback_summary,
    list_annotation_feedback,
    record_search_annotation_review,
)
from app.services.search_batch import QueryBatchSearchDetails, execute_query_batch


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'search-annotations.sqlite3'}",
        jwt_secret="test-signing-key-that-is-long-enough",
        cookie_secure=False,
        index_artifact_dir=tmp_path / "index-artifacts",
        attachment_storage_dir=tmp_path / "attachments",
    )


async def build_db(tmp_path: Path) -> tuple[async_sessionmaker[AsyncSession], object, Settings]:
    settings = make_settings(tmp_path)
    engine = create_async_engine(settings.database_url_with_password)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine, settings


async def create_user(
    session: AsyncSession,
    *,
    role: UserRole = UserRole.NORMAL_USER,
) -> UserAccount:
    user = UserAccount(
        username=f"user-{uuid4().hex[:8]}",
        display_name="标注测试用户",
        password_hash="x" * 32,
        role=role,
        must_change_password=False,
    )
    session.add(user)
    await session.flush()
    return user


async def create_published_graph(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> tuple[KnowledgeBase, ChildKnowledgeBasePublication]:
    parent = Parent(created_by_user_id=user_id)
    knowledge_base = KnowledgeBase(
        logical_key=f"kb-{uuid4().hex[:8]}",
        name="标注测试知识库",
        current_physical_collection_name=f"collection-{uuid4().hex[:8]}",
        created_by_user_id=user_id,
    )
    session.add_all([parent, knowledge_base])
    await session.flush()

    parent_revision = ParentRevision(
        parent_id=parent.id,
        revision_number=1,
        name="登录问题",
        canonical_keyword="登录",
        created_by_user_id=user_id,
    )
    child = Child(parent_id=parent.id, is_primary=True, created_by_user_id=user_id)
    session.add_all([parent_revision, child])
    await session.flush()
    child_revision = ChildRevision(
        child_id=child.id,
        revision_number=1,
        question="登录失败怎么办？",
        response_content="请先检查账号状态，再联系管理员重置。",
        created_by_user_id=user_id,
    )
    session.add(child_revision)
    await session.flush()
    session.add(
        ReviewSubmission(
            submission_kind=ReviewSubmissionKind.PARENT_WITH_PRIMARY,
            status=ReviewSubmissionStatus.PUBLISHED,
            parent_id=parent.id,
            parent_revision_id=parent_revision.id,
            child_id=child.id,
            child_revision_id=child_revision.id,
            submitted_by_user_id=user_id,
        )
    )
    publication = ChildKnowledgeBasePublication(
        child_id=child.id,
        knowledge_base_id=knowledge_base.id,
        status=ChildPublicationStatus.PUBLISHED,
        active_revision_id=child_revision.id,
    )
    session.add(publication)
    await session.flush()
    return knowledge_base, publication


async def create_empty_knowledge_base(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> KnowledgeBase:
    knowledge_base = KnowledgeBase(
        logical_key=f"empty-{uuid4().hex[:8]}",
        name="空知识库",
        current_physical_collection_name=f"collection-{uuid4().hex[:8]}",
        created_by_user_id=user_id,
    )
    session.add(knowledge_base)
    await session.flush()
    return knowledge_base


def visible_result_ids(search: SearchDetails) -> list[UUID]:
    return [result.id for _parent, items in search.groups for result, _candidate in items]


def visible_batch_result_ids(search: QueryBatchSearchDetails) -> list[UUID]:
    return [item.result_item.id for _parent, items in search.groups for item in items]


def result_feedbacks(
    result_item_ids: list[UUID],
    labels: list[SearchAnnotationResultLabel],
    *,
    other_note: str | None = None,
) -> list[ResultFeedbackInput]:
    assert result_item_ids
    assert len(result_item_ids) == len(labels)
    return [
        ResultFeedbackInput(
            search_result_item_id=result_item_id,
            feedback_type=label,
            other_note=other_note if label == SearchAnnotationResultLabel.OTHER else None,
        )
        for result_item_id, label in zip(result_item_ids, labels, strict=True)
    ]


def test_annotation_review_request_normalizes_and_validates_result_labels() -> None:
    result_item_id = uuid4()
    request = SearchAnnotationReviewRequest(
        result_feedbacks=[
            {
                "search_result_item_id": result_item_id,
                "feedback_type": "other",
                "other_note": "  需要补充召回  ",
            }
        ]
    )
    assert request.result_feedbacks[0].other_note == "需要补充召回"

    with pytest.raises(ValidationError, match="必须填写说明"):
        SearchAnnotationReviewRequest(
            result_feedbacks=[
                {
                    "search_result_item_id": result_item_id,
                    "feedback_type": "other",
                    "other_note": "   ",
                }
            ]
        )
    with pytest.raises(ValidationError, match="不接受其他说明"):
        SearchAnnotationReviewRequest(
            result_feedbacks=[
                {
                    "search_result_item_id": result_item_id,
                    "feedback_type": "normal",
                    "other_note": "不应保存",
                }
            ]
        )
    with pytest.raises(ValidationError, match="同一检索结果只能标注一次"):
        SearchAnnotationReviewRequest(
            result_feedbacks=[
                {"search_result_item_id": result_item_id, "feedback_type": "normal"},
                {"search_result_item_id": result_item_id, "feedback_type": "normal"},
            ]
        )


@pytest.mark.asyncio
async def test_search_interactions_link_vector_and_ordered_batch_events(tmp_path: Path) -> None:
    factory, engine, settings = await build_db(tmp_path)
    try:
        async with factory() as session:
            user = await create_user(session)
            knowledge_base, _publication = await create_published_graph(session, user_id=user.id)

            vector = await search_published_content(
                session,
                user_id=user.id,
                query="登录失败怎么办？",
                ocr_text=None,
                knowledge_base_id=knowledge_base.id,
                retrieval_mode="vector",
                limit=10,
            )
            assert vector.interaction is not None
            assert vector.interaction.interaction_type == SearchInteractionType.VECTOR
            assert vector.event.search_interaction_id == vector.interaction.id
            assert vector.event.query_order == 1

            recognition = OcrRecognition(
                text="登录失败怎么办？",
                keywords=("登录",),
                confidence=0.97,
                model_version="PP-OCRv6_medium",
                image_sha256="a" * 64,
            )
            image = await search_published_content(
                session,
                user_id=user.id,
                query=None,
                ocr_text=recognition.text,
                ocr_recognition=recognition,
                knowledge_base_id=knowledge_base.id,
                retrieval_mode="vector",
                limit=10,
            )
            mixed = await search_published_content(
                session,
                user_id=user.id,
                query="登录问题",
                ocr_text=recognition.text,
                ocr_recognition=recognition,
                knowledge_base_id=knowledge_base.id,
                retrieval_mode="vector",
                limit=10,
            )
            for details, query_mode in (
                (image, SearchQueryMode.IMAGE),
                (mixed, SearchQueryMode.MIXED),
            ):
                assert details.interaction is not None
                assert details.interaction.interaction_type == SearchInteractionType.VECTOR
                assert details.event.query_mode == query_mode
                assert details.event.search_interaction_id == details.interaction.id
                assert details.event.query_order == 1

            field_filter = await search_published_content(
                session,
                user_id=user.id,
                query=None,
                ocr_text=None,
                knowledge_base_id=knowledge_base.id,
                retrieval_mode="field_filter",
                limit=10,
            )
            assert field_filter.interaction is None
            assert field_filter.event.search_interaction_id is None
            assert field_filter.event.query_order is None

            batch = await execute_query_batch(
                session,
                user_id=user.id,
                queries=["登录失败怎么办？", "登录失败"],
                knowledge_base_id=None,
                limit=10,
                settings=settings,
                index_backend=None,
            )
            assert batch.interaction is not None
            assert batch.interaction.interaction_type == SearchInteractionType.QUICK_SEARCH
            events = list(
                (
                    await session.scalars(
                        select(SearchEvent)
                        .where(SearchEvent.search_interaction_id == batch.interaction.id)
                        .order_by(SearchEvent.query_order)
                    )
                ).all()
            )
            assert [event.query_text for event in events] == ["登录失败怎么办？", "登录失败"]
            assert [event.query_order for event in events] == [1, 2]
            assert batch.groups
            assert batch.groups[0][1][0].matched_queries == ["登录失败怎么办？", "登录失败"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_result_level_review_is_complete_immutable_and_idempotent(tmp_path: Path) -> None:
    factory, engine, _settings = await build_db(tmp_path)
    try:
        async with factory() as session:
            owner = await create_user(session)
            other_user = await create_user(session)
            knowledge_base, publication = await create_published_graph(session, user_id=owner.id)
            search = await search_published_content(
                session,
                user_id=owner.id,
                query="登录失败怎么办？",
                ocr_text=None,
                knowledge_base_id=knowledge_base.id,
                retrieval_mode="vector",
                limit=10,
            )
            assert search.interaction is not None
            result_item_ids = visible_result_ids(search)
            assert result_item_ids
            helpful_count = publication.helpful_count

            with pytest.raises(SearchAnnotationReviewInputError, match="逐条完成"):
                await record_search_annotation_review(
                    session,
                    user_id=owner.id,
                    interaction_id=search.interaction.id,
                    result_feedbacks=[],
                )
            with pytest.raises(SearchAnnotationReviewInputError, match="逐条完成"):
                await record_search_annotation_review(
                    session,
                    user_id=owner.id,
                    interaction_id=search.interaction.id,
                    result_feedbacks=result_feedbacks(
                        result_item_ids,
                        [SearchAnnotationResultLabel.NORMAL] * len(result_item_ids),
                    )[:-1]
                    + [
                        ResultFeedbackInput(
                            search_result_item_id=uuid4(),
                            feedback_type=SearchAnnotationResultLabel.NORMAL,
                            other_note=None,
                        )
                    ],
                )

            submitted_feedbacks = result_feedbacks(
                result_item_ids,
                [SearchAnnotationResultLabel.NORMAL] * len(result_item_ids),
            )
            review, stored_feedbacks, already_recorded = await record_search_annotation_review(
                session,
                user_id=owner.id,
                interaction_id=search.interaction.id,
                result_feedbacks=submitted_feedbacks,
            )
            assert already_recorded is False
            assert review.reviewed_result_count == len(result_item_ids)
            assert {feedback.feedback_type for feedback in stored_feedbacks} == {
                SearchAnnotationResultLabel.NORMAL
            }
            assert publication.helpful_count == helpful_count

            retried, retried_feedbacks, already_recorded = await record_search_annotation_review(
                session,
                user_id=owner.id,
                interaction_id=search.interaction.id,
                result_feedbacks=submitted_feedbacks,
            )
            assert already_recorded is True
            assert retried.id == review.id
            assert {feedback.id for feedback in retried_feedbacks} == {
                feedback.id for feedback in stored_feedbacks
            }
            with pytest.raises(SearchAnnotationReviewConflictError):
                await record_search_annotation_review(
                    session,
                    user_id=owner.id,
                    interaction_id=search.interaction.id,
                    result_feedbacks=result_feedbacks(
                        result_item_ids,
                        [SearchAnnotationResultLabel.HIGH_SCORE_IRRELEVANT] * len(result_item_ids),
                    ),
                )
            with pytest.raises(SearchAnnotationReviewUnavailableError):
                await record_search_annotation_review(
                    session,
                    user_id=other_user.id,
                    interaction_id=search.interaction.id,
                    result_feedbacks=submitted_feedbacks,
                )
            assert await session.scalar(select(func.count(SearchAnnotationReview.id))) == 1
            assert (
                await session.scalar(select(func.count(SearchAnnotationResultFeedback.id)))
                == len(result_item_ids)
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_summarizes_result_labels_and_rebuilds_deduplicated_batch_results(
    tmp_path: Path,
) -> None:
    factory, engine, settings = await build_db(tmp_path)
    try:
        async with factory() as session:
            user = await create_user(session)
            knowledge_base, _publication = await create_published_graph(session, user_id=user.id)
            empty_knowledge_base = await create_empty_knowledge_base(session, user_id=user.id)

            vector = await search_published_content(
                session,
                user_id=user.id,
                query="登录失败怎么办？",
                ocr_text=None,
                knowledge_base_id=knowledge_base.id,
                retrieval_mode="vector",
                limit=10,
            )
            batch = await execute_query_batch(
                session,
                user_id=user.id,
                queries=["登录失败怎么办？", "登录失败"],
                knowledge_base_id=None,
                limit=10,
                settings=settings,
                index_backend=None,
            )
            no_match = await search_published_content(
                session,
                user_id=user.id,
                query="不存在的知识",
                ocr_text=None,
                knowledge_base_id=empty_knowledge_base.id,
                retrieval_mode="vector",
                limit=10,
            )
            assert vector.interaction is not None
            assert batch.interaction is not None
            assert no_match.interaction is not None

            vector_result_item_ids = visible_result_ids(vector)
            batch_result_item_ids = visible_batch_result_ids(batch)
            assert vector_result_item_ids
            assert batch_result_item_ids
            await record_search_annotation_review(
                session,
                user_id=user.id,
                interaction_id=vector.interaction.id,
                result_feedbacks=result_feedbacks(
                    vector_result_item_ids,
                    [SearchAnnotationResultLabel.HIGH_SCORE_IRRELEVANT]
                    * len(vector_result_item_ids),
                ),
            )
            batch_review, _feedbacks, _ = await record_search_annotation_review(
                session,
                user_id=user.id,
                interaction_id=batch.interaction.id,
                result_feedbacks=result_feedbacks(
                    batch_result_item_ids,
                    [SearchAnnotationResultLabel.OTHER] * len(batch_result_item_ids),
                    other_note="需要补充同义问句",
                ),
            )
            no_match_review, no_match_feedbacks, _ = await record_search_annotation_review(
                session,
                user_id=user.id,
                interaction_id=no_match.interaction.id,
                result_feedbacks=[],
            )
            assert no_match_review.reviewed_result_count == 0
            assert no_match_feedbacks == []
            await session.commit()

            summary = await get_annotation_feedback_summary(
                session,
                knowledge_base_id=knowledge_base.id,
                query_keyword="登录",
            )
            assert summary.completed_review_count == 2
            assert summary.annotated_result_count == len(vector_result_item_ids) + len(
                batch_result_item_ids
            )
            assert summary.high_score_irrelevant_count == len(vector_result_item_ids)
            assert summary.other_count == len(batch_result_item_ids)
            assert summary.low_score_relevant_count == 0
            assert summary.normal_count == 0

            other_page = await list_annotation_feedback(
                session,
                page=1,
                page_size=20,
                feedback_type=SearchAnnotationResultLabel.OTHER,
                knowledge_base_id=knowledge_base.id,
            )
            assert [item.id for item in other_page.items] == [batch_review.id]
            assert other_page.items[0].target_knowledge_base_id is None
            assert other_page.items[0].result_count == len(batch_result_item_ids)
            assert other_page.items[0].other_count == len(batch_result_item_ids)

            empty_page = await list_annotation_feedback(
                session,
                page=1,
                page_size=20,
                knowledge_base_id=empty_knowledge_base.id,
            )
            assert empty_page.total == 1
            assert empty_page.items[0].result_count == 0

            detail = await get_annotation_feedback_detail(session, feedback_id=batch_review.id)
            assert [query.query_order for query in detail.query_details] == [1, 2]
            detail_results = [result for query in detail.query_details for result in query.results]
            assert len(detail_results) == len(batch_result_item_ids)
            assert detail_results[0].matched_queries == ["登录失败怎么办？", "登录失败"]
            assert detail_results[0].feedback_type == SearchAnnotationResultLabel.OTHER
            assert detail_results[0].other_note == "需要补充同义问句"
            assert detail.result_count == len(batch_result_item_ids)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_annotation_routes_require_csrf_owner_and_system_admin(tmp_path: Path) -> None:
    factory, engine, settings = await build_db(tmp_path)
    try:
        async with factory() as session:
            owner = await create_user(session)
            other_user = await create_user(session)
            system_admin = await create_user(session, role=UserRole.SYSTEM_ADMIN)
            knowledge_base, _publication = await create_published_graph(session, user_id=owner.id)
            search = await search_published_content(
                session,
                user_id=owner.id,
                query="登录失败怎么办？",
                ocr_text=None,
                knowledge_base_id=knowledge_base.id,
                retrieval_mode="vector",
                limit=10,
            )
            assert search.interaction is not None
            body = {
                "result_feedbacks": [
                    {
                        "search_result_item_id": str(result_item_id),
                        "feedback_type": "high_score_irrelevant",
                    }
                    for result_item_id in visible_result_ids(search)
                ]
            }
            assert body["result_feedbacks"]
            await session.commit()

        app = create_app(settings=settings, db_session_factory=factory)
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="https://testserver") as client:
                def set_session(user: UserAccount, csrf_token: str) -> None:
                    client.cookies.set(
                        settings.session_cookie_name,
                        create_session_token(user, csrf_token, settings),
                    )
                    client.cookies.set(settings.csrf_cookie_name, csrf_token)

                set_session(owner, "owner-csrf")
                endpoint = (
                    f"/api/v1/search/interactions/{search.interaction.id}/annotation-feedback"
                )
                missing_csrf = await client.post(endpoint, json=body)
                assert missing_csrf.status_code == 403

                incomplete = await client.post(
                    endpoint,
                    headers={"X-CSRF-Token": "owner-csrf"},
                    json={"result_feedbacks": []},
                )
                assert incomplete.status_code == 422

                accepted = await client.post(
                    endpoint,
                    headers={"X-CSRF-Token": "owner-csrf"},
                    json=body,
                )
                assert accepted.status_code == 200
                assert accepted.json()["already_recorded"] is False
                assert accepted.json()["reviewed_result_count"] == len(body["result_feedbacks"])

                set_session(other_user, "other-csrf")
                not_owner = await client.post(
                    endpoint,
                    headers={"X-CSRF-Token": "other-csrf"},
                    json=body,
                )
                assert not_owner.status_code == 404
                assert not_owner.json()["detail"] == "检索记录不存在"

                ordinary_dashboard = await client.get("/api/v1/search/admin/annotation-feedback")
                assert ordinary_dashboard.status_code == 403

                set_session(system_admin, "admin-csrf")
                admin_dashboard = await client.get("/api/v1/search/admin/annotation-feedback")
                assert admin_dashboard.status_code == 200
                assert admin_dashboard.json()["total"] == 1
    finally:
        await engine.dispose()
