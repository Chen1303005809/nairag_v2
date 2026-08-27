from __future__ import annotations

import asyncio
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from app.core.config import Settings
from app.services.embedding import (
    DEFAULT_EMBEDDING_DIMENSION,
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
    QwenRerankerProvider,
    RerankerProvider,
)


class SearchIndexUnavailableError(RuntimeError):
    """No readable derived index exists for the requested knowledge base."""


@dataclass(frozen=True)
class IndexQuery:
    text: str
    channel: str
    weight: float


@dataclass(frozen=True)
class IndexHit:
    knowledge_base_id: UUID
    child_revision_id: UUID
    source_item_id: str
    field_type: str
    score: float
    dense_score: float
    sparse_score: float
    channel: str
    match_reason: str


class SearchIndexBackend:
    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        queries: Sequence[IndexQuery],
        limit: int,
        collection_name: str | None = None,
    ) -> list[IndexHit]:
        raise NotImplementedError


TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")
FIELD_WEIGHTS = {
    "question": 1.0,
    "question_variant": 0.95,
    "response_content": 0.85,
}
MILVUS_DENSE_WEIGHT: Final = 0.65
MILVUS_SPARSE_WEIGHT: Final = 0.35
MILVUS_OUTPUT_FIELDS: Final = (
    "source_item_id",
    "child_id",
    "child_revision_id",
    "field_type",
    "field_text",
    "embedding_model",
)


@dataclass(frozen=True)
class _ArtifactFragment:
    source_item_id: str
    child_revision_id: UUID
    field_type: str
    field_text: str
    dense_vector: list[float]
    sparse_terms: dict[str, float]


def _tokens(value: str) -> list[str]:
    return TOKEN_PATTERN.findall(value.casefold())


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(
        0.0,
        min(
            1.0,
            sum(a * b for a, b in zip(left, right, strict=True))
            / (left_norm * right_norm),
        ),
    )


def _bm25_scores(query: str, fragments: Sequence[_ArtifactFragment]) -> list[float]:
    query_terms = Counter(_tokens(query))
    if not query_terms or not fragments:
        return [0.0] * len(fragments)
    document_frequency: Counter[str] = Counter()
    lengths: list[float] = []
    for fragment in fragments:
        terms = fragment.sparse_terms
        document_frequency.update(terms.keys())
        lengths.append(sum(terms.values()))
    average_length = sum(lengths) / max(len(lengths), 1)
    scores: list[float] = []
    for fragment, length in zip(fragments, lengths, strict=True):
        score = 0.0
        for term, query_frequency in query_terms.items():
            frequency = fragment.sparse_terms.get(term, 0.0)
            if frequency <= 0:
                continue
            document_count = document_frequency.get(term, 0)
            inverse_document_frequency = math.log(
                1.0 + (len(fragments) - document_count + 0.5) / (document_count + 0.5)
            )
            denominator = frequency + 1.5 * (
                1.0 - 0.75 + 0.75 * length / max(average_length, 1.0)
            )
            score += inverse_document_frequency * (
                frequency * 2.5 / max(denominator, 1e-9)
            ) * min(query_frequency, 2)
        scores.append(score)
    return [score / (score + 1.0) for score in scores]


