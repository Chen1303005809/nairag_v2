from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from typing import Annotated

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sentence_transformers import CrossEncoder

DEFAULT_MODEL = "Qwen/Qwen3-Reranker-0.6B"
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RerankRequest(BaseModel):
    model: str = DEFAULT_MODEL
    query: Text
    documents: list[Text] = Field(min_length=1, max_length=64)

    @field_validator("query")
    @classmethod
    def validate_query_length(cls, value: str) -> str:
        if len(value) > 16_000:
            raise ValueError("query must contain at most 16000 characters")
        return value

    @field_validator("documents")
    @classmethod
    def validate_document_lengths(cls, values: list[str]) -> list[str]:
        if any(len(value) > 64_000 for value in values):
            raise ValueError("each document must contain at most 64000 characters")
        return values


class RerankResult(BaseModel):
    index: int
    relevance_score: float


class RerankResponse(BaseModel):
    results: list[RerankResult]


class RerankerRuntime:
    def __init__(self) -> None:
        self.model_name = os.environ.get("RERANKER_MODEL", DEFAULT_MODEL).strip()
        self.device = os.environ.get("RERANKER_DEVICE", "cuda").strip()
        self.max_length = int(os.environ.get("RERANKER_MAX_LENGTH", "2048"))
        self.batch_size = int(os.environ.get("RERANKER_BATCH_SIZE", "4"))
        if not self.model_name:
            raise RuntimeError("RERANKER_MODEL must not be empty")
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("RERANKER_DEVICE requires CUDA, but CUDA is unavailable")
        if self.max_length <= 0:
            raise RuntimeError("RERANKER_MAX_LENGTH must be positive")
        if self.batch_size <= 0:
            raise RuntimeError("RERANKER_BATCH_SIZE must be positive")
        self._lock = threading.Lock()
        self._model = CrossEncoder(
            self.model_name,
            device=self.device,
            max_length=self.max_length,
        )

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        pairs = [(query, document) for document in documents]
        with self._lock:
            scores = self._model.predict(
                pairs,
                activation_fn=torch.nn.Sigmoid(),
                batch_size=self.batch_size,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return [float(score) for score in scores]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = RerankerRuntime()
    yield


app = FastAPI(title="Nairag Reranker", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    runtime: RerankerRuntime = app.state.runtime
    return {
        "status": "ok",
        "model": runtime.model_name,
        "device": runtime.device,
    }


@app.post("/rerank", response_model=RerankResponse)
def rerank(request: RerankRequest) -> RerankResponse:
    runtime: RerankerRuntime = app.state.runtime
    if request.model != runtime.model_name:
        raise HTTPException(
            status_code=400,
            detail=f"model {request.model!r} is not loaded",
        )
    scores = runtime.rerank(request.query, request.documents)
    return RerankResponse(
        results=[
            RerankResult(index=index, relevance_score=score)
            for index, score in enumerate(scores)
        ]
    )
