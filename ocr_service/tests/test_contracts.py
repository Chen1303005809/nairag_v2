from __future__ import annotations

import base64

import pytest

from app.contracts import (
    MODEL_NAME,
    OcrInputError,
    OcrNoTextError,
    collect_ocr_result,
    decode_image_payload,
)


def test_decode_image_payload_accepts_matching_png_signature() -> None:
    image_bytes = b"\x89PNG\r\n\x1a\nimage-content"

    result = decode_image_payload(
        base64.b64encode(image_bytes).decode("ascii"),
        mime_type="image/png",
        max_bytes=1024,
    )

    assert result == image_bytes


def test_decode_image_payload_rejects_mismatched_mime_type() -> None:
    image_bytes = b"\x89PNG\r\n\x1a\nimage-content"

    with pytest.raises(OcrInputError, match="类型校验"):
        decode_image_payload(
            base64.b64encode(image_bytes).decode("ascii"),
            mime_type="image/jpeg",
            max_bytes=1024,
        )


def test_collect_ocr_result_normalizes_lines_keywords_and_confidence() -> None:
    result = collect_ocr_result(
        [
            {
                "rec_texts": ["  客户  A  ", "Order_42"],
                "rec_scores": [0.8, 1.0],
            }
        ]
    )

    assert result.text == "客户 A\nOrder_42"
    assert result.keywords == ("客户", "a", "order_42")
    assert result.confidence == pytest.approx((0.8 * 4 + 1.0 * 8) / 12)
    assert result.model_version == MODEL_NAME


def test_collect_ocr_result_rejects_empty_recognition() -> None:
    with pytest.raises(OcrNoTextError):
        collect_ocr_result([{"rec_texts": ["  "], "rec_scores": [0.9]}])