class LocalArtifactSearchBackend(SearchIndexBackend):
    """Hybrid dense/BM25 search over worker-produced JSON artifacts.

    This is intentionally a deterministic offline implementation of the same
    fragment contract used by Milvus. It provides a faithful local test path
    while production can swap in a Milvus search implementation without changing
    PostgreSQL publication filtering or API result grouping.
    """

    def __init__(
        self,
        artifact_dir: Path,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        reranker: RerankerProvider | None = None,
        rerank_limit: int = 24,
    ) -> None:
        if rerank_limit <= 0:
            raise ValueError("rerank_limit must be positive")
        self.artifact_dir = artifact_dir
        self.embedding_provider = embedding_provider or DeterministicEmbeddingProvider(
            dimension=DEFAULT_EMBEDDING_DIMENSION
        )
        self.reranker = reranker
        self.rerank_limit = rerank_limit

    def _load_fragments(self, knowledge_base_id: UUID) -> list[_ArtifactFragment]:
        directory = self.artifact_dir / str(knowledge_base_id)
        paths = sorted(directory.glob("*.json"))
        if not paths:
            raise SearchIndexUnavailableError(knowledge_base_id)
        fragments: list[_ArtifactFragment] = []
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            raw_fragments = payload.get("fragments") if isinstance(payload, dict) else None
            if not isinstance(raw_fragments, list):
                continue
            artifact_model = payload.get("embedding_model") if isinstance(payload, dict) else None
            artifact_dimension = (
                payload.get("embedding_dimension") if isinstance(payload, dict) else None
            )
            if (
                artifact_model is not None
                and artifact_model != self.embedding_provider.model_name
            ):
                continue
            if (
                artifact_dimension is not None
                and artifact_dimension != self.embedding_provider.dimension
            ):
                continue
            for raw in raw_fragments:
                if not isinstance(raw, dict):
                    continue
                try:
                    revision_id = UUID(str(raw["child_revision_id"]))
                    source_item_id = str(raw["source_item_id"])
                    field_type = str(raw["field_type"])
                    field_text = str(raw["field_text"])
                    dense_vector = [float(value) for value in raw["dense_vector"]]
                    sparse_terms = {
                        str(term): float(value)
                        for term, value in dict(raw["sparse_terms"]).items()
                    }
                except (KeyError, TypeError, ValueError):
                    continue
                if len(dense_vector) != self.embedding_provider.dimension:
                    continue
                if field_type not in FIELD_WEIGHTS:
                    continue
                fragments.append(
                    _ArtifactFragment(
                        source_item_id=source_item_id,
                        child_revision_id=revision_id,
                        field_type=field_type,
                        field_text=field_text,
                        dense_vector=dense_vector,
                        sparse_terms=sparse_terms,
                    )
                )
        if not fragments:
            raise SearchIndexUnavailableError(knowledge_base_id)
        return fragments

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        queries: Sequence[IndexQuery],
        limit: int,
        collection_name: str | None = None,
    ) -> list[IndexHit]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        fragments = self._load_fragments(knowledge_base_id)
        active_queries = [query for query in queries if query.text.strip() and query.weight > 0]
        if not active_queries:
            return []
        query_vectors = await self.embedding_provider.embed_texts(
            [query.text for query in active_queries]
        )
        if len(query_vectors) != len(active_queries):
            raise SearchIndexUnavailableError(knowledge_base_id)

        best_hits: dict[str, IndexHit] = {}
        for query, query_vector in zip(active_queries, query_vectors, strict=True):
            sparse_scores = _bm25_scores(query.text, fragments)
            ranked: list[tuple[int, float, float, float]] = []
            for index, (fragment, sparse_score) in enumerate(
                zip(fragments, sparse_scores, strict=True)
            ):
                dense_score = _cosine_similarity(query_vector, fragment.dense_vector)
                hybrid_score = query.weight * FIELD_WEIGHTS[fragment.field_type] * (
                    0.65 * dense_score + 0.35 * sparse_score
                )
                if hybrid_score > 0:
                    ranked.append((index, hybrid_score, dense_score, sparse_score))
            ranked.sort(key=lambda item: item[1], reverse=True)
            ranked = ranked[: self.rerank_limit]

            rerank_scores: list[float] | None = None
            if self.reranker is not None and ranked:
                documents = [fragments[index].field_text for index, *_scores in ranked]
                rerank_scores = await self.reranker.rerank(query.text, documents)
                if len(rerank_scores) != len(ranked):
                    raise SearchIndexUnavailableError(knowledge_base_id)

            for position, (index, hybrid_score, dense_score, sparse_score) in enumerate(ranked):
                fragment = fragments[index]
                score = hybrid_score
                reason = "hybrid_dense_bm25"
                if rerank_scores is not None:
                    rerank_score = max(0.0, min(1.0, rerank_scores[position]))
                    score = 0.7 * hybrid_score + 0.3 * query.weight * rerank_score
                    reason = "hybrid_dense_bm25_reranked"
                hit = IndexHit(
                    knowledge_base_id=knowledge_base_id,
                    child_revision_id=fragment.child_revision_id,
                    source_item_id=fragment.source_item_id,
                    field_type=fragment.field_type,
                    score=score,
                    dense_score=dense_score,
                    sparse_score=sparse_score,
                    channel=query.channel,
                    match_reason=reason,
                )
                previous = best_hits.get(fragment.source_item_id)
                if previous is None or hit.score > previous.score:
                    best_hits[fragment.source_item_id] = hit
        return sorted(best_hits.values(), key=lambda hit: hit.score, reverse=True)[:limit]


