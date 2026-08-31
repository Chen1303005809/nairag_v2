from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_content import (
    ChildRevision,
    ParentRevision,
    SearchAnnotationResultFeedback,
    SearchAnnotationResultLabel,
    SearchAnnotationReview,
    SearchEvent,
    SearchInteraction,
    SearchInteractionType,
    SearchResultItem,
)
from app.models.user_account import UserAccount
from app.services.search_batch import PersistedQueryResult, merge_persisted_query_results


class SearchAnnotationReviewUnavailableError(Exception):
    """The caller cannot learn whether an interaction exists or belongs to another user."""


class SearchAnnotationReviewConflictError(Exception):
    pass


class SearchAnnotationReviewNotFoundError(Exception):
    pass


class SearchAnnotationReviewInputError(ValueError):
    pass


class SearchAnnotationFilterError(ValueError):
    pass


@dataclass(frozen=True)
class ResultFeedbackInput:
    search_result_item_id: UUID
    feedback_type: SearchAnnotationResultLabel
    other_note: str | None


@dataclass(frozen=True)
class AnnotationResultLabelCounts:
    high_score_irrelevant_count: int = 0
    low_score_relevant_count: int = 0
    normal_count: int = 0
    other_count: int = 0


@dataclass(frozen=True)
class AnnotationFeedbackSummary:
    completed_review_count: int
    annotated_result_count: int
    high_score_irrelevant_count: int
    low_score_relevant_count: int
    normal_count: int
    other_count: int


@dataclass(frozen=True)
class AnnotationFeedbackActor:
    id: UUID
    username: str
    display_name: str


@dataclass(frozen=True)
class AnnotationFeedbackListItem:
    id: UUID
    submitted_by: AnnotationFeedbackActor
    interaction_type: SearchInteractionType
    queries: list[str]
    target_knowledge_base_id: UUID | None
    target_knowledge_base_name: str | None
    high_score_irrelevant_count: int
    low_score_relevant_count: int
    normal_count: int
    other_count: int
    searched_at: datetime
    submitted_at: datetime
    result_count: int


@dataclass(frozen=True)
class AnnotationFeedbackPage:
    items: list[AnnotationFeedbackListItem]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class AnnotationFeedbackResultDetail:
    result_item_id: UUID
    rank: int
    score: float
    hybrid_score: float | None
    rerank_score: float | None
    selection_stage: str
    matched_field: str | None
    parent_name: str
    question: str
    knowledge_base_id: UUID
    knowledge_base_name: str
    matched_queries: list[str]
    feedback_type: SearchAnnotationResultLabel
    other_note: str | None


@dataclass(frozen=True)
class AnnotationFeedbackQueryDetail:
    search_event_id: UUID
    query_order: int
    query_text: str | None
    ocr_text: str | None
    no_match: bool
    results: list[AnnotationFeedbackResultDetail]


@dataclass(frozen=True)
class AnnotationFeedbackDetail(AnnotationFeedbackListItem):
    no_match: bool
    degraded: bool
    degradation_reasons: list[str]
    query_details: list[AnnotationFeedbackQueryDetail]


def _normalized_keyword(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _normalized_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # Older callers may still send a local-looking ISO timestamp without
        # an offset. Treat it consistently as UTC rather than allowing a
        # naive/aware comparison to fail at runtime.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _feedback_conditions(
    *,
    annotated_from: datetime | None,
    annotated_to: datetime | None,
    knowledge_base_id: UUID | None,
    query_keyword: str | None,
) -> list[object]:
    annotated_from = _normalized_datetime(annotated_from)
    annotated_to = _normalized_datetime(annotated_to)
    if annotated_from is not None and annotated_to is not None and annotated_from > annotated_to:
        raise SearchAnnotationFilterError("标注开始时间不能晚于结束时间")

    conditions: list[object] = []
    if annotated_from is not None:
        conditions.append(SearchAnnotationReview.submitted_at >= annotated_from)
    if annotated_to is not None:
        conditions.append(SearchAnnotationReview.submitted_at <= annotated_to)
    if knowledge_base_id is not None:
        returned_from_knowledge_base = exists(
            select(SearchResultItem.id)
            .join(SearchEvent, SearchEvent.id == SearchResultItem.search_event_id)
            .where(
                SearchEvent.search_interaction_id == SearchInteraction.id,
                SearchResultItem.knowledge_base_id == knowledge_base_id,
            )
        )
        conditions.append(
            or_(
                SearchInteraction.knowledge_base_id == knowledge_base_id,
                returned_from_knowledge_base,
            )
        )
    if (keyword := _normalized_keyword(query_keyword)) is not None:
        escaped_keyword = (
            keyword.casefold()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped_keyword}%"
        matching_query = exists(
            select(SearchEvent.id).where(
                SearchEvent.search_interaction_id == SearchInteraction.id,
                or_(
                    func.lower(SearchEvent.query_text).like(pattern, escape="\\"),
                    func.lower(SearchEvent.ocr_text).like(pattern, escape="\\"),
                ),
            )
        )
        conditions.append(matching_query)
    return conditions


