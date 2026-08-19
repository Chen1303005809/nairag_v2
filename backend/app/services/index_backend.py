from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_content import (
    ChildRevision,
    ChildRevisionQuestionVariant,
    IndexJob,
)

VECTOR_DIMENSION: Final = 1024
RESPONSE_CHUNK_SIZE: Final = 1_200
RESPONSE_CHUNK_OVERLAP: Final = 120
SOURCE_ID_NAMESPACE: Final = UUID("2b41df37-dfbb-5e08-a4c6-b43fb0ff4f5a")
TOKEN_PATTERN: Final = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")


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


def deterministic_hash_vector(value: str, *, dimension: int = VECTOR_DIMENSION) -> list[float]:
    """Create a reproducible local vector for development and contract tests.

    This is intentionally not presented as a semantic embedding model. Production
    deployments replace this function through the same fragment contract with the
    pinned Qwen embedding service.
    """

    raw_values: list[float] = []
    encoded = value.encode("utf-8")
    for index in range(dimension):
        digest = hashlib.sha256(index.to_bytes(4, "big") + encoded).digest()
        integer = int.from_bytes(digest[:8], "big", signed=False)
        raw_values.append((integer / 2**63) - 1.0)
    norm = sum(item * item for item in raw_values) ** 0.5
    if norm == 0:
        return [0.0] * dimension
    return [round(item / norm, 8) for item in raw_values]


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
        dense_vector=deterministic_hash_vector(normalized),
        sparse_terms=_sparse_terms(normalized),
        content_hash=content_hash,
    )


async def build_index_fragments(
    session: AsyncSession,
    *,
    child_revision_id: UUID,
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
    fragments = [
        _fragment(
            revision_id=revision.id,
            field_type="question",
            field_text=revision.question,
            ordinal=0,
        )
    ]
    fragments.extend(
        _fragment(
            revision_id=revision.id,
            field_type="question_variant",
            field_text=variant.question_text,
            ordinal=index,
        )
        for index, variant in enumerate(variants)
    )
    fragments.extend(
        _fragment(
            revision_id=revision.id,
            field_type="response_content",
            field_text=chunk,
            ordinal=index,
        )
        for index, chunk in enumerate(_response_chunks(revision.response_content))
    )
    return fragments


class LocalArtifactIndexBackend:
    """Materialize deterministic index fragments as rebuildable JSON artifacts.

    The artifact is a local development backend and an integration seam for a
    later Milvus writer. It is never used as the publication source of truth.
    """

    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir

    async def index_target(self, session: AsyncSession, job: IndexJob) -> None:
        if job.child_revision_id is None or job.knowledge_base_id is None:
            raise ValueError("index job is missing target fields")
        fragments = await build_index_fragments(
            session,
            child_revision_id=job.child_revision_id,
        )
        if not fragments:
            raise ValueError("child revision has no indexable content")

        payload = {
            "schema_version": 1,
            "job_id": str(job.id),
            "knowledge_base_id": str(job.knowledge_base_id),
            "child_id": str(job.child_id) if job.child_id else None,
            "child_revision_id": str(job.child_revision_id),
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