class MilvusHybridSearcher:
    async def hybrid_search(
        self,
        *,
        collection_name: str,
        queries: Sequence[dict[str, object]],
        limit: int,
    ) -> list[dict[str, object]]:
        raise NotImplementedError


def _create_milvus_client(base_url: str, token: str | None) -> Any:
    try:
        from pymilvus import MilvusClient
    except ImportError as exc:  # pragma: no cover - dependency is installed in production.
        raise RuntimeError("pymilvus is required for Milvus search") from exc

    arguments: dict[str, object] = {"uri": base_url}
    if token:
        arguments["token"] = token
    return MilvusClient(**arguments)


def _create_ann_search_request(
    *,
    data: list[object],
    anns_field: str,
    param: dict[str, object],
    limit: int,
    expr: str | None = None,
) -> Any:
    try:
        from pymilvus import AnnSearchRequest
    except ImportError as exc:  # pragma: no cover - dependency is installed in production.
        raise RuntimeError("pymilvus is required for Milvus search") from exc
    arguments: dict[str, object] = {
        "data": data,
        "anns_field": anns_field,
        "param": param,
        "limit": limit,
    }
    if expr:
        arguments["expr"] = expr
    return AnnSearchRequest(**arguments)


def _create_weighted_ranker(dense_weight: float, sparse_weight: float) -> Any:
    try:
        from pymilvus import WeightedRanker
    except ImportError as exc:  # pragma: no cover - dependency is installed in production.
        raise RuntimeError("pymilvus is required for Milvus search") from exc
    return WeightedRanker(dense_weight, sparse_weight)


def _milvus_hit_value(hit: object, key: str, default: object = None) -> object:
    """Read a PyMilvus hit through its mapping protocol before attributes.

    ``pymilvus`` returns ``Hit`` objects that expose result fields through
    ``hit["..."]``. The ``entity`` attribute on those objects can be a wrapper
    containing another ``entity`` key, while ``hit["entity"]`` is the actual
    entity payload. Attribute-first access therefore loses
    ``child_revision_id`` and silently drops every result.
    """

    if isinstance(hit, Mapping):
        return hit.get(key, default)
    try:
        return hit[key]  # type: ignore[index]
    except (AttributeError, IndexError, KeyError, TypeError):
        pass
    return getattr(hit, key, default)


def _milvus_entity(hit: object) -> dict[str, object]:
    """Return the actual entity dictionary from dicts and PyMilvus ``Hit``s."""

    raw_entity = _milvus_hit_value(hit, "entity", {})
    if not isinstance(raw_entity, Mapping):
        return {}
    entity = dict(raw_entity)
    nested_entity = entity.get("entity")
    if isinstance(nested_entity, Mapping):
        outer_entity = entity
        entity = dict(nested_entity)
        for key in (
            "source_item_id",
            "child_id",
            "child_revision_id",
            "field_type",
            "field_text",
            "embedding_model",
        ):
            if key not in entity and key in outer_entity:
                entity[key] = outer_entity[key]
    return entity


