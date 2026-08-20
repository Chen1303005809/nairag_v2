from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Protocol
from uuid import UUID, uuid5

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_content import (
    ChildRevision,
    ChildRevisionQuestionVariant,
    IndexJob,
)
from app.services.embedding import (
    DEFAULT_EMBEDDING_DIMENSION,
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
)
from app.services.embedding import (
    deterministic_hash_vector as _deterministic_hash_vector,
)

VECTOR_DIMENSION: Final = DEFAULT_EMBEDDING_DIMENSION
RESPONSE_CHUNK_SIZE: Final = 1_200
RESPONSE_CHUNK_OVERLAP: Final = 120
SOURCE_ID_NAMESPACE: Final = UUID("2b41df37-dfbb-5e08-a4c6-b43fb0ff4f5a")
TOKEN_PATTERN: Final = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")


def deterministic_hash_vector(value: str, *, dimension: int = VECTOR_DIMENSION) -> list[float]:
    """Backward-compatible export for the offline embedding contract."""

    return _deterministic_hash_vector(value, dimension=dimension)


@dataclass(frozen=True)
class IndexFragment:
    source_item_id: str
    child_revision_id: str
    field_type: str
    field_text: str
    ordinal: int
    dense_vector: list[float]
    sparse_terms: dict[str, int]
    content_hash: str
    embedding_model: str


def stable_source_item_id(
    *,
    child_revision_id: UUID,
    field_type: str,
    ordinal: int,
    field_text: str,
) -> UUID:
    content_hash = hashlib.sha256(field_text.encode("utf-8")).hexdigest()
    return uuid5(
        SOURCE_ID_NAMESPACE,
        f"{child_revision_id}:{field_type}:{ordinal}:{content_hash}",
    )


def _tokens(value: str) -> list[str]:
    return TOKEN_PATTERN.findall(value.casefold())


