from __future__ import annotations

import json
from collections import deque

import httpx
import pytest

from app.services.supplemental_retrieval import (
    LightRagHttpAdapter,
    LightRagSupplementalRetriever,
    SupplementalAvailability,
    SupplementalDocument,
)


class StubLightRagAdapter:
    def __init__(self, health_results: list[bool]) -> None:
        self.health_results = deque(health_results)
        self.queries: list[str] = []

    async def health(self) -> bool:
        return self.health_results.popleft() if self.health_results else False

    async def aclose(self) -> None:
        return None

    async def query_data(self, query: str) -> list[SupplementalDocument]:
        self.queries.append(query)
        if query == "文本查询":
            return [
                SupplementalDocument(
                    source_hash="a" * 64,
                    title="系统指南.pdf",
                    content="文本通道片段",
                    citation_metadata={"chunk_count": 1, "reference_ids": ["chunk-text"]},
                    source_score=1.0,
                    upstream_rank=1,
                )
            ]
        return [
            SupplementalDocument(
                source_hash="a" * 64,
                title="系统指南.pdf",
                content="OCR 通道片段",
                citation_metadata={"chunk_count": 1, "reference_ids": ["chunk-ocr"]},
                source_score=0.5,
                upstream_rank=2,
            )
        ]


async def test_health_gate_requires_two_successes_and_stales_without_dispatch() -> None:
    clock = [0.0]
    adapter = StubLightRagAdapter([True, True, False, True, True])
    retriever = LightRagSupplementalRetriever(
        adapter,  # type: ignore[arg-type] - this is a narrow test double.
        health_interval_seconds=5,
        health_ttl_seconds=10,
        monotonic=lambda: clock[0],
    )

    assert retriever.availability_snapshot().state is SupplementalAvailability.UNAVAILABLE
    assert (await retriever.probe_once()).state is SupplementalAvailability.UNAVAILABLE
    assert (await retriever.probe_once()).state is SupplementalAvailability.AVAILABLE

    clock[0] = 11.0
    assert retriever.availability_snapshot().state is SupplementalAvailability.STALE
    assert (await retriever.probe_once()).state is SupplementalAvailability.UNAVAILABLE
    assert (await retriever.probe_once()).state is SupplementalAvailability.UNAVAILABLE
    assert (await retriever.probe_once()).state is SupplementalAvailability.AVAILABLE


async def test_dual_channel_retrieval_uses_weighted_fusion_and_safe_documents() -> None:
    adapter = StubLightRagAdapter([True, True])
    retriever = LightRagSupplementalRetriever(
        adapter,  # type: ignore[arg-type] - this is a narrow test double.
        health_interval_seconds=5,
        health_ttl_seconds=10,
    )

    results = await retriever.retrieve(query="文本查询", ocr_text="OCR 查询")

    assert adapter.queries == ["文本查询", "OCR 查询"]
    assert len(results) == 1
    assert results[0].source_score == pytest.approx(0.825)
    assert results[0].title == "系统指南.pdf"
    assert results[0].citation_metadata == {
        "chunk_count": 2,
        "reference_ids": ["chunk-text", "chunk-ocr"],
    }


async def test_http_adapter_uses_official_query_envelope_and_never_returns_raw_paths() -> None:
    captured_payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        assert request.url.path == "/query/data"
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "chunks": [
                        {
                            "content": "仅供展示的资料片段",
                            "file_path": "/private/internals/客户/系统指南.pdf",
                            "reference_id": "chunk-1",
                        }
                    ]
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://lightrag.test",
    ) as client:
        adapter = LightRagHttpAdapter(
            "http://lightrag.test",
            health_timeout_seconds=1,
            retrieval_timeout_seconds=15,
            client=client,
        )
        assert await adapter.health()
        documents = await adapter.query_data("如何处理登录失败")

    assert captured_payloads == [
        {
            "query": "如何处理登录失败",
            "mode": "mix",
            "enable_rerank": False,
            "top_k": 60,
            "chunk_top_k": 20,
        }
    ]
    assert len(documents) == 1
    assert documents[0].title == "系统指南.pdf"
    assert "/private/" not in documents[0].title
    assert "/private/" not in documents[0].content
    assert "/private/" not in str(documents[0].citation_metadata)