def _milvus_filter_literal(value: str) -> str:
    """Escape a string for the Milvus expression literal used below."""

    return value.replace("\\", "\\\\").replace('"', '\\"')


def _milvus_model_filter(model_name: str) -> str | None:
    normalized = model_name.strip()
    if not normalized:
        return None
    return f'embedding_model == "{_milvus_filter_literal(normalized)}"'


def _flatten_milvus_hits(results: object) -> list[object]:
    """Normalize MilvusClient's one-query nested result shape for the app contract."""

    if hasattr(results, "to_list"):
        results = results.to_list()
    if not isinstance(results, list):
        return []
    if len(results) == 1 and isinstance(results[0], list):
        return results[0]
    return results


class MilvusClientHybridSearcher:
    """Official PyMilvus hybrid search adapter.

    ``MilvusClient.hybrid_search`` is synchronous, so the blocking SDK call is
    isolated in a worker thread. One application query becomes one hybrid call
    containing exactly one dense and one BM25 ``AnnSearchRequest``. This keeps
    text and OCR channels independent while satisfying Milvus's one-vector and
    one-ranker constraints.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        client: Any | None = None,
        client_factory: Callable[[str, str | None], Any] | None = None,
        ann_search_request_factory: Callable[..., Any] | None = None,
        ranker_factory: Callable[[float, float], Any] | None = None,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        if not normalized_url:
            raise ValueError("base_url must not be empty")
        self.base_url = normalized_url
        self.token = token
        if client is not None and client_factory is not None:
            raise ValueError("client and client_factory cannot both be provided")
        self._client = (
            client
            if client is not None
            else (client_factory or _create_milvus_client)(self.base_url, self.token)
        )
        self._ann_search_request_factory = ann_search_request_factory or _create_ann_search_request
        self._ranker_factory = ranker_factory or _create_weighted_ranker

    async def hybrid_search(
        self,
        *,
        collection_name: str,
        queries: Sequence[dict[str, object]],
        limit: int,
    ) -> list[dict[str, object]]:
        if not collection_name or not queries or limit <= 0:
            return []
        return await asyncio.to_thread(
            self._hybrid_search_sync,
            collection_name,
            list(queries),
            limit,
        )

    def _hybrid_search_sync(
        self,
        collection_name: str,
        queries: list[dict[str, object]],
        limit: int,
    ) -> list[dict[str, object]]:
        try:
            self._client.load_collection(collection_name=collection_name)
            results: list[dict[str, object]] = []
            for query_index, query in enumerate(queries):
                text = str(query.get("text", "")).strip()
                vector = query.get("dense_vector")
                if not text or not isinstance(vector, list) or not vector:
                    continue
                try:
                    query_weight = float(query.get("weight", 1.0))
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(query_weight) or query_weight <= 0:
                    continue

                model_filter = _milvus_model_filter(
                    str(query.get("embedding_model", ""))
                )
                dense_request = self._ann_search_request_factory(
                    data=[vector],
                    anns_field="dense_vector",
                    param={"metric_type": "COSINE"},
                    limit=limit,
                    expr=model_filter,
                )
                sparse_request = self._ann_search_request_factory(
                    data=[text],
                    anns_field="sparse_vector",
                    param={"metric_type": "BM25"},
                    limit=limit,
                    expr=model_filter,
                )
                ranker = self._ranker_factory(
                    MILVUS_DENSE_WEIGHT,
                    MILVUS_SPARSE_WEIGHT,
                )
                match_reason = "hybrid_dense_bm25"
                try:
                    raw_results = self._client.hybrid_search(
                        collection_name=collection_name,
                        reqs=[dense_request, sparse_request],
                        ranker=ranker,
                        limit=limit,
                        output_fields=list(MILVUS_OUTPUT_FIELDS),
                    )
                    if not _flatten_milvus_hits(raw_results):
                        raise RuntimeError("Milvus hybrid search returned no hits")
                except Exception:
                    # Milvus 3.0.0 currently raises ``unsupported ID type``
                    # when the BM25 branch has no matches. Dense retrieval is
                    # still valid in that case and must not be replaced by a
                    # parent-keyword fallback.
                    raw_results = self._client.search(
                        collection_name=collection_name,
                        data=[vector],
                        anns_field="dense_vector",
                        filter=model_filter or "",
                        limit=limit,
                        output_fields=list(MILVUS_OUTPUT_FIELDS),
                        search_params={"metric_type": "COSINE"},
                    )
                    match_reason = "dense_fallback"
                channel = str(query.get("channel", "text"))
                for hit in _flatten_milvus_hits(raw_results):
                    entity = _milvus_entity(hit)
                    source_item_id = (
                        entity.get("source_item_id")
                        or _milvus_hit_value(hit, "source_item_id")
                        or _milvus_hit_value(hit, "id")
                    )
                    revision_id = entity.get("child_revision_id") or _milvus_hit_value(
                        hit,
                        "child_revision_id",
                    )
                    field_type = entity.get("field_type") or _milvus_hit_value(
                        hit,
                        "field_type",
                        "response_content",
                    )
                    if field_type is not None:
                        entity["field_type"] = str(field_type)
                    score = _milvus_hit_value(
                        hit,
                        "distance",
                        _milvus_hit_value(hit, "score", entity.get("score", 0.0)),
                    )
                    if source_item_id is None or revision_id is None:
                        continue
                    try:
                        numeric_score = float(score)
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(numeric_score):
                        continue
                    entity["source_item_id"] = str(source_item_id)
                    results.append(
                        {
                            "entity": entity,
                            "score": numeric_score,
                            "channel": channel,
                            "query_index": query_index,
                            "query_weight": query_weight,
                            "match_reason": match_reason,
                        }
                    )
            return results
        except Exception as exc:
            raise SearchIndexUnavailableError(collection_name) from exc


class MilvusSearchBackend(SearchIndexBackend):
    """Query the current Milvus collection, leaving publication filtering to PostgreSQL."""

    def __init__(
        self,
        *,
        searcher: MilvusHybridSearcher,
        embedding_provider: EmbeddingProvider,
        reranker: RerankerProvider | None = None,
        rerank_limit: int = 24,
    ) -> None:
        if embedding_provider.dimension != DEFAULT_EMBEDDING_DIMENSION:
            raise ValueError(f"embedding dimension must be exactly {DEFAULT_EMBEDDING_DIMENSION}")
        if rerank_limit <= 0:
            raise ValueError("rerank_limit must be positive")
        self.searcher = searcher
        self.embedding_provider = embedding_provider
        self.reranker = reranker
        self.rerank_limit = rerank_limit

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        queries: Sequence[IndexQuery],
        limit: int,
        collection_name: str | None = None,
    ) -> list[IndexHit]:
        if not collection_name:
            raise SearchIndexUnavailableError(knowledge_base_id)
        active_queries = [query for query in queries if query.text.strip() and query.weight > 0]
        if not active_queries:
            return []
        vectors = await self.embedding_provider.embed_texts(
            [query.text for query in active_queries]
        )
        search_queries = [
            {
                "text": query.text,
                "channel": query.channel,
                "weight": query.weight,
                "dense_vector": vector,
                "embedding_model": self.embedding_provider.model_name,
            }
            for query, vector in zip(active_queries, vectors, strict=True)
        ]
        raw_hits = await self.searcher.hybrid_search(
            collection_name=collection_name,
            queries=search_queries,
            limit=max(limit, self.rerank_limit) if self.reranker is not None else limit,
        )
        hits: list[IndexHit] = []
        rerank_candidates: dict[int, list[tuple[IndexHit, str]]] = {}
        for raw in raw_hits:
            entity = raw.get("entity") if isinstance(raw.get("entity"), dict) else raw
            try:
                revision_id = UUID(str(entity["child_revision_id"]))
                source_item_id = str(entity["source_item_id"])
                field_type = str(entity.get("field_type", "response_content"))
                score = float(raw.get("score", raw.get("distance", entity.get("score", 0.0))))
                dense_score = float(raw.get("dense_score", entity.get("dense_score", score)))
                sparse_score = float(raw.get("sparse_score", entity.get("sparse_score", 0.0)))
                channel = str(raw.get("channel", entity.get("channel", "text")))
                query_weight = float(raw.get("query_weight", 1.0))
                match_reason = str(
                    raw.get("match_reason", entity.get("match_reason", "hybrid_dense_bm25"))
                )
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(score) or not math.isfinite(query_weight):
                continue
            score *= max(query_weight, 0.0) * FIELD_WEIGHTS.get(field_type, 1.0)
            if not math.isfinite(score):
                continue
            hit = IndexHit(
                knowledge_base_id=knowledge_base_id,
                child_revision_id=revision_id,
                source_item_id=source_item_id,
                field_type=field_type,
                score=max(0.0, min(1.0, score)),
                dense_score=max(0.0, min(1.0, dense_score)),
                sparse_score=max(0.0, min(1.0, sparse_score)),
                channel=channel,
                match_reason=match_reason,
            )
            document = str(entity.get("field_text", "")).strip()
            raw_query_index = raw.get("query_index")
            if (
                isinstance(raw_query_index, int)
                and 0 <= raw_query_index < len(active_queries)
            ):
                query_index = raw_query_index
            elif len(active_queries) == 1:
                query_index = 0
            else:
                query_index = None
            if self.reranker is not None and query_index is not None and document:
                rerank_candidates.setdefault(query_index, []).append((hit, document))
            else:
                hits.append(hit)

        if self.reranker is not None:
            for query_index, candidates in rerank_candidates.items():
                reranked_candidates = candidates[: self.rerank_limit]
                hits.extend(hit for hit, _document in candidates[self.rerank_limit :])
                rerank_scores = await self.reranker.rerank(
                    active_queries[query_index].text,
                    [document for _hit, document in reranked_candidates],
                )
                if len(rerank_scores) != len(reranked_candidates):
                    raise SearchIndexUnavailableError(knowledge_base_id)
                query_weight = active_queries[query_index].weight
                for (hit, _document), rerank_score in zip(
                    reranked_candidates,
                    rerank_scores,
                    strict=True,
                ):
                    normalized_rerank_score = max(0.0, min(1.0, rerank_score))
                    hits.append(
                        replace(
                            hit,
                            score=max(
                                0.0,
                                min(
                                    1.0,
                                    0.7 * hit.score
                                    + 0.3 * query_weight * normalized_rerank_score,
                                ),
                            ),
                            match_reason=f"{hit.match_reason}_reranked",
                        )
                    )
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit]


def create_search_index_backend(settings: Settings) -> SearchIndexBackend:
    reranker = None
    if settings.reranker_service_url:
        reranker = QwenRerankerProvider(
            settings.reranker_service_url,
            api_key=settings.reranker_api_key,
            model_name=settings.reranker_model,
            timeout_seconds=settings.reranker_timeout_seconds,
        )
    if settings.index_backend_mode == "local_artifact":
        return LocalArtifactSearchBackend(
            settings.index_artifact_dir,
            reranker=reranker,
        )
    if settings.embedding_service_url is None or settings.milvus_url is None:
        raise RuntimeError("Milvus search requires embedding and Milvus service URLs")
    from app.services.embedding import QwenEmbeddingProvider

    return MilvusSearchBackend(
        searcher=MilvusClientHybridSearcher(
            settings.milvus_url,
            token=settings.milvus_token,
        ),
        embedding_provider=QwenEmbeddingProvider(
            settings.embedding_service_url,
            api_key=settings.embedding_api_key,
            model_name=settings.embedding_model,
            dimension=settings.embedding_dimension,
            timeout_seconds=settings.embedding_timeout_seconds,
        ),
        reranker=reranker,
    )
