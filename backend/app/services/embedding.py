from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_RERANKER_MODEL = "Qwen/Qwen3-Reranker-0.6B"
DEFAULT_EMBEDDING_DIMENSION = 1024


class EmbeddingProviderError(RuntimeError):
    """The configured embedding service returned an invalid or failed response."""


class RerankerProviderError(RuntimeError):
    """The configured reranker service returned an invalid or failed response."""


class EmbeddingProvider(Protocol):
    model_name: str
    dimension: int

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class RerankerProvider(Protocol):
    model_name: str

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        ...


def deterministic_hash_vector(
    value: str,
    *,
    dimension: int = DEFAULT_EMBEDDING_DIMENSION,
) -> list[float]:
    """Create a reproducible unit vector for offline development and contract tests."""

    if dimension <= 0:
        raise ValueError("dimension must be positive")
    raw_values: list[float] = []
    encoded = value.encode("utf-8")
    for index in range(dimension):
        digest = hashlib.sha256(index.to_bytes(4, "big") + encoded).digest()
        integer = int.from_bytes(digest[:8], "big", signed=False)
        raw_values.append((integer / 2**63) - 1.0)
    norm = math.sqrt(sum(item * item for item in raw_values))
    if norm == 0:
        return [0.0] * dimension
    return [round(item / norm, 8) for item in raw_values]


@dataclass(frozen=True)
class DeterministicEmbeddingProvider:
    """Offline embedding provider with the same 1024-dimensional contract as Qwen."""

    model_name: str = "deterministic-hash-v1"
    dimension: int = DEFAULT_EMBEDDING_DIMENSION

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [
            deterministic_hash_vector(text, dimension=self.dimension)
            for text in texts
        ]


class QwenEmbeddingProvider:
    """Call an OpenAI-compatible local Qwen embedding service over HTTP.

    The service is intentionally external to the API and worker processes. This
    keeps CUDA/MPS dependencies out of the business image and allows the same
    contract to target a host-side MPS server or a production CUDA container.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        dimension: int = DEFAULT_EMBEDDING_DIMENSION,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        if not normalized_url:
            raise ValueError("base_url must not be empty")
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = normalized_url
        self.api_key = api_key
        self.model_name = model_name
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        values = list(texts)
        if not values:
            return []
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        request_payload = {
            "model": self.model_name,
            "input": values,
            "encoding_format": "float",
        }
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            try:
                response = await client.post(
                    f"{self.base_url}/embeddings",
                    json=request_payload,
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise EmbeddingProviderError("embedding service request failed") from exc
        finally:
            if owns_client:
                await client.aclose()

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(values):
            raise EmbeddingProviderError("embedding service returned an unexpected item count")
        ordered = sorted(
            data,
            key=lambda item: item.get("index", 0) if isinstance(item, dict) else 0,
        )
        vectors: list[list[float]] = []
        for item in ordered:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or len(vector) != self.dimension:
                raise EmbeddingProviderError(
                    f"embedding dimension must be exactly {self.dimension}"
                )
            try:
                normalized = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise EmbeddingProviderError("embedding contains a non-numeric value") from exc
            if not all(math.isfinite(value) for value in normalized):
                raise EmbeddingProviderError("embedding contains a non-finite value")
            vectors.append(normalized)
        return vectors


class QwenRerankerProvider:
    """Call a local Qwen reranker using a small, stable JSON protocol."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        model_name: str = DEFAULT_RERANKER_MODEL,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        if not normalized_url:
            raise ValueError("base_url must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = normalized_url
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        values = list(documents)
        if not values:
            return []
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        request_payload = {
            "model": self.model_name,
            "query": query,
            "documents": values,
        }
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            try:
                response = await client.post(
                    f"{self.base_url}/rerank",
                    json=request_payload,
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise RerankerProviderError("reranker service request failed") from exc
        finally:
            if owns_client:
                await client.aclose()

        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if raw_results is None and isinstance(payload, dict):
            raw_results = payload.get("data")
        if not isinstance(raw_results, list) or len(raw_results) != len(values):
            raise RerankerProviderError("reranker service returned an unexpected item count")
        scores = [0.0] * len(values)
        for position, item in enumerate(raw_results):
            if not isinstance(item, dict):
                raise RerankerProviderError("reranker result must be an object")
            index = item.get("index", position)
            score = item.get("relevance_score", item.get("score"))
            if not isinstance(index, int) or index < 0 or index >= len(values):
                raise RerankerProviderError("reranker returned an invalid document index")
            try:
                numeric_score = float(score)
            except (TypeError, ValueError) as exc:
                raise RerankerProviderError("reranker score is not numeric") from exc
            if not math.isfinite(numeric_score):
                raise RerankerProviderError("reranker score is not finite")
            scores[index] = numeric_score
        return scores