def _event_query_label(event: SearchEvent) -> str:
    if event.query_text and event.ocr_text:
        return f"{event.query_text}（OCR：{event.ocr_text}）"
    return event.query_text or event.ocr_text or "（未记录查询）"


def _label_counts(
    values: dict[SearchAnnotationResultLabel, int],
) -> AnnotationResultLabelCounts:
    return AnnotationResultLabelCounts(
        high_score_irrelevant_count=values.get(
            SearchAnnotationResultLabel.HIGH_SCORE_IRRELEVANT,
            0,
        ),
        low_score_relevant_count=values.get(SearchAnnotationResultLabel.LOW_SCORE_RELEVANT, 0),
        normal_count=values.get(SearchAnnotationResultLabel.NORMAL, 0),
        other_count=values.get(SearchAnnotationResultLabel.OTHER, 0),
    )


async def _queries_by_interaction(
    session: AsyncSession,
    interaction_ids: list[UUID],
) -> dict[UUID, list[str]]:
    if not interaction_ids:
        return {}
    events = list(
        (
            await session.scalars(
                select(SearchEvent)
                .where(SearchEvent.search_interaction_id.in_(interaction_ids))
                .order_by(SearchEvent.search_interaction_id, SearchEvent.query_order)
            )
        ).all()
    )
    queries: dict[UUID, list[str]] = {}
    for event in events:
        if event.search_interaction_id is not None:
            queries.setdefault(event.search_interaction_id, []).append(_event_query_label(event))
    return queries


async def _label_counts_by_review(
    session: AsyncSession,
    review_ids: list[UUID],
) -> dict[UUID, AnnotationResultLabelCounts]:
    if not review_ids:
        return {}
    rows = (
        await session.execute(
            select(
                SearchAnnotationResultFeedback.search_annotation_review_id,
                SearchAnnotationResultFeedback.feedback_type,
                func.count(SearchAnnotationResultFeedback.id),
            )
            .where(SearchAnnotationResultFeedback.search_annotation_review_id.in_(review_ids))
            .group_by(
                SearchAnnotationResultFeedback.search_annotation_review_id,
                SearchAnnotationResultFeedback.feedback_type,
            )
        )
    ).all()
    values_by_review: dict[UUID, dict[SearchAnnotationResultLabel, int]] = {}
    for review_id, feedback_type, count in rows:
        values_by_review.setdefault(review_id, {})[feedback_type] = int(count)
    return {
        review_id: _label_counts(values)
        for review_id, values in values_by_review.items()
    }


def _as_list_item(
    review: SearchAnnotationReview,
    interaction: SearchInteraction,
    user: UserAccount,
    target_knowledge_base: KnowledgeBase | None,
    label_counts: AnnotationResultLabelCounts,
    queries: list[str],
) -> AnnotationFeedbackListItem:
    return AnnotationFeedbackListItem(
        id=review.id,
        submitted_by=AnnotationFeedbackActor(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
        ),
        interaction_type=interaction.interaction_type,
        queries=queries,
        target_knowledge_base_id=interaction.knowledge_base_id,
        target_knowledge_base_name=(
            target_knowledge_base.name if target_knowledge_base is not None else None
        ),
        high_score_irrelevant_count=label_counts.high_score_irrelevant_count,
        low_score_relevant_count=label_counts.low_score_relevant_count,
        normal_count=label_counts.normal_count,
        other_count=label_counts.other_count,
        searched_at=interaction.created_at,
        submitted_at=review.submitted_at,
        result_count=review.reviewed_result_count,
    )


def _normalized_result_feedbacks(
    result_feedbacks: list[ResultFeedbackInput],
) -> list[ResultFeedbackInput]:
    normalized: list[ResultFeedbackInput] = []
    seen_result_item_ids: set[UUID] = set()
    for item in result_feedbacks:
        if item.search_result_item_id in seen_result_item_ids:
            raise SearchAnnotationReviewInputError("同一检索结果只能标注一次")
        seen_result_item_ids.add(item.search_result_item_id)
        other_note = item.other_note.strip() if item.other_note is not None else None
        if item.feedback_type == SearchAnnotationResultLabel.OTHER:
            if not other_note:
                raise SearchAnnotationReviewInputError("选择“其他”时必须填写说明")
            if len(other_note) > 4_000:
                raise SearchAnnotationReviewInputError("其他说明不能超过 4000 字符")
        elif other_note is not None:
            raise SearchAnnotationReviewInputError("该标注类型不接受其他说明")
        normalized.append(
            ResultFeedbackInput(
                search_result_item_id=item.search_result_item_id,
                feedback_type=item.feedback_type,
                other_note=other_note,
            )
        )
    return normalized


