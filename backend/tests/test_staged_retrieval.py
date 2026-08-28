from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.llm import RelevanceCandidate, RelevanceJudgement
from app.services.retrieval import IndexQuery
from app.services.search import (
    ScoredCandidate,
    SearchPipelineOptions,
    StagedRetrievalPipeline,
    _sort_selected_candidates,
)


def make_scored_candidate(score: float, *, helpful_count: int = 0) -> ScoredCandidate:
    candidate = SimpleNamespace(
        publication=SimpleNamespace(helpful_count=helpful_count),
        knowledge_base=SimpleNamespace(id=uuid4()),
        child_revision=SimpleNamespace(
            id=uuid4(),
            question="如何处理登录失败？",
            response_content="请联系管理员重置密码。",
        ),
        question_variants=[SimpleNamespace(question_text="无法登录怎么办？")],
    )
    return ScoredCandidate(
        candidate=candidate,
        hybrid_score=score,
        match_reason="hybrid_dense_bm25",
        matched_field="question",
    )


class RecordingReranker:
    def __init__(self, scores: list[float] | Exception) -> None:
        self.scores = scores
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, documents))
        if isinstance(self.scores, Exception):
            raise self.scores
        return self.scores


class RecordingJudge:
    def __init__(
        self,
        relevant_positions: set[int] | None = None,
        *,
        failure: Exception | None = None,
        incomplete: bool = False,
        invalid: bool = False,
        duplicate: bool = False,
    ) -> None:
        self.relevant_positions = relevant_positions or set()
        self.failure = failure
        self.incomplete = incomplete
        self.invalid = invalid
        self.duplicate = duplicate
        self.calls: list[tuple[str, list[RelevanceCandidate]]] = []

    async def judge_search_relevance(
        self,
        query: str,
        candidates: list[RelevanceCandidate],
    ) -> list[RelevanceJudgement]:
        self.calls.append((query, candidates))
        if self.failure is not None:
            raise self.failure
        if self.incomplete:
            return [RelevanceJudgement(candidate_id=candidates[0].candidate_id, relevant=True)]
        if self.invalid:
            return [
                RelevanceJudgement(candidate_id="unknown", relevant=True),
                *[
                    RelevanceJudgement(candidate_id=candidate.candidate_id, relevant=False)
                    for candidate in candidates[1:]
                ],
            ]
        if self.duplicate:
            return [
                RelevanceJudgement(candidate_id=candidates[0].candidate_id, relevant=True)
                for _candidate in candidates
            ]
        return [
            RelevanceJudgement(
                candidate_id=candidate.candidate_id,
                relevant=index in self.relevant_positions,
            )
            for index, candidate in enumerate(candidates)
        ]


def pipeline(*, reranker=None, judge=None) -> StagedRetrievalPipeline:
    return StagedRetrievalPipeline(
        reranker=reranker,
        relevance_judge=judge,
        options=SearchPipelineOptions(),
    )


@pytest.mark.asyncio
async def test_high_hybrid_score_short_circuits_models_and_returns_all_retrieval_hits() -> None:
    reranker = RecordingReranker([0.9, 0.9])
    judge = RecordingJudge({0, 1})
    decision = await pipeline(reranker=reranker, judge=judge).select(
        [make_scored_candidate(0.72), make_scored_candidate(0.69)],
        queries=[IndexQuery(text="登录失败", channel="text", weight=1.0)],
        index_degraded=False,
    )

    assert [item.selection_stage for item in decision.selected] == ["hybrid", "hybrid"]
    assert [item.hybrid_score for item in decision.selected] == [0.72, 0.69]
    assert reranker.calls == []
    assert judge.calls == []


@pytest.mark.asyncio
async def test_rerank_acceptance_short_circuits_llm() -> None:
    reranker = RecordingReranker([0.5, 0.49])
    judge = RecordingJudge({0, 1})
    decision = await pipeline(reranker=reranker, judge=judge).select(
        [make_scored_candidate(0.69), make_scored_candidate(0.42)],
        queries=[IndexQuery(text="登录失败", channel="text", weight=1.0)],
        index_degraded=False,
    )

    assert [item.selection_stage for item in decision.selected] == ["rerank"]
    assert decision.selected[0].rerank_score == 0.5
    assert len(reranker.calls) == 1
    assert judge.calls == []


@pytest.mark.asyncio
async def test_llm_returns_only_explicitly_relevant_candidates_after_low_rerank() -> None:
    reranker = RecordingReranker([0.49, 0.3])
    judge = RecordingJudge({1})
    decision = await pipeline(reranker=reranker, judge=judge).select(
        [make_scored_candidate(0.65), make_scored_candidate(0.4)],
        queries=[IndexQuery(text="登录失败", channel="text", weight=1.0)],
        index_degraded=False,
    )

    assert [item.selection_stage for item in decision.selected] == ["llm"]
    assert decision.selected[0].rerank_score == 0.3
    assert len(judge.calls) == 1


