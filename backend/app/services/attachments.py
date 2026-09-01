from __future__ import annotations

import io
import struct
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
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
}
MAX_ATTACHMENTS_PER_REVISION = 10
MAX_ATTACHMENT_TOTAL_BYTES = 100 * 1024 * 1024
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_CFB_FREESECT = 0xFFFFFFFF
_CFB_ENDOFCHAIN = 0xFFFFFFFE
_CFB_FATSECT = 0xFFFFFFFD
_CFB_DIFSECT = 0xFFFFFFFC
_MAX_OFFICE_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


class AttachmentValidationError(Exception):
    pass


@dataclass(frozen=True)
class ValidatedAttachmentUpload:
    name: str
    content_type: str
    content: bytes
    storage_key: str
    checksum_sha256: str


@dataclass(frozen=True)
class _OleStream:
    start_sector: int
    size: int


def _cfb_u32(content: bytes, offset: int) -> int:
    if offset + 4 > len(content):
        raise AttachmentValidationError("DOC 附件内容无效")
    return struct.unpack_from("<I", content, offset)[0]


def _cfb_sector(content: bytes, sector_size: int, sector_id: int) -> bytes:
    if sector_id < 0 or sector_id in {
        _CFB_FREESECT,
        _CFB_ENDOFCHAIN,
        _CFB_FATSECT,
        _CFB_DIFSECT,
    }:
        raise AttachmentValidationError("DOC 附件内容无效")
    offset = 512 + sector_id * sector_size
    if offset < 512 or offset + sector_size > len(content):
        raise AttachmentValidationError("DOC 附件内容无效")
    return content[offset : offset + sector_size]


def _cfb_fat(content: bytes, sector_size: int) -> list[int]:
    """Read a bounded FAT from an OLE compound file.

    We only need stream names and the WordDocument stream, but parsing the FAT
    rather than searching raw bytes prevents a renamed ZIP or arbitrary CFB
    payload from passing as a legacy Word document.
    """

    fat_count = _cfb_u32(content, 44)
    if fat_count == 0 or fat_count > len(content) // sector_size:
        raise AttachmentValidationError("DOC 附件内容无效")
    fat_sector_ids = [
        _cfb_u32(content, 76 + index * 4)
        for index in range(109)
        if _cfb_u32(content, 76 + index * 4) != _CFB_FREESECT
    ]
    next_difat_sector = _cfb_u32(content, 68)
    seen_difat: set[int] = set()
    values_per_difat_sector = sector_size // 4 - 1
    while len(fat_sector_ids) < fat_count and next_difat_sector != _CFB_ENDOFCHAIN:
        if next_difat_sector in seen_difat:
            raise AttachmentValidationError("DOC 附件内容无效")
        seen_difat.add(next_difat_sector)
        sector = _cfb_sector(content, sector_size, next_difat_sector)
        for index in range(values_per_difat_sector):
            value = struct.unpack_from("<I", sector, index * 4)[0]
            if value != _CFB_FREESECT:
                fat_sector_ids.append(value)
                if len(fat_sector_ids) == fat_count:
                    break
        next_difat_sector = struct.unpack_from("<I", sector, sector_size - 4)[0]
    if len(fat_sector_ids) != fat_count:
        raise AttachmentValidationError("DOC 附件内容无效")

    entries: list[int] = []
    for sector_id in fat_sector_ids:
        sector = _cfb_sector(content, sector_size, sector_id)
        entries.extend(struct.unpack(f"<{sector_size // 4}I", sector))
    return entries


def _cfb_chain(
    content: bytes,
    *,
    sector_size: int,
    fat: list[int],
    start_sector: int,
) -> bytes:
    if start_sector == _CFB_ENDOFCHAIN:
        return b""
    parts: list[bytes] = []
    sector_id = start_sector
    seen: set[int] = set()
    # A valid chain cannot contain more regular sectors than the file has.
    max_sectors = len(content) // sector_size + 1
    while sector_id != _CFB_ENDOFCHAIN:
        if sector_id in seen or sector_id >= len(fat) or len(seen) >= max_sectors:
            raise AttachmentValidationError("DOC 附件内容无效")
        seen.add(sector_id)
        parts.append(_cfb_sector(content, sector_size, sector_id))
        sector_id = fat[sector_id]
    return b"".join(parts)


def _parse_ole_streams(content: bytes) -> tuple[dict[str, _OleStream], bytes, list[int], int]:
    if len(content) < 512 or not content.startswith(_CFB_MAGIC):
        raise AttachmentValidationError("DOC 附件内容无效")
    if content[28:30] != b"\xfe\xff":
        raise AttachmentValidationError("DOC 附件内容无效")
    sector_shift = struct.unpack_from("<H", content, 30)[0]
    mini_sector_shift = struct.unpack_from("<H", content, 32)[0]
    if sector_shift not in {9, 12} or mini_sector_shift != 6:
        raise AttachmentValidationError("DOC 附件内容无效")
    sector_size = 1 << sector_shift
    if len(content) < 512 + sector_size:
        raise AttachmentValidationError("DOC 附件内容无效")
    fat = _cfb_fat(content, sector_size)
    directory = _cfb_chain(
        content,
        sector_size=sector_size,
        fat=fat,
        start_sector=_cfb_u32(content, 48),
    )
    streams: dict[str, _OleStream] = {}
    root_stream = _OleStream(_CFB_ENDOFCHAIN, 0)
    for offset in range(0, len(directory) - 127, 128):
        entry = directory[offset : offset + 128]
        object_type = entry[66]
        if object_type == 0:
            continue
        name_length = struct.unpack_from("<H", entry, 64)[0]
        if not 2 <= name_length <= 64 or name_length % 2:
            raise AttachmentValidationError("DOC 附件内容无效")
        try:
            name = entry[: name_length - 2].decode("utf-16-le")
        except UnicodeDecodeError as exc:
            raise AttachmentValidationError("DOC 附件内容无效") from exc
        stream = _OleStream(
            start_sector=struct.unpack_from("<I", entry, 116)[0],
            size=struct.unpack_from("<Q", entry, 120)[0],
        )
        if object_type == 5:
            root_stream = stream
        elif object_type == 2:
            streams[name.casefold()] = stream
    if "worddocument" not in streams:
        raise AttachmentValidationError("DOC 附件内容与扩展名不匹配")
    root_mini_stream = b""
    if root_stream.start_sector != _CFB_ENDOFCHAIN and root_stream.size:
        root_mini_stream = _cfb_chain(
            content,
            sector_size=sector_size,
            fat=fat,
            start_sector=root_stream.start_sector,
        )[: root_stream.size]
    return streams, root_mini_stream, fat, sector_size


