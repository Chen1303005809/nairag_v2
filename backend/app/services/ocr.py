from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import httpx
import jwt

from app.core.config import Settings

OCR_MODEL_NAME = "PP-OCRv6_medium"
MAX_OCR_TEXT_LENGTH = 4_000
MAX_OCR_KEYWORDS = 64
MAX_OCR_KEYWORD_LENGTH = 120
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
_KEYWORD_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_IMAGE_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OcrInputError(ValueError):
    """The uploaded image cannot safely be sent to the OCR provider."""


class OcrNoTextError(ValueError):
    """The OCR provider completed but found no usable text."""


class OcrProviderError(RuntimeError):
    """The configured OCR provider failed or violated its HTTP contract."""


class OcrRecognitionTokenError(ValueError):
    """A client supplied an expired, invalid, or cross-user OCR token."""


@dataclass(frozen=True)
class OcrRecognition:
    text: str
    keywords: tuple[str, ...]
    confidence: float
    model_version: str
    image_sha256: str


class OcrProvider(Protocol):
    async def recognize(self, image_bytes: bytes, media_type: str) -> OcrRecognition:
        ...


def validate_ocr_image(image_bytes: bytes, *, max_bytes: int) -> str:
    """Validate a transient image upload and return its signature-derived MIME type."""

    if not image_bytes:
        raise OcrInputError("请选择包含图片内容的文件")
    if len(image_bytes) > max_bytes:
        raise OcrInputError(f"图片不能超过 {max_bytes // (1024 * 1024)} MB")
    for signature, media_type in _IMAGE_SIGNATURES:
        if image_bytes.startswith(signature):
            return media_type
    if (
        len(image_bytes) >= 12
        and image_bytes[:4] == b"RIFF"
        and image_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    raise OcrInputError("仅支持 PNG、JPEG 或 WebP 图片")


def clean_ocr_text(value: str) -> str:
    return " ".join(value.split())


def _derive_keywords(text: str) -> tuple[str, ...]:
    keywords: list[str] = []
    seen: set[str] = set()
    for match in _KEYWORD_PATTERN.finditer(text.casefold()):
        keyword = match.group(0)
        if keyword in seen:
            continue
        keywords.append(keyword)
        seen.add(keyword)
        if len(keywords) >= MAX_OCR_KEYWORDS:
            break
    return tuple(keywords)


def normalize_ocr_keywords(raw_keywords: object, *, text: str) -> tuple[str, ...]:
    if raw_keywords is None:
        return _derive_keywords(text)
    if isinstance(raw_keywords, str | bytes) or not isinstance(raw_keywords, Sequence):
        raise OcrProviderError("OCR service returned invalid keywords")

    keywords: list[str] = []
    seen: set[str] = set()
    for raw_keyword in raw_keywords:
        if not isinstance(raw_keyword, str):
            raise OcrProviderError("OCR service returned invalid keywords")
        keyword = clean_ocr_text(raw_keyword)
        if not keyword or len(keyword) > MAX_OCR_KEYWORD_LENGTH:
            continue
        normalized = keyword.casefold()
        if normalized in seen:
            continue
        keywords.append(keyword)
        seen.add(normalized)
        if len(keywords) >= MAX_OCR_KEYWORDS:
            break
    return tuple(keywords) or _derive_keywords(text)


def build_ocr_recognition(
    *,
    text: object,
    keywords: object,
    confidence: object,
    model_version: object,
    image_sha256: str,
) -> OcrRecognition:
    if not isinstance(text, str):
        raise OcrProviderError("OCR service returned invalid text")
    cleaned_text = clean_ocr_text(text)
    if not cleaned_text:
        raise OcrNoTextError("图片中没有可用于检索的文字")
    if len(cleaned_text) > MAX_OCR_TEXT_LENGTH:
        raise OcrProviderError("OCR service returned text that is too long")
    if isinstance(confidence, bool):
        raise OcrProviderError("OCR service returned invalid confidence")
    try:
        normalized_confidence = float(confidence)
    except (TypeError, ValueError) as exc:
        raise OcrProviderError("OCR service returned invalid confidence") from exc
    if not 0 <= normalized_confidence <= 1:
        raise OcrProviderError("OCR service returned invalid confidence")
    if model_version != OCR_MODEL_NAME:
        raise OcrProviderError("OCR service returned an unexpected model version")
    if not _IMAGE_HASH_PATTERN.fullmatch(image_sha256):
        raise ValueError("image_sha256 must be a lowercase SHA-256 hex digest")
    return OcrRecognition(
        text=cleaned_text,
        keywords=normalize_ocr_keywords(keywords, text=cleaned_text),
        confidence=normalized_confidence,
        model_version=OCR_MODEL_NAME,
        image_sha256=image_sha256,
    )


class HttpOcrProvider:
    """Call the local PP-OCR service through the stable JSON model-service contract.

    The service receives ``model``, ``image_base64`` and ``mime_type`` at
    ``POST {OCR_SERVICE_URL}/ocr`` and returns ``text``, ``keywords``,
    ``confidence`` and ``model_version`` (optionally inside a ``data`` object).
    Images are never written by this adapter; they only live in the request body.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
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
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def recognize(self, image_bytes: bytes, media_type: str) -> OcrRecognition:
        detected_media_type = validate_ocr_image(image_bytes, max_bytes=len(image_bytes))
        if media_type != detected_media_type:
            raise OcrInputError("图片类型校验失败")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = {
            "model": OCR_MODEL_NAME,
            "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            "mime_type": media_type,
        }
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            try:
                response = await client.post(
                    f"{self.base_url}/ocr",
                    json=payload,
                    headers=headers,
                )
                if response.status_code == 422:
                    detail = _ocr_service_error_detail(response)
                    if detail == "图片中没有可用于检索的文字":
                        raise OcrNoTextError(detail)
                    raise OcrInputError(detail or "OCR 服务拒绝了图片")
                response.raise_for_status()
                response_payload = response.json()
            except (OcrInputError, OcrNoTextError):
                raise
            except (httpx.HTTPError, ValueError) as exc:
                raise OcrProviderError("OCR service request failed") from exc
        finally:
            if owns_client:
                await client.aclose()

        result = response_payload.get("data", response_payload) if isinstance(
            response_payload, Mapping
        ) else None
        if not isinstance(result, Mapping):
            raise OcrProviderError("OCR service returned an invalid response")
        return build_ocr_recognition(
            text=result.get("text"),
            keywords=result.get("keywords"),
            confidence=result.get("confidence"),
            model_version=result.get("model_version", result.get("model")),
            image_sha256=hashlib.sha256(image_bytes).hexdigest(),
        )


def _ocr_service_error_detail(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, Mapping):
        return None
    detail = payload.get("detail")
    return detail if isinstance(detail, str) else None


def create_ocr_provider(settings: Settings) -> OcrProvider | None:
    if settings.ocr_service_url is None:
        return None
    return HttpOcrProvider(
        settings.ocr_service_url,
        api_key=settings.ocr_api_key,
        timeout_seconds=settings.ocr_timeout_seconds,
    )


def create_ocr_recognition_token(
    recognition: OcrRecognition,
    *,
    user_id: UUID,
    settings: Settings,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "purpose": "search_ocr_recognition",
        "sub": str(user_id),
        "text": recognition.text,
        "keywords": list(recognition.keywords),
        "confidence": recognition.confidence,
        "model_version": recognition.model_version,
        "image_sha256": recognition.image_sha256,
        "iat": now,
        "exp": now + timedelta(seconds=settings.ocr_ticket_ttl_seconds),
    }
    return jwt.encode(payload, settings.signing_key, algorithm=settings.jwt_algorithm)


def decode_ocr_recognition_token(
    token: str,
    *,
    user_id: UUID,
    settings: Settings,
) -> OcrRecognition:
    try:
        payload = jwt.decode(
            token,
            settings.signing_key,
            algorithms=[settings.jwt_algorithm],
            options={
                "require": [
                    "purpose",
                    "sub",
                    "text",
                    "keywords",
                    "confidence",
                    "model_version",
                    "image_sha256",
                    "iat",
                    "exp",
                ]
            },
        )
        if payload.get("purpose") != "search_ocr_recognition":
            raise OcrRecognitionTokenError("OCR 识别凭据无效")
        if UUID(str(payload["sub"])) != user_id:
            raise OcrRecognitionTokenError("OCR 识别凭据不属于当前用户")
        return build_ocr_recognition(
            text=payload["text"],
            keywords=payload["keywords"],
            confidence=payload["confidence"],
            model_version=payload["model_version"],
            image_sha256=str(payload["image_sha256"]),
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, OcrRecognitionTokenError):
            raise
        raise OcrRecognitionTokenError("OCR 识别凭据无效或已过期") from exc