@pytest.mark.asyncio
async def test_llm_can_return_multiple_explicitly_relevant_candidates() -> None:
    judge = RecordingJudge({0, 1})
    decision = await pipeline(
        reranker=RecordingReranker([0.49, 0.3]),
        judge=judge,
    ).select(
        [make_scored_candidate(0.65), make_scored_candidate(0.4)],
        queries=[IndexQuery(text="登录失败", channel="text", weight=1.0)],
        index_degraded=False,
    )

    assert [item.selection_stage for item in decision.selected] == ["llm", "llm"]
    assert len(judge.calls) == 1


@pytest.mark.asyncio
async def test_successful_llm_with_no_relevant_candidates_does_not_use_score_fallback() -> None:
    judge = RecordingJudge()
    decision = await pipeline(judge=judge).select(
        [make_scored_candidate(0.6), make_scored_candidate(0.3)],
        queries=[IndexQuery(text="登录失败", channel="text", weight=1.0)],
        index_degraded=False,
    )

    assert decision.selected == []
    assert decision.degradation_reasons == ()


@pytest.mark.asyncio
async def test_unconfigured_models_use_raw_hybrid_fallback_and_keep_helpful_out_of_gates() -> None:
    popular_but_sub_high = make_scored_candidate(0.69, helpful_count=100_000)
    low_candidate = make_scored_candidate(0.21)
    decision = await pipeline().select(
        [popular_but_sub_high, low_candidate],
        queries=[IndexQuery(text="登录失败", channel="text", weight=1.0)],
        index_degraded=False,
    )

    assert [item.selection_stage for item in decision.selected] == ["score_fallback"]
    assert decision.selected[0].hybrid_score == 0.69
    assert decision.selected[0].score > 0.69
    assert decision.degradation_reasons == (
        "reranker_unconfigured",
        "llm_unconfigured",
    )


@pytest.mark.asyncio
async def test_empty_score_fallback_does_not_mark_an_independent_keyword_path_degraded() -> None:
    decision = await pipeline().select(
        [make_scored_candidate(0.21)],
        queries=[IndexQuery(text="登录失败", channel="text", weight=1.0)],
        index_degraded=False,
    )

    assert decision.selected == []
    assert decision.degradation_reasons == ()


@pytest.mark.asyncio
async def test_invalid_reranker_item_count_falls_back_without_raising() -> None:
    decision = await pipeline(reranker=RecordingReranker([0.4])).select(
        [make_scored_candidate(0.4), make_scored_candidate(0.1)],
        queries=[IndexQuery(text="登录失败", channel="text", weight=1.0)],
        index_degraded=False,
    )

    assert [item.selection_stage for item in decision.selected] == ["score_fallback"]
    assert decision.degradation_reasons == ("reranker_failed", "llm_unconfigured")


@pytest.mark.asyncio
async def test_failed_or_invalid_llm_judgements_fall_back_without_raising() -> None:
    judges = [
        RecordingJudge(failure=TimeoutError("timeout")),
        RecordingJudge(incomplete=True),
        RecordingJudge(invalid=True),
        RecordingJudge(duplicate=True),
    ]
    for judge in judges:
        decision = await pipeline(judge=judge).select(
            [make_scored_candidate(0.4), make_scored_candidate(0.1)],
            queries=[IndexQuery(text="登录失败", channel="text", weight=1.0)],
            index_degraded=False,
        )

        assert [item.selection_stage for item in decision.selected] == ["score_fallback"]
        assert decision.degradation_reasons == ("reranker_unconfigured", "llm_failed")


@pytest.mark.asyncio
async def test_failed_reranker_and_invalid_llm_fall_back_without_raising() -> None:
    decision = await pipeline(
        reranker=RecordingReranker(TimeoutError("timeout")),
        judge=RecordingJudge(invalid=True),
    ).select(
        [make_scored_candidate(0.4), make_scored_candidate(0.1)],
        queries=[IndexQuery(text="登录失败", channel="text", weight=1.0)],
        index_degraded=False,
    )

    assert [item.selection_stage for item in decision.selected] == ["score_fallback"]
    assert decision.degradation_reasons == ("reranker_failed", "llm_failed")


@pytest.mark.asyncio
async def test_composite_score_then_helpful_count_controls_stable_order() -> None:
    decision = await pipeline().select(
        [make_scored_candidate(0.3, helpful_count=0), make_scored_candidate(0.3, helpful_count=5)],
        queries=[IndexQuery(text="登录失败", channel="text", weight=1.0)],
        index_degraded=False,
    )

    ordered = _sort_selected_candidates(decision.selected)
    assert ordered[0].helpful_count_at_search == 5
    assert ordered[0].score > ordered[1].score