def _sparse_terms(value: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in _tokens(value):
        counts[token] = counts.get(token, 0) + 1
    return counts


def _response_chunks(value: str) -> list[str]:
    if len(value) <= RESPONSE_CHUNK_SIZE:
        return [value]
    chunks: list[str] = []
    start = 0
    step = RESPONSE_CHUNK_SIZE - RESPONSE_CHUNK_OVERLAP
    while start < len(value):
        chunk = value[start : start + RESPONSE_CHUNK_SIZE]
        if chunk:
            chunks.append(chunk)
        if start + RESPONSE_CHUNK_SIZE >= len(value):
            break
        start += step
    return chunks


def _fragment(
    *,
    revision_id: UUID,
    field_type: str,
    field_text: str,
    ordinal: int,
    dense_vector: list[float],
    embedding_model: str,
) -> IndexFragment:
    normalized = field_text.strip()
    content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return IndexFragment(
        source_item_id=str(
            stable_source_item_id(
                child_revision_id=revision_id,
                field_type=field_type,
                ordinal=ordinal,
                field_text=normalized,
            )
        ),
        child_revision_id=str(revision_id),
        field_type=field_type,
        field_text=normalized,
        ordinal=ordinal,
        dense_vector=dense_vector,
        sparse_terms=_sparse_terms(normalized),
        content_hash=content_hash,
        embedding_model=embedding_model,
    )


async def build_index_fragments(
    session: AsyncSession,
    *,
    child_revision_id: UUID,
    embedding_provider: EmbeddingProvider | None = None,
) -> list[IndexFragment]:
    revision = await session.get(ChildRevision, child_revision_id)
    if revision is None:
        return []
    variants = list(
        (
            await session.scalars(
                select(ChildRevisionQuestionVariant)
                .where(ChildRevisionQuestionVariant.child_revision_id == child_revision_id)
                .order_by(ChildRevisionQuestionVariant.sort_order)
            )
        ).all()
    )
    fragment_inputs: list[tuple[str, str, int]] = [("question", revision.question, 0)]
    fragment_inputs.extend(
        ("question_variant", variant.question_text, index)
        for index, variant in enumerate(variants)
    )
    fragment_inputs.extend(
        ("response_content", chunk, index)
        for index, chunk in enumerate(_response_chunks(revision.response_content))
    )
    provider = embedding_provider or DeterministicEmbeddingProvider(dimension=VECTOR_DIMENSION)
    texts = [text.strip() for _field_type, text, _ordinal in fragment_inputs]
    vectors = await provider.embed_texts(texts)
    if len(vectors) != len(fragment_inputs):
        raise ValueError("embedding provider returned an unexpected vector count")
    fragments: list[IndexFragment] = []
    for (field_type, field_text, ordinal), vector in zip(fragment_inputs, vectors, strict=True):
        if len(vector) != provider.dimension:
            raise ValueError(f"embedding dimension must be exactly {provider.dimension}")
        fragments.append(
            _fragment(
                revision_id=revision.id,
                field_type=field_type,
                field_text=field_text,
                ordinal=ordinal,
                dense_vector=vector,
                embedding_model=provider.model_name,
            )
        )
    return fragments


class LocalArtifactIndexBackend:
    """Materialize deterministic index fragments as rebuildable JSON artifacts.

    The artifact is a local development backend and an integration seam for a
    later Milvus writer. It is never used as the publication source of truth.
    """

    def __init__(
        self,
        artifact_dir: Path,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.artifact_dir = artifact_dir
        self.embedding_provider = embedding_provider or DeterministicEmbeddingProvider(
            dimension=VECTOR_DIMENSION
        )

    async def index_target(self, session: AsyncSession, job: IndexJob) -> None:
        if job.child_revision_id is None or job.knowledge_base_id is None:
            raise ValueError("index job is missing target fields")
        fragments = await build_index_fragments(
            session,
            child_revision_id=job.child_revision_id,
            embedding_provider=self.embedding_provider,
        )
        if not fragments:
            raise ValueError("child revision has no indexable content")

        payload = {
            "schema_version": 1,
            "job_id": str(job.id),
            "knowledge_base_id": str(job.knowledge_base_id),
            "child_id": str(job.child_id) if job.child_id else None,
            "child_revision_id": str(job.child_revision_id),
            "embedding_model": self.embedding_provider.model_name,
            "embedding_dimension": self.embedding_provider.dimension,
            "fragments": [asdict(fragment) for fragment in fragments],
        }
        destination = (
            self.artifact_dir
            / str(job.knowledge_base_id)
            / f"{job.child_revision_id}.json"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            if temporary_path is None:  # pragma: no cover - NamedTemporaryFile always sets it.
                raise RuntimeError("failed to create index artifact temporary file")
            temporary_path.replace(destination)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    async def clean_publication(self, session: AsyncSession, job: IndexJob) -> None:
        if job.knowledge_base_id is None or job.child_id is None:
            raise ValueError("cleanup job is missing publication fields")
        knowledge_base_directory = self.artifact_dir / str(job.knowledge_base_id)
        if not knowledge_base_directory.is_dir():
            return
        for artifact_path in knowledge_base_directory.glob("*.json"):
            try:
                payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A malformed artifact is not proof that it belongs to this
                # publication, so leave it intact for diagnostics.
                continue
            if payload.get("child_id") == str(job.child_id):
                artifact_path.unlink(missing_ok=True)


class MilvusWriter(Protocol):
    async def upsert(
        self,
        *,
        collection_name: str,
        rows: Sequence[dict[str, object]],
    ) -> None:
        ...

    async def delete(
        self,
        *,
        collection_name: str,
        filter_expression: str,
    ) -> None:
        ...


class MilvusHttpWriter:
    """Write rows through Milvus REST v2's idempotent upsert endpoint.

    The target collection is created and indexed by deployment tooling with a
    VARCHAR ``source_item_id`` primary key, a 1024-dimension ``dense_vector``,
    the metadata fields in ``_milvus_rows`` below, and a BM25 function over
    ``field_text``. Upsert makes retries safe without treating Milvus as the
    publication source of truth.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        if not normalized_url:
            raise ValueError("base_url must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = normalized_url
        self.token = token
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def upsert(
        self,
        *,
        collection_name: str,
        rows: Sequence[dict[str, object]],
    ) -> None:
        if not collection_name:
            raise ValueError("collection_name must not be empty")
        if not rows:
            return
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            try:
                response = await client.post(
                    f"{self.base_url}/v2/vectordb/entities/upsert",
                    json={"collectionName": collection_name, "data": list(rows)},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise RuntimeError("Milvus upsert request failed") from exc
        finally:
            if owns_client:
                await client.aclose()
        if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
            raise RuntimeError(f"Milvus upsert failed: {payload.get('message', 'unknown error')}")

    async def delete(
        self,
        *,
        collection_name: str,
        filter_expression: str,
    ) -> None:
        if not collection_name:
            raise ValueError("collection_name must not be empty")
        if not filter_expression:
            raise ValueError("filter_expression must not be empty")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            try:
                response = await client.post(
                    f"{self.base_url}/v2/vectordb/entities/delete",
                    json={"collectionName": collection_name, "filter": filter_expression},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise RuntimeError("Milvus delete request failed") from exc
        finally:
            if owns_client:
                await client.aclose()
        if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
            raise RuntimeError(f"Milvus delete failed: {payload.get('message', 'unknown error')}")


class MilvusIndexBackend:
    """Build Qwen-compatible fragments and upsert them into the current collection."""

    def __init__(
        self,
        *,
        writer: MilvusWriter,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        if embedding_provider.dimension != VECTOR_DIMENSION:
            raise ValueError(f"embedding dimension must be exactly {VECTOR_DIMENSION}")
        self.writer = writer
        self.embedding_provider = embedding_provider

    async def index_target(self, session: AsyncSession, job: IndexJob) -> None:
        if (
            job.child_revision_id is None
            or job.knowledge_base_id is None
            or job.child_id is None
        ):
            raise ValueError("index job is missing target fields")
        knowledge_base = await session.get(KnowledgeBase, job.knowledge_base_id)
        if knowledge_base is None or not knowledge_base.is_active:
            raise ValueError("knowledge base is missing or inactive")
        fragments = await build_index_fragments(
            session,
            child_revision_id=job.child_revision_id,
            embedding_provider=self.embedding_provider,
        )
        if not fragments:
            raise ValueError("child revision has no indexable content")
        rows = [
            {
                "source_item_id": fragment.source_item_id,
                "child_id": str(job.child_id),
                "child_revision_id": fragment.child_revision_id,
                "field_type": fragment.field_type,
                "field_text": fragment.field_text,
                "dense_vector": fragment.dense_vector,
                "sparse_terms": fragment.sparse_terms,
                "content_hash": fragment.content_hash,
                "embedding_model": fragment.embedding_model,
            }
            for fragment in fragments
        ]
        await self.writer.upsert(
            collection_name=knowledge_base.current_physical_collection_name,
            rows=rows,
        )

    async def clean_publication(self, session: AsyncSession, job: IndexJob) -> None:
        if job.knowledge_base_id is None or job.child_id is None:
            raise ValueError("cleanup job is missing publication fields")
        knowledge_base = await session.get(KnowledgeBase, job.knowledge_base_id)
        if knowledge_base is None:
            raise ValueError("knowledge base is missing")
        await self.writer.delete(
            collection_name=knowledge_base.current_physical_collection_name,
            filter_expression=f'child_id == "{job.child_id}"',
        )