def _read_ole_stream(
    content: bytes,
    *,
    stream: _OleStream,
    root_mini_stream: bytes,
    fat: list[int],
    sector_size: int,
) -> bytes:
    mini_stream_cutoff = _cfb_u32(content, 56)
    if stream.size > _MAX_OFFICE_UNCOMPRESSED_BYTES:
        raise AttachmentValidationError("DOC 附件内容过大")
    if stream.size >= mini_stream_cutoff:
        return _cfb_chain(
            content,
            sector_size=sector_size,
            fat=fat,
            start_sector=stream.start_sector,
        )[: stream.size]

    mini_fat_start = _cfb_u32(content, 60)
    mini_fat_count = _cfb_u32(content, 64)
    if mini_fat_count == 0:
        raise AttachmentValidationError("DOC 附件内容无效")
    mini_fat_bytes = _cfb_chain(
        content,
        sector_size=sector_size,
        fat=fat,
        start_sector=mini_fat_start,
    )[: mini_fat_count * sector_size]
    if len(mini_fat_bytes) != mini_fat_count * sector_size:
        raise AttachmentValidationError("DOC 附件内容无效")
    mini_fat = list(struct.unpack(f"<{len(mini_fat_bytes) // 4}I", mini_fat_bytes))
    mini_sector_size = 64
    parts: list[bytes] = []
    sector_id = stream.start_sector
    seen: set[int] = set()
    while sector_id != _CFB_ENDOFCHAIN:
        if sector_id in seen or sector_id >= len(mini_fat):
            raise AttachmentValidationError("DOC 附件内容无效")
        seen.add(sector_id)
        start = sector_id * mini_sector_size
        end = start + mini_sector_size
        if end > len(root_mini_stream):
            raise AttachmentValidationError("DOC 附件内容无效")
        parts.append(root_mini_stream[start:end])
        sector_id = mini_fat[sector_id]
    return b"".join(parts)[: stream.size]


def _assert_legacy_word_document(content: bytes) -> None:
    streams, root_mini_stream, fat, sector_size = _parse_ole_streams(content)
    stream_names = set(streams)
    if any("vba" in name or "macro" in name for name in stream_names):
        raise AttachmentValidationError("不支持包含宏的 Office 附件")
    if "encryptioninfo" in stream_names or "encryptedpackage" in stream_names:
        raise AttachmentValidationError("不支持加密的 Office 附件")
    word_document = _read_ole_stream(
        content,
        stream=streams["worddocument"],
        root_mini_stream=root_mini_stream,
        fat=fat,
        sector_size=sector_size,
    )
    if len(word_document) < 12 or word_document[:2] != b"\xec\xa5":
        raise AttachmentValidationError("DOC 附件内容无效")
    flags = struct.unpack_from("<H", word_document, 10)[0]
    if flags & 0x0100 or flags & 0x8000:
        raise AttachmentValidationError("不支持加密的 Office 附件")


def _assert_office_container(content: bytes, content_type: str) -> None:
    expected_directory = {
        ALLOWED_ATTACHMENT_CONTENT_TYPES[".docx"]: "word/",
        ALLOWED_ATTACHMENT_CONTENT_TYPES[".xlsx"]: "xl/",
        ALLOWED_ATTACHMENT_CONTENT_TYPES[".pptx"]: "ppt/",
    }[content_type]
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            members = archive.namelist()
            uncompressed_size = sum(member.file_size for member in archive.infolist())
            if uncompressed_size > _MAX_OFFICE_UNCOMPRESSED_BYTES:
                raise AttachmentValidationError("Office 附件解压后过大")
    except BadZipFile as exc:
        raise AttachmentValidationError("Office 附件内容无效") from exc
    normalized_members = {member.casefold() for member in members}
    if "[content_types].xml" not in normalized_members or not any(
        member.startswith(expected_directory) for member in normalized_members
    ):
        raise AttachmentValidationError("Office 附件内容与扩展名不匹配")
    if any(member.endswith("vbaproject.bin") for member in normalized_members):
        raise AttachmentValidationError("不支持包含宏的 Office 附件")
    if {"encryptioninfo", "encryptedpackage"} & normalized_members:
        raise AttachmentValidationError("不支持加密的 Office 附件")


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
    if content_type == "application/msword":
        _assert_legacy_word_document(content)
    office_types = set(ALLOWED_ATTACHMENT_CONTENT_TYPES.values()) - {
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/pdf",
        "text/plain",
    }
    if content_type in office_types and content_type != "application/msword":
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
        raise AttachmentValidationError(
            "仅支持 PNG、JPEG、WebP、PDF、DOC、DOCX、XLSX、PPTX 和 TXT 附件"
        )
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
