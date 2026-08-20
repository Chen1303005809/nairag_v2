from __future__ import annotations

import hashlib
import io
from zipfile import ZipFile

import pytest

from app.services.attachments import AttachmentValidationError, validate_attachment_upload


def office_document(*, include_macro: bool = False) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
        if include_macro:
            archive.writestr("word/vbaProject.bin", b"macro")
    return buffer.getvalue()


def test_attachment_upload_validation_derives_metadata_from_trusted_content() -> None:
    content = office_document()

    upload = validate_attachment_upload(
        filename="操作说明.DOCX",
        declared_content_type="application/octet-stream",
        content=content,
        max_file_bytes=20 * 1024 * 1024,
    )

    assert upload.name == "操作说明.DOCX"
    assert upload.content_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert upload.checksum_sha256 == hashlib.sha256(content).hexdigest()
    assert upload.storage_key.startswith("uploads/")
    assert upload.storage_key.endswith(".docx")


def test_attachment_upload_rejects_macro_enabled_office_content() -> None:
    with pytest.raises(AttachmentValidationError, match="宏"):
        validate_attachment_upload(
            filename="操作说明.docx",
            declared_content_type="application/octet-stream",
            content=office_document(include_macro=True),
            max_file_bytes=20 * 1024 * 1024,
        )
