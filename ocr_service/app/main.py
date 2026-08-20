from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

import numpy as np
from fastapi import FastAPI, HTTPException, Request, status
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from app.contracts import (
    ALLOWED_MIME_TYPES,
    MODEL_NAME,
    OcrInputError,
    OcrNoTextError,
    OcrResult,
    collect_ocr_result,
    decode_image_payload,
)

logger = logging.getLogger(__name__)


class OcrEngine(Protocol):
    def recognize(self, image: np.ndarray[Any, Any]) -> OcrResult:
        ...


@dataclass(frozen=True)
class OcrServiceSettings:
    max_image_bytes: int
    max_image_pixels: int
    cpu_threads: int
    device: str

    @classmethod
    def from_environment(cls) -> OcrServiceSettings:
        return cls(
            max_image_bytes=_positive_int("OCR_MAX_IMAGE_BYTES", 10 * 1024 * 1024),
            max_image_pixels=_positive_int("OCR_MAX_IMAGE_PIXELS", 20_000_000),
            cpu_threads=_positive_int("OCR_CPU_THREADS", 4),
            device=os.environ.get("OCR_DEVICE", "cpu").strip() or "cpu",
        )


@dataclass
class OcrServiceState:
    engine: OcrEngine
    settings: OcrServiceSettings
    inference_lock: asyncio.Lock


class OcrRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=64)
    image_base64: str = Field(min_length=1)
    mime_type: str = Field(min_length=1, max_length=32)


class OcrResponse(BaseModel):
    text: str
    keywords: list[str]
    confidence: float
    model_version: str


class PaddleOcrEngine:
    """The fixed PP-OCRv6 medium pipeline, loaded once when the process starts."""

    def __init__(self, settings: OcrServiceSettings) -> None:
        # Delayed import keeps the protocol and input-validation code testable without models.
        from paddleocr import PaddleOCR

        self._pipeline = PaddleOCR(
            text_detection_model_name="PP-OCRv6_medium_det",
            text_recognition_model_name="PP-OCRv6_medium_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device=settings.device,
            cpu_threads=settings.cpu_threads,
        )

    def recognize(self, image: np.ndarray[Any, Any]) -> OcrResult:
        return collect_ocr_result(self._pipeline.predict(image))


def _positive_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def decode_image_to_array(image_bytes: bytes, *, max_pixels: int) -> np.ndarray[Any, Any]:
    """Decode a single image in memory and reject decompression-bomb-sized inputs."""

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            if getattr(source, "n_frames", 1) != 1:
                raise OcrInputError("不支持多帧图片")
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise OcrInputError("图片像素数超过服务限制")
            image = source.convert("RGB")
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise OcrInputError("图片内容无效") from exc
    return np.asarray(image)


def create_app(
    *,
    settings: OcrServiceSettings | None = None,
    engine_factory: type[PaddleOcrEngine] = PaddleOcrEngine,
) -> FastAPI:
    service_settings = settings or OcrServiceSettings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Loading %s OCR models", MODEL_NAME)
        try:
            engine = await asyncio.to_thread(engine_factory, service_settings)
        except Exception:
            logger.exception("OCR model initialization failed")
            raise
        app.state.ocr = OcrServiceState(
            engine=engine,
            settings=service_settings,
            inference_lock=asyncio.Lock(),
        )
        logger.info("%s OCR models are ready", MODEL_NAME)
        yield

    app = FastAPI(title="Nairag OCR Service", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, str]:
        state = _state_from(request)
        return {"status": "ok", "model_version": MODEL_NAME, "device": state.settings.device}

    @app.post("/ocr", response_model=OcrResponse)
    async def recognize(payload: OcrRequest, request: Request) -> OcrResponse:
        service_state = _state_from(request)
        if payload.model != MODEL_NAME:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"仅支持模型 {MODEL_NAME}",
            )
        if payload.mime_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="仅支持 PNG、JPEG 或 WebP 图片",
            )
        try:
            image_bytes = decode_image_payload(
                payload.image_base64,
                mime_type=payload.mime_type,
                max_bytes=service_state.settings.max_image_bytes,
            )
            image = decode_image_to_array(
                image_bytes,
                max_pixels=service_state.settings.max_image_pixels,
            )
        except OcrInputError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

        try:
            async with service_state.inference_lock:
                result = await asyncio.to_thread(service_state.engine.recognize, image)
        except OcrNoTextError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            # Do not include model output or image-derived data in service logs.
            logger.error("OCR inference failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OCR 服务暂时不可用，请稍后重试",
            ) from exc

        return OcrResponse(
            text=result.text,
            keywords=list(result.keywords),
            confidence=result.confidence,
            model_version=result.model_version,
        )

    return app


def _state_from(request: Request) -> OcrServiceState:
    state = getattr(request.app.state, "ocr", None)
    if not isinstance(state, OcrServiceState):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OCR 服务正在初始化",
        )
    return state


app = create_app()
