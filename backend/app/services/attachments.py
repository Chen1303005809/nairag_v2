from __future__ import annotations

import io
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import ReviewerKnowledgeBase
from app.models.knowledge_content import (
    ChildKnowledgeBasePublication,
    ChildPublicationStatus,
    EvidenceAttachment,
    ReviewSubmission,
    ReviewSubmissionTarget,
)
from app.models.user_account import UserRole

ALLOWED_ATTACHMENT_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
}
MAX_ATTACHMENTS_PER_REVISION = 10
MAX_ATTACHMENT_TOTAL_BYTES = 100 * 1024 * 1024


class AttachmentValidationError(Exception):
    pass


@dataclass(frozen=True)
class ValidatedAttachmentUpload:
    name: str
    content_type: str
    content: bytes
    storage_key: str
    checksum_sha256: str


def _assert_office_container(content: bytes, content_type: str) -> None:
    expected_directory = {
        ALLOWED_ATTACHMENT_CONTENT_TYPES[".docx"]: "word/",
        ALLOWED_ATTACHMENT_CONTENT_TYPES[".xlsx"]: "xl/",
        ALLOWED_ATTACHMENT_CONTENT_TYPES[".pptx"]: "ppt/",
    }[content_type]
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            members = archive.namelist()
    except BadZipFile as exc:
        raise AttachmentValidationError("Office 附件内容无效") from exc
    normalized_members = {member.casefold() for member in members}
    if "[content_types].xml" not in normalized_members or not any(
        member.startswith(expected_directory) for member in normalized_members
    ):
        raise AttachmentValidationError("Office 附件内容与扩展名不匹配")
    if any(member.endswith("vbaproject.bin") for member in normalized_members):
        raise AttachmentValidationError("不支持包含宏的 Office 附件")


def _assert_content_matches_type(content: bytes, content_type: str) -> None:
    if content_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AttachmentValidationError("PNG 附件内容无效")
    if content_type == "image/jpeg" and not content.startswith(b"\xff\xd8\xff"):
        raise AttachmentValidationError("JPEG 附件内容无效")
    if content_type == "image/webp" and not (
        len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    ):
        raise AttachmentValidationError("WebP 附件内容无效")
    if content_type == "application/pdf" and not content.startswith(b"%PDF-"):
        raise AttachmentValidationError("PDF 附件内容无效")
    office_types = set(ALLOWED_ATTACHMENT_CONTENT_TYPES.values()) - {
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/pdf",
        "text/plain",
    }
    if content_type in office_types:
        _assert_office_container(content, content_type)
    if content_type == "text/plain":
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AttachmentValidationError("TXT 附件必须使用 UTF-8 编码") from exc


def validate_attachment_upload(
    *,
    filename: str | None,
    declared_content_type: str | None,
    content: bytes,
    max_file_bytes: int,
) -> ValidatedAttachmentUpload:
    name = Path(filename or "").name.strip()
    if not name:
        raise AttachmentValidationError("附件必须包含文件名")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise AttachmentValidationError("附件文件名不能包含控制字符")
    if len(name) > 255:
        raise AttachmentValidationError("附件文件名不能超过 255 个字符")
    if not content:
        raise AttachmentValidationError("附件不能为空")
    if len(content) > max_file_bytes:
        raise AttachmentValidationError("单个附件不能超过 20 MB")

    suffix = Path(name).suffix.casefold()
    content_type = ALLOWED_ATTACHMENT_CONTENT_TYPES.get(suffix)
    if content_type is None:
        raise AttachmentValidationError("仅支持 PNG、JPEG、WebP、PDF、DOCX、XLSX、PPTX 和 TXT 附件")
    normalized_declared_type = (declared_content_type or "").split(";", 1)[0].strip().casefold()
    allowed_declared_types = {
        content_type,
        "application/octet-stream",
        "image/jpg" if content_type == "image/jpeg" else content_type,
    }
    if normalized_declared_type and normalized_declared_type not in allowed_declared_types:
        raise AttachmentValidationError("附件扩展名与内容类型不匹配")
    _assert_content_matches_type(content, content_type)
    return ValidatedAttachmentUpload(
        name=name,
        content_type=content_type,
        content=content,
        storage_key=f"uploads/{uuid4().hex}{suffix}",
        checksum_sha256=sha256(content).hexdigest(),
    )


async def attachment_is_readable_by(
    session: AsyncSession,
    *,
    attachment: EvidenceAttachment,
    user_id,
    user_role: UserRole,
) -> bool:
    """Authorize owner, relevant reviewer, administrator, or published readers."""

    if attachment.uploaded_by_user_id == user_id or user_role == UserRole.SYSTEM_ADMIN:
        return True
    if attachment.child_revision_id is None:
        return False

    published = await session.scalar(
        select(ChildKnowledgeBasePublication.child_id).where(
            ChildKnowledgeBasePublication.active_revision_id == attachment.child_revision_id,
            ChildKnowledgeBasePublication.status == ChildPublicationStatus.PUBLISHED,
        )
    )
    if published is not None:
        return True
    if user_role != UserRole.REVIEW_ADMIN:
        return False
    assigned_review_target = await session.scalar(
        select(ReviewSubmissionTarget.review_submission_id)
        .join(
            ReviewSubmission,
            ReviewSubmission.id == ReviewSubmissionTarget.review_submission_id,
        )
        .join(
            ReviewerKnowledgeBase,
            ReviewerKnowledgeBase.knowledge_base_id == ReviewSubmissionTarget.knowledge_base_id,
        )
        .where(
            ReviewSubmission.child_revision_id == attachment.child_revision_id,
            ReviewerKnowledgeBase.reviewer_user_id == user_id,
        )
    )
    return assigned_review_target is not None