async def _events_and_persisted_results(
    session: AsyncSession,
    interaction_id: UUID,
) -> tuple[list[SearchEvent], list[PersistedQueryResult]]:
    events = list(
        (
            await session.scalars(
                select(SearchEvent)
                .where(SearchEvent.search_interaction_id == interaction_id)
                .order_by(SearchEvent.query_order)
            )
        ).all()
    )
    event_ids = [event.id for event in events]
    if not event_ids:
        return events, []
    rows = (
        await session.execute(
            select(
                SearchResultItem,
                ParentRevision,
                ChildRevision,
                KnowledgeBase,
            )
            .join(ParentRevision, ParentRevision.id == SearchResultItem.parent_revision_id)
            .join(ChildRevision, ChildRevision.id == SearchResultItem.child_revision_id)
            .join(KnowledgeBase, KnowledgeBase.id == SearchResultItem.knowledge_base_id)
            .where(SearchResultItem.search_event_id.in_(event_ids))
            .order_by(SearchResultItem.search_event_id, SearchResultItem.rank)
        )
    ).all()
    labels_by_event = {event.id: _event_query_label(event) for event in events}
    query_orders_by_event = {event.id: event.query_order or 1 for event in events}
    return (
        events,
        [
            PersistedQueryResult(
                search_event_id=result_item.search_event_id,
                query_order=query_orders_by_event[result_item.search_event_id],
                query_label=labels_by_event[result_item.search_event_id],
                result_item=result_item,
                parent_name=parent_revision.name,
                question=child_revision.question,
                knowledge_base_name=knowledge_base.name,
            )
            for result_item, parent_revision, child_revision, knowledge_base in rows
        ],
    )


async def _review_feedbacks(
    session: AsyncSession,
    review_id: UUID,
) -> list[SearchAnnotationResultFeedback]:
    return list(
        (
            await session.scalars(
                select(SearchAnnotationResultFeedback)
                .where(SearchAnnotationResultFeedback.search_annotation_review_id == review_id)
                .order_by(SearchAnnotationResultFeedback.search_result_item_id)
            )
        ).all()
    )


def _same_review(
    stored_feedbacks: list[SearchAnnotationResultFeedback],
    requested_feedbacks: list[ResultFeedbackInput],
) -> bool:
    stored = {
        item.search_result_item_id: (item.feedback_type, item.other_note)
        for item in stored_feedbacks
    }
    requested = {
        item.search_result_item_id: (item.feedback_type, item.other_note)
        for item in requested_feedbacks
    }
    return stored == requested


async def record_search_annotation_review(
    session: AsyncSession,
    *,
    user_id: UUID,
    interaction_id: UUID,
    result_feedbacks: list[ResultFeedbackInput],
) -> tuple[SearchAnnotationReview, list[SearchAnnotationResultFeedback], bool]:
    """Atomically complete one immutable review with one label per visible result."""

    normalized_feedbacks = _normalized_result_feedbacks(result_feedbacks)
    interaction = await session.scalar(
        select(SearchInteraction).where(
            SearchInteraction.id == interaction_id,
            SearchInteraction.user_id == user_id,
            SearchInteraction.interaction_type.in_(
                (SearchInteractionType.VECTOR, SearchInteractionType.QUICK_SEARCH)
            ),
        )
    )
    if interaction is None:
        raise SearchAnnotationReviewUnavailableError(interaction_id)

    _events, persisted_results = await _events_and_persisted_results(session, interaction.id)
    visible_result_ids = {
        item.result.result_item.id for item in merge_persisted_query_results(persisted_results)
    }
    submitted_result_ids = {item.search_result_item_id for item in normalized_feedbacks}
    if submitted_result_ids != visible_result_ids:
        raise SearchAnnotationReviewInputError("请逐条完成本次检索实际展示的全部结果")

    existing = await session.scalar(
        select(SearchAnnotationReview).where(
            SearchAnnotationReview.search_interaction_id == interaction.id
        )
    )
    if existing is not None:
        stored_feedbacks = await _review_feedbacks(session, existing.id)
        if _same_review(stored_feedbacks, normalized_feedbacks):
            return existing, stored_feedbacks, True
        raise SearchAnnotationReviewConflictError(interaction_id)

    review = SearchAnnotationReview(
        submitted_by_user_id=user_id,
        search_interaction_id=interaction.id,
        reviewed_result_count=len(visible_result_ids),
    )
    feedback_models: list[SearchAnnotationResultFeedback] = []
    try:
        async with session.begin_nested():
            session.add(review)
            await session.flush()
            feedback_models = [
                SearchAnnotationResultFeedback(
                    search_annotation_review_id=review.id,
                    search_result_item_id=item.search_result_item_id,
                    feedback_type=item.feedback_type,
                    other_note=item.other_note,
                )
                for item in normalized_feedbacks
            ]
            session.add_all(feedback_models)
            await session.flush()
    except IntegrityError:
        # A concurrent first submission may have won the unique interaction
        # constraint. Re-read it and retain the same retry/conflict semantics.
        existing = await session.scalar(
            select(SearchAnnotationReview).where(
                SearchAnnotationReview.search_interaction_id == interaction.id
            )
        )
        if existing is None:
            raise
        stored_feedbacks = await _review_feedbacks(session, existing.id)
        if _same_review(stored_feedbacks, normalized_feedbacks):
            return existing, stored_feedbacks, True
        raise SearchAnnotationReviewConflictError(interaction_id) from None
    return review, feedback_models, False


