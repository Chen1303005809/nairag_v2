"""Availability-gated access to the independently deployed LightRAG service.

The platform deliberately depends on this module's small interface instead of
on LightRAG's HTTP API.  That keeps optional global material retrieval from
affecting the platform's startup, health endpoint, or core knowledge-base
search when the independent service is absent.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import posixpath
import re
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Protocol

import httpx

from app.core.config import Settings

LOGGER = logging.getLogger(__name__)

LIGHTRAG_QUERY_TOP_K = 60
LIGHTRAG_QUERY_CHUNK_TOP_K = 20
SUPPLEMENTAL_CONTENT_MAX_CHARS = 4_000
_URL_TOKEN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_PATH_TOKEN = re.compile(r"(?:(?:[A-Za-z]:)?[\\/])[^\s,;:]+")


class SupplementalAvailability(str, Enum):
    DISABLED = "disabled"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"
    STALE = "stale"


class SupplementalUpstreamError(RuntimeError):
    """The independent service could not complete an already-dispatched call."""


class SupplementalUnavailableError(RuntimeError):
    """A caller attempted to use an availability-gated supplemental operation."""


@dataclass(frozen=True)
class SupplementalAvailabilitySnapshot:
    state: SupplementalAvailability
    consecutive_health_successes: int = 0

    @property
    def is_available(self) -> bool:
        return self.state is SupplementalAvailability.AVAILABLE


@dataclass(frozen=True)
class SupplementalDocument:
    """A display-safe global material result.

    ``source_hash`` is derived from LightRAG's normalized source path.  The
    raw source path never crosses the service boundary into search persistence
    or API responses.
    """

    source_hash: str
    title: str
    content: str
    citation_metadata: dict[str, object]
    source_score: float
    upstream_rank: int


@dataclass(frozen=True)
class SupplementalMaterial:
    """A sanitized administrative document row returned by LightRAG."""

    document_id: str
    title: str
    status: str | None = None
    progress: float | None = None
    chunks_count: int | None = None
    track_id: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class SupplementalMaterialPage:
    materials: list[SupplementalMaterial]
    total: int
    page: int
    page_size: int


class SupplementalRetriever(Protocol):
    """The only supplemental-search dependency used by business services."""

    def availability_snapshot(self) -> SupplementalAvailabilitySnapshot:
        ...

    async def start(self) -> None:
        ...

    async def aclose(self) -> None:
        ...

    async def retrieve(
        self,
        *,
        query: str | None,
        ocr_text: str | None,
    ) -> list[SupplementalDocument]:
        ...

    async def supported_file_types(self) -> list[str]:
        ...

    async def list_materials(
        self,
        *,
        page: int,
        page_size: int,
        statuses: Sequence[str] | None = None,
    ) -> SupplementalMaterialPage:
        ...

    async def upload_material(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None,
    ) -> str | None:
        ...

    async def delete_material(self, *, document_id: str) -> None:
        ...


def _compact_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    printable = "".join(
        character for character in value if character >= " " and character != "\x7f"
    )
    normalized = " ".join(printable.split())
    if not normalized:
        return None
    return normalized[:limit]


def _safe_filename(value: object) -> str:
    raw = _compact_text(value, limit=1_024)
    if raw is None:
        return "未命名资料"
    normalized = raw.replace("\\", "/")
    name = PurePosixPath(normalized).name
    name = _compact_text(name, limit=255)
    return name or "未命名资料"


def _safe_error_message(value: object) -> str | None:
    """Keep a useful processing error while removing endpoint/path disclosure."""

    message = _compact_text(value, limit=1_000)
    if message is None:
        return None
    message = _URL_TOKEN.sub("[已隐藏地址]", message)
    return _PATH_TOKEN.sub("[已隐藏路径]", message)


def validate_upload_filename(value: str | None) -> str:
    """Accept only a browser-supplied basename, never an upstream file path."""

    if value is None:
        raise ValueError("上传文件缺少文件名")
    filename = _compact_text(value, limit=255)
    if filename is None or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValueError("上传文件名无效")
    return filename


def validate_document_id(value: str) -> str:
    document_id = _compact_text(value, limit=512)
    if document_id is None or "/" in document_id or "\\" in document_id:
        raise ValueError("资料标识无效")
    return document_id


def _normalize_source_identifier(value: object, *, fallback: str) -> str:
    raw = value if isinstance(value, str) else fallback
    normalized = unicodedata.normalize("NFKC", raw).replace("\\", "/").strip()
    normalized = "".join(
        character for character in normalized if character >= " " and character != "\x7f"
    )
    normalized = posixpath.normpath(normalized)
    if normalized in {"", "."}:
        normalized = fallback
    return normalized.casefold()


def _source_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_reference(value: object) -> str | None:
    reference = _compact_text(value, limit=160)
    if reference is None or "/" in reference or "\\" in reference:
        return None
    return reference


def _merge_content(parts: Sequence[str]) -> str:
    unique_parts: list[str] = []
    seen: set[str] = set()
    for value in parts:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique_parts.append(cleaned)
    return "\n\n".join(unique_parts)[:SUPPLEMENTAL_CONTENT_MAX_CHARS]


class LightRagHttpAdapter:
    """Narrow HTTP adapter for the LightRAG 1.5.6 public API.

    There are intentionally no authentication headers here: the service is
    protected by Docker's internal network boundary, as defined by deployment.
    """

    def __init__(
        self,
        base_url: str,
        *,
        health_timeout_seconds: float,
        retrieval_timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url:
            raise ValueError("lightrag base URL must not be empty")
        self._base_url = normalized_base_url
        self._health_timeout_seconds = health_timeout_seconds
        self._retrieval_timeout_seconds = retrieval_timeout_seconds
        self._client = client or httpx.AsyncClient(base_url=normalized_base_url)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health(self) -> bool:
        try:
            response = await self._client.get(
                self._url("/health"),
                timeout=self._health_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return False
        status = payload.get("status") if isinstance(payload, Mapping) else None
        return isinstance(status, str) and status.casefold() in {"healthy", "ok", "success"}

    async def query_data(self, query: str) -> list[SupplementalDocument]:
        payload = await self._request_json(
            "POST",
            "/query/data",
            json={
                "query": query,
                "mode": "mix",
                "enable_rerank": False,
                "top_k": LIGHTRAG_QUERY_TOP_K,
                "chunk_top_k": LIGHTRAG_QUERY_CHUNK_TOP_K,
            },
            timeout=self._retrieval_timeout_seconds,
        )
        data = payload.get("data")
        chunks = data.get("chunks") if isinstance(data, Mapping) else None
        if chunks is None:
            return []
        if not isinstance(chunks, list):
            raise SupplementalUpstreamError("LightRAG query response has invalid chunks")
        return self._group_query_chunks(chunks)

    async def supported_file_types(self) -> list[str]:
        payload = await self._request_json(
            "GET",
            "/documents/supported_file_types",
            timeout=self._retrieval_timeout_seconds,
        )
        raw = payload.get("data", payload.get("supported_file_types", []))
        if isinstance(raw, Mapping):
            raw = raw.get("supported_file_types", raw.get("extensions", []))
        if not isinstance(raw, list):
            return []
        values: list[str] = []
        for item in raw:
            extension = _compact_text(item, limit=32)
            if extension is None:
                continue
            extension = extension.lower().lstrip(".")
            if extension and extension.replace("_", "").replace("-", "").isalnum():
                values.append(extension)
        return sorted(set(values))

    async def list_materials(
        self,
        *,
        page: int,
        page_size: int,
        statuses: Sequence[str] | None,
    ) -> SupplementalMaterialPage:
        request_data: dict[str, object] = {
            "page": page,
            "page_size": page_size,
            "sort_field": "updated_at",
            "sort_direction": "desc",
        }
        if statuses:
            request_data["status_filter"] = list(statuses)
        payload = await self._request_json(
            "POST",
            "/documents/paginated",
            json=request_data,
            timeout=self._retrieval_timeout_seconds,
        )
        data = payload.get("data", payload)
        if not isinstance(data, Mapping):
            raise SupplementalUpstreamError("LightRAG document response is invalid")
        raw_materials = data.get("documents", data.get("items", []))
        if not isinstance(raw_materials, list):
            raise SupplementalUpstreamError("LightRAG document response is invalid")
        materials = [
            material
            for item in raw_materials
            if isinstance(item, Mapping)
            if (material := self._decode_material(item)) is not None
        ]
        pagination = data.get("pagination")
        pagination_total = (
            pagination.get("total", pagination.get("total_count"))
            if isinstance(pagination, Mapping)
            else None
        )
        total = data.get("total", data.get("total_count", pagination_total))
        return SupplementalMaterialPage(
            materials=materials,
            total=total if isinstance(total, int) and total >= 0 else len(materials),
            page=page,
            page_size=page_size,
        )

    async def upload_material(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None,
    ) -> str | None:
        payload = await self._request_json(
            "POST",
            "/documents/upload",
            files={"file": (filename, content, content_type or "application/octet-stream")},
            timeout=self._retrieval_timeout_seconds,
        )
        data = payload.get("data", payload)
        if not isinstance(data, Mapping):
            return None
        return _safe_reference(data.get("track_id", data.get("document_id")))

    async def delete_material(self, *, document_id: str) -> None:
        await self._request_json(
            "DELETE",
            "/documents/delete_document",
            json={"doc_ids": [document_id], "delete_file": True, "delete_llm_cache": False},
            timeout=self._retrieval_timeout_seconds,
        )

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        timeout: float,
        json: dict[str, object] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> dict[str, object]:
        try:
            response = await self._client.request(
                method,
                self._url(path),
                json=json,
                files=files,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SupplementalUpstreamError("LightRAG request failed") from exc
        if not isinstance(payload, dict):
            raise SupplementalUpstreamError("LightRAG returned an invalid response")
        response_status = payload.get("status")
        if isinstance(response_status, str) and response_status.casefold() in {"failed", "error"}:
            raise SupplementalUpstreamError("LightRAG rejected the request")
        return payload

    def _group_query_chunks(self, chunks: list[object]) -> list[SupplementalDocument]:
        grouped: dict[str, dict[str, object]] = {}
        for chunk_order, raw_chunk in enumerate(chunks, start=1):
            if not isinstance(raw_chunk, Mapping):
                continue
            content = raw_chunk.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            reference = _safe_reference(raw_chunk.get("reference_id"))
            normalized_source = _normalize_source_identifier(
                raw_chunk.get("file_path"),
                fallback=f"reference-{reference or chunk_order}",
            )
            source_hash = _source_hash(normalized_source)
            group = grouped.setdefault(
                source_hash,
                {
                    "title": _safe_filename(raw_chunk.get("file_path")),
                    "contents": [],
                    "references": [],
                    "first_rank": chunk_order,
                },
            )
            cast_contents = group["contents"]
            if isinstance(cast_contents, list):
                cast_contents.append(content)
            cast_references = group["references"]
            if reference is not None and isinstance(cast_references, list):
                cast_references.append(reference)

        documents: list[SupplementalDocument] = []
        for source_hash, group in grouped.items():
            contents = group["contents"]
            if not isinstance(contents, list):
                continue
            merged_content = _merge_content([item for item in contents if isinstance(item, str)])
            if not merged_content:
                continue
            first_rank = group["first_rank"]
            rank = first_rank if isinstance(first_rank, int) else len(documents) + 1
            references = group["references"]
            safe_references = (
                list(dict.fromkeys(item for item in references if isinstance(item, str)))[:20]
                if isinstance(references, list)
                else []
            )
            documents.append(
                SupplementalDocument(
                    source_hash=source_hash,
                    title=str(group["title"]),
                    content=merged_content,
                    citation_metadata={
                        "chunk_count": len(contents),
                        "reference_ids": safe_references,
                    },
                    source_score=1.0 / rank,
                    upstream_rank=rank,
                )
            )
        return sorted(documents, key=lambda item: (item.upstream_rank, item.source_hash))

    @staticmethod
    def _decode_material(item: Mapping[str, object]) -> SupplementalMaterial | None:
        document_id = _safe_reference(item.get("id", item.get("doc_id", item.get("document_id"))))
        if document_id is None:
            return None
        progress = item.get("progress")
        if isinstance(progress, int | float):
            progress = float(progress)
            if progress > 1:
                progress /= 100
        else:
            progress = None
        chunks_count = item.get("chunks_count", item.get("chunk_count"))
        if not isinstance(chunks_count, int):
            chunks_count = None
        return SupplementalMaterial(
            document_id=document_id,
            title=_safe_filename(item.get("file_path", item.get("file_name", item.get("name")))),
            status=_compact_text(item.get("status"), limit=80),
            progress=progress,
            chunks_count=chunks_count,
            track_id=_safe_reference(item.get("track_id")),
            error_message=_safe_error_message(item.get("error", item.get("error_message"))),
            created_at=_compact_text(item.get("created_at"), limit=80),
            updated_at=_compact_text(item.get("updated_at"), limit=80),
        )


class DisabledSupplementalRetriever:
    """No-op adapter used unless supplemental retrieval is explicitly enabled."""

    def availability_snapshot(self) -> SupplementalAvailabilitySnapshot:
        return SupplementalAvailabilitySnapshot(SupplementalAvailability.DISABLED)

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def retrieve(
        self,
        *,
        query: str | None,
        ocr_text: str | None,
    ) -> list[SupplementalDocument]:
        raise SupplementalUnavailableError("supplemental retrieval is disabled")

    async def supported_file_types(self) -> list[str]:
        raise SupplementalUnavailableError("supplemental retrieval is disabled")

    async def list_materials(
        self,
        *,
        page: int,
        page_size: int,
        statuses: Sequence[str] | None = None,
    ) -> SupplementalMaterialPage:
        raise SupplementalUnavailableError("supplemental retrieval is disabled")

    async def upload_material(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None,
    ) -> str | None:
        raise SupplementalUnavailableError("supplemental retrieval is disabled")

    async def delete_material(self, *, document_id: str) -> None:
        raise SupplementalUnavailableError("supplemental retrieval is disabled")


class LightRagSupplementalRetriever:
    """Gate all LightRAG dispatches behind fresh two-success health state."""

    def __init__(
        self,
        adapter: LightRagHttpAdapter,
        *,
        health_interval_seconds: float,
        health_ttl_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if health_interval_seconds <= 0 or health_ttl_seconds <= 0:
            raise ValueError("supplemental health intervals must be positive")
        self._adapter = adapter
        self._health_interval_seconds = health_interval_seconds
        self._health_ttl_seconds = health_ttl_seconds
        self._monotonic = monotonic
        self._consecutive_health_successes = 0
        self._last_health_success_at: float | None = None
        self._state = SupplementalAvailability.UNAVAILABLE
        self._monitor_task: asyncio.Task[None] | None = None

    def availability_snapshot(self) -> SupplementalAvailabilitySnapshot:
        if self._state is SupplementalAvailability.AVAILABLE:
            if (
                self._last_health_success_at is None
                or self._monotonic() - self._last_health_success_at > self._health_ttl_seconds
            ):
                # A stale probe is no longer an available service. Reset the
                # recovery counter so two fresh probes are required again.
                self._state = SupplementalAvailability.STALE
                self._consecutive_health_successes = 0
                self._last_health_success_at = None
                return SupplementalAvailabilitySnapshot(
                    SupplementalAvailability.STALE,
                    0,
                )
        return SupplementalAvailabilitySnapshot(
            self._state,
            self._consecutive_health_successes,
        )

    async def start(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(
                self._monitor(),
                name="lightrag-supplemental-health",
            )

    async def aclose(self) -> None:
        task = self._monitor_task
        self._monitor_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._adapter.aclose()

    async def probe_once(self) -> SupplementalAvailabilitySnapshot:
        healthy = await self._adapter.health()
        if not healthy:
            self._mark_unavailable()
            return self.availability_snapshot()
        self._consecutive_health_successes += 1
        self._last_health_success_at = self._monotonic()
        if self._consecutive_health_successes >= 2:
            self._state = SupplementalAvailability.AVAILABLE
        return self.availability_snapshot()

    async def retrieve(
        self,
        *,
        query: str | None,
        ocr_text: str | None,
    ) -> list[SupplementalDocument]:
        channels: list[tuple[float, str]] = []
        if query and query.strip():
            channels.append((0.65 if ocr_text and ocr_text.strip() else 1.0, query.strip()))
        if ocr_text and ocr_text.strip():
            channels.append((0.35 if query and query.strip() else 1.0, ocr_text.strip()))
        if not channels:
            return []
        try:
            channel_documents = await asyncio.gather(
                *(self._adapter.query_data(channel_query) for _weight, channel_query in channels)
            )
        except SupplementalUpstreamError:
            self._mark_unavailable()
            raise
        return self._fuse_channel_documents(
            [
                (weight, documents)
                for (weight, _query), documents in zip(
                    channels,
                    channel_documents,
                    strict=True,
                )
            ]
        )

    async def supported_file_types(self) -> list[str]:
        return await self._dispatch_admin(self._adapter.supported_file_types)

    async def list_materials(
        self,
        *,
        page: int,
        page_size: int,
        statuses: Sequence[str] | None = None,
    ) -> SupplementalMaterialPage:
        return await self._dispatch_admin(
            lambda: self._adapter.list_materials(
                page=page,
                page_size=page_size,
                statuses=statuses,
            )
        )

    async def upload_material(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None,
    ) -> str | None:
        return await self._dispatch_admin(
            lambda: self._adapter.upload_material(
                filename=filename,
                content=content,
                content_type=content_type,
            )
        )

    async def delete_material(self, *, document_id: str) -> None:
        await self._dispatch_admin(lambda: self._adapter.delete_material(document_id=document_id))

    async def _dispatch_admin(self, operation: Callable[[], Any]) -> Any:
        try:
            return await operation()
        except SupplementalUpstreamError:
            self._mark_unavailable()
            raise

    async def _monitor(self) -> None:
        while True:
            try:
                await self.probe_once()
            except Exception:
                # ``health`` is deliberately defensive, but the monitor must
                # never make the API lifecycle fail if an adapter is replaced.
                LOGGER.warning("supplemental health probe failed", exc_info=True)
                self._mark_unavailable()
            await asyncio.sleep(self._health_interval_seconds)

    def _mark_unavailable(self) -> None:
        self._consecutive_health_successes = 0
        self._last_health_success_at = None
        self._state = SupplementalAvailability.UNAVAILABLE

    @staticmethod
    def _fuse_channel_documents(
        channel_documents: Sequence[tuple[float, Sequence[SupplementalDocument]]],
    ) -> list[SupplementalDocument]:
        merged: dict[str, SupplementalDocument] = {}
        for weight, documents in channel_documents:
            for document in documents:
                weighted_score = weight * document.source_score
                existing = merged.get(document.source_hash)
                if existing is None:
                    merged[document.source_hash] = replace(document, source_score=weighted_score)
                    continue
                existing_references = existing.citation_metadata.get("reference_ids", [])
                new_references = document.citation_metadata.get("reference_ids", [])
                references = [
                    item
                    for item in [*existing_references, *new_references]
                    if isinstance(item, str)
                ]
                merged[document.source_hash] = SupplementalDocument(
                    source_hash=document.source_hash,
                    title=existing.title,
                    content=_merge_content([existing.content, document.content]),
                    citation_metadata={
                        "chunk_count": int(existing.citation_metadata.get("chunk_count", 0))
                        + int(document.citation_metadata.get("chunk_count", 0)),
                        "reference_ids": list(dict.fromkeys(references))[:20],
                    },
                    source_score=existing.source_score + weighted_score,
                    upstream_rank=min(existing.upstream_rank, document.upstream_rank),
                )
        return sorted(
            merged.values(),
            key=lambda item: (-item.source_score, item.upstream_rank, item.source_hash),
        )


class InMemorySupplementalRetriever:
    """Test adapter that follows the same availability gate contract."""

    def __init__(
        self,
        documents: Sequence[SupplementalDocument] = (),
        *,
        state: SupplementalAvailability = SupplementalAvailability.AVAILABLE,
    ) -> None:
        self.documents = list(documents)
        self.state = state
        self.retrieve_calls = 0
        self.supported_extensions = ["pdf", "docx", "txt", "md"]
        self.materials: list[SupplementalMaterial] = []

    def availability_snapshot(self) -> SupplementalAvailabilitySnapshot:
        return SupplementalAvailabilitySnapshot(
            self.state,
            2 if self.state is SupplementalAvailability.AVAILABLE else 0,
        )

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def retrieve(
        self,
        *,
        query: str | None,
        ocr_text: str | None,
    ) -> list[SupplementalDocument]:
        self.retrieve_calls += 1
        if self.state is not SupplementalAvailability.AVAILABLE:
            raise SupplementalUnavailableError("supplemental retrieval is unavailable")
        return list(self.documents)

    async def supported_file_types(self) -> list[str]:
        if self.state is not SupplementalAvailability.AVAILABLE:
            raise SupplementalUnavailableError("supplemental retrieval is unavailable")
        return list(self.supported_extensions)

    async def list_materials(
        self,
        *,
        page: int,
        page_size: int,
        statuses: Sequence[str] | None = None,
    ) -> SupplementalMaterialPage:
        if self.state is not SupplementalAvailability.AVAILABLE:
            raise SupplementalUnavailableError("supplemental retrieval is unavailable")
        rows = [
            item
            for item in self.materials
            if not statuses or item.status in set(statuses)
        ]
        start = (page - 1) * page_size
        return SupplementalMaterialPage(rows[start : start + page_size], len(rows), page, page_size)

    async def upload_material(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None,
    ) -> str | None:
        if self.state is not SupplementalAvailability.AVAILABLE:
            raise SupplementalUnavailableError("supplemental retrieval is unavailable")
        document_id = hashlib.sha256(filename.encode("utf-8") + content).hexdigest()
        self.materials.append(
            SupplementalMaterial(document_id=document_id, title=filename, status="pending")
        )
        return document_id

    async def delete_material(self, *, document_id: str) -> None:
        if self.state is not SupplementalAvailability.AVAILABLE:
            raise SupplementalUnavailableError("supplemental retrieval is unavailable")
        self.materials = [item for item in self.materials if item.document_id != document_id]


def create_supplemental_retriever(settings: Settings) -> SupplementalRetriever:
    if not settings.supplemental_retrieval_enabled:
        return DisabledSupplementalRetriever()
    return LightRagSupplementalRetriever(
        LightRagHttpAdapter(
            settings.lightrag_base_url,
            health_timeout_seconds=settings.lightrag_health_timeout_seconds,
            retrieval_timeout_seconds=settings.lightrag_retrieval_timeout_seconds,
        ),
        health_interval_seconds=settings.lightrag_health_interval_seconds,
        health_ttl_seconds=settings.lightrag_health_ttl_seconds,
    )
