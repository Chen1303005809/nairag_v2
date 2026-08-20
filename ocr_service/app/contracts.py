from __future__ import annotations

import base64
import binascii
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

MODEL_NAME = "PP-OCRv6_medium"
ALLOWED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_KEYWORDS = 64
_KEYWORD_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


class OcrInputError(ValueError):
    """The HTTP request did not contain a supported image."""


class OcrNoTextError(ValueError):
    """The OCR model completed but found no usable text."""


class OcrOutputError(RuntimeError):
    """The local model returned a result outside the expected schema."""


@dataclass(frozen=True)
class OcrResult:
    text: str
    keywords: tuple[str, ...]
    confidence: float
    model_version: str = MODEL_NAME


def detect_image_mime_type(image_bytes: bytes) -> str | None:
    for signature, mime_type in _IMAGE_SIGNATURES:
        if image_bytes.startswith(signature):
            return mime_type
    if (
        len(image_bytes) >= 12
        and image_bytes[:4] == b"RIFF"
        and image_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def decode_image_payload(
    image_base64: str,
    *,
    mime_type: str,
    max_bytes: int,
) -> bytes:
    """Decode and signature-check a bounded image without writing it to disk."""

    if mime_type not in ALLOWED_MIME_TYPES:
        raise OcrInputError("仅支持 PNG、JPEG 或 WebP 图片")
    if not image_base64:
        raise OcrInputError("图片内容不能为空")
    max_encoded_length = 4 * math.ceil(max_bytes / 3)
    if len(image_base64) > max_encoded_length:
        raise OcrInputError("图片大小超过服务限制")
    try:
        image_bytes = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OcrInputError("图片编码无效") from exc
    if not image_bytes or len(image_bytes) > max_bytes:
        raise OcrInputError("图片大小超过服务限制")
    detected_mime_type = detect_image_mime_type(image_bytes)
    if detected_mime_type != mime_type:
        raise OcrInputError("图片类型校验失败")
    return image_bytes


def clean_text(value: str) -> str:
    return " ".join(value.split())


def derive_keywords(text: str) -> tuple[str, ...]:
    keywords: list[str] = []
    seen: set[str] = set()
    for match in _KEYWORD_PATTERN.finditer(text.casefold()):
        keyword = match.group(0)
        if keyword in seen:
            continue
        keywords.append(keyword)
        seen.add(keyword)
        if len(keywords) >= MAX_KEYWORDS:
            break
    return tuple(keywords)


def _as_mapping(result: object) -> Mapping[str, Any]:
    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise OcrOutputError("OCR model returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise OcrOutputError("OCR model returned invalid result")
    nested_payload = payload.get("res")
    if isinstance(nested_payload, Mapping):
        return nested_payload
    return payload


def _as_list(value: object, field_name: str) -> list[object]:
    if isinstance(value, str | bytes) or not isinstance(value, Iterable):
        raise OcrOutputError(f"OCR model returned invalid {field_name}")
    return list(value)


def collect_ocr_result(results: Iterable[object]) -> OcrResult:
    """Normalize PaddleOCR result objects into Nairag's stable HTTP response."""

    recognized: list[tuple[str, float]] = []
    for model_result in results:
        payload = _as_mapping(model_result)
        texts = _as_list(payload.get("rec_texts"), "rec_texts")
        scores = _as_list(payload.get("rec_scores"), "rec_scores")
        if len(texts) != len(scores):
            raise OcrOutputError("OCR model returned mismatched text and confidence lists")
        for raw_text, raw_score in zip(texts, scores, strict=True):
            if not isinstance(raw_text, str):
                raise OcrOutputError("OCR model returned invalid text")
            text = clean_text(raw_text)
            if not text:
                continue
            if isinstance(raw_score, bool):
                raise OcrOutputError("OCR model returned invalid confidence")
            try:
                confidence = float(raw_score)
            except (TypeError, ValueError) as exc:
                raise OcrOutputError("OCR model returned invalid confidence") from exc
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise OcrOutputError("OCR model returned invalid confidence")
            recognized.append((text, confidence))

    if not recognized:
        raise OcrNoTextError("图片中没有可用于检索的文字")
    text = "\n".join(item[0] for item in recognized)
    total_weight = sum(max(1, len(item[0])) for item in recognized)
    confidence = sum(
        score * max(1, len(line)) for line, score in recognized
    ) / total_weight
    return OcrResult(
        text=text,
        keywords=derive_keywords(text),
        confidence=confidence,
    )