async def get_annotation_feedback_summary(
    session: AsyncSession,
    *,
    annotated_from: datetime | None = None,
    annotated_to: datetime | None = None,
    knowledge_base_id: UUID | None = None,
    query_keyword: str | None = None,
) -> AnnotationFeedbackSummary:
    conditions = _feedback_conditions(
        annotated_from=annotated_from,
        annotated_to=annotated_to,
        knowledge_base_id=knowledge_base_id,
        query_keyword=query_keyword,
    )
    completed_review_count = await session.scalar(
        select(func.count(SearchAnnotationReview.id))
        .join(
            SearchInteraction,
            SearchInteraction.id == SearchAnnotationReview.search_interaction_id,
        )
        .where(*conditions)
    )
    rows = (
        await session.execute(
            select(
                SearchAnnotationResultFeedback.feedback_type,
                func.count(SearchAnnotationResultFeedback.id),
            )
            .join(
                SearchAnnotationReview,
                SearchAnnotationReview.id
                == SearchAnnotationResultFeedback.search_annotation_review_id,
            )
            .join(
                SearchInteraction,
                SearchInteraction.id == SearchAnnotationReview.search_interaction_id,
            )
            .where(*conditions)
            .group_by(SearchAnnotationResultFeedback.feedback_type)
        )
    ).all()
    counts = _label_counts({feedback_type: int(count) for feedback_type, count in rows})
    return AnnotationFeedbackSummary(
        completed_review_count=completed_review_count or 0,
        annotated_result_count=(
            counts.high_score_irrelevant_count
            + counts.low_score_relevant_count
            + counts.normal_count
            + counts.other_count
        ),
        high_score_irrelevant_count=counts.high_score_irrelevant_count,
        low_score_relevant_count=counts.low_score_relevant_count,
        normal_count=counts.normal_count,
        other_count=counts.other_count,
    )


async def list_annotation_feedback(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    feedback_type: SearchAnnotationResultLabel | None = None,
    annotated_from: datetime | None = None,
    annotated_to: datetime | None = None,
    knowledge_base_id: UUID | None = None,
    query_keyword: str | None = None,
) -> AnnotationFeedbackPage:
    if page < 1:
        raise SearchAnnotationFilterError("页码必须大于 0")
    if not 1 <= page_size <= 100:
        raise SearchAnnotationFilterError("每页数量必须在 1 到 100 之间")
    conditions = _feedback_conditions(
        annotated_from=annotated_from,
        annotated_to=annotated_to,
        knowledge_base_id=knowledge_base_id,
        query_keyword=query_keyword,
    )
    if feedback_type is not None:
        conditions.append(
            exists(
                select(SearchAnnotationResultFeedback.id).where(
                    SearchAnnotationResultFeedback.search_annotation_review_id
                    == SearchAnnotationReview.id,
                    SearchAnnotationResultFeedback.feedback_type == feedback_type,
                )
            )
        )

    target_knowledge_base = aliased(KnowledgeBase)
    total = await session.scalar(
        select(func.count(SearchAnnotationReview.id))
        .join(
            SearchInteraction,
            SearchInteraction.id == SearchAnnotationReview.search_interaction_id,
        )
        .where(*conditions)
    )
    rows = (
        await session.execute(
            select(
                SearchAnnotationReview,
                SearchInteraction,
                UserAccount,
                target_knowledge_base,
            )
            .join(
                SearchInteraction,
                SearchInteraction.id == SearchAnnotationReview.search_interaction_id,
            )
            .join(UserAccount, UserAccount.id == SearchAnnotationReview.submitted_by_user_id)
            .outerjoin(
                target_knowledge_base,
                target_knowledge_base.id == SearchInteraction.knowledge_base_id,
            )
            .where(*conditions)
            .order_by(
                SearchAnnotationReview.submitted_at.desc(),
                SearchAnnotationReview.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    interaction_ids = [interaction.id for _review, interaction, *_rest in rows]
    review_ids = [review.id for review, *_rest in rows]
    queries_by_interaction = await _queries_by_interaction(session, interaction_ids)
    counts_by_review = await _label_counts_by_review(session, review_ids)
    items = [
        _as_list_item(
            review,
            interaction,
            user,
            target_knowledge_base,
            counts_by_review.get(review.id, AnnotationResultLabelCounts()),
            queries_by_interaction.get(interaction.id, []),
        )
        for review, interaction, user, target_knowledge_base in rows
    ]
    return AnnotationFeedbackPage(
        items=items,
        total=total or 0,
        page=page,
        page_size=page_size,
    )


async def get_annotation_feedback_detail(
    session: AsyncSession,
    *,
    feedback_id: UUID,
) -> AnnotationFeedbackDetail:
    target_knowledge_base = aliased(KnowledgeBase)
    row = (
        await session.execute(
            select(
                SearchAnnotationReview,
                SearchInteraction,
                UserAccount,
                target_knowledge_base,
            )
            .join(
                SearchInteraction,
                SearchInteraction.id == SearchAnnotationReview.search_interaction_id,
            )
            .join(UserAccount, UserAccount.id == SearchAnnotationReview.submitted_by_user_id)
            .outerjoin(
                target_knowledge_base,
                target_knowledge_base.id == SearchInteraction.knowledge_base_id,
            )
            .where(SearchAnnotationReview.id == feedback_id)
        )
    ).one_or_none()
    if row is None:
        raise SearchAnnotationReviewNotFoundError(feedback_id)
    review, interaction, user, target_knowledge_base = row
    events, persisted_results = await _events_and_persisted_results(session, interaction.id)
    merged_results = merge_persisted_query_results(persisted_results)
    feedbacks = await _review_feedbacks(session, review.id)
    feedback_by_result_item_id = {
        feedback.search_result_item_id: feedback for feedback in feedbacks
    }
    label_counts = _label_counts(
        {
            label: sum(1 for feedback in feedbacks if feedback.feedback_type == label)
            for label in SearchAnnotationResultLabel
        }
    )
    results_by_event: dict[UUID, list[AnnotationFeedbackResultDetail]] = {}
    for merged_result in merged_results:
        persisted = merged_result.result
        result_item = persisted.result_item
        result_feedback = feedback_by_result_item_id.get(result_item.id)
        if result_feedback is None:
            raise RuntimeError("completed annotation review is missing a visible result label")
        results_by_event.setdefault(persisted.search_event_id, []).append(
            AnnotationFeedbackResultDetail(
                result_item_id=result_item.id,
                rank=result_item.rank,
                score=result_item.score,
                hybrid_score=result_item.hybrid_score,
                rerank_score=result_item.rerank_score,
                selection_stage=result_item.selection_stage,
                matched_field=result_item.matched_field,
                parent_name=persisted.parent_name,
                question=persisted.question,
                knowledge_base_id=result_item.knowledge_base_id,
                knowledge_base_name=persisted.knowledge_base_name,
                matched_queries=list(merged_result.matched_queries),
                feedback_type=result_feedback.feedback_type,
                other_note=result_feedback.other_note,
            )
        )
    query_details = [
        AnnotationFeedbackQueryDetail(
            search_event_id=event.id,
            query_order=event.query_order or 1,
            query_text=event.query_text,
            ocr_text=event.ocr_text,
            no_match=event.no_match,
            results=results_by_event.get(event.id, []),
        )
        for event in events
    ]
    list_item = _as_list_item(
        review,
        interaction,
        user,
        target_knowledge_base,
        label_counts,
        [_event_query_label(event) for event in events],
    )
    return AnnotationFeedbackDetail(
        **list_item.__dict__,
        no_match=interaction.no_match,
        degraded=interaction.degraded,
        degradation_reasons=list(interaction.degradation_reasons or []),
        query_details=query_details,
    )
