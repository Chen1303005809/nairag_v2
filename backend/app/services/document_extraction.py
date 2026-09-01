"""Safe, worker-side DOC/DOCX text extraction.

The API process never invokes LibreOffice.  This module is called only by the
durable worker and uses a fresh temporary directory plus a per-run LibreOffice
profile so an untrusted document cannot share lock files or user state with a
different job.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

WORDPROCESSING_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{WORDPROCESSING_NS}}}"


class DocumentExtractionError(Exception):
    """A permanent format/conversion failure visible to the batch owner."""


class DocumentExtractionTransientError(DocumentExtractionError):
    """A retryable temporary converter failure."""


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    image_count: int


def _clean_text(text: str) -> str:
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    # Word field separators and a stray BOM are not meaningful answer content.
    text = text.replace("\x13", "").replace("\x14", "").replace("\x15", "")
    lines = [re.sub(r"[ \t]+$", "", line) for line in text.split("\n")]
    compact: list[str] = []
    empty_run = 0
    for line in lines:
        if line.strip():
            compact.append(line)
            empty_run = 0
        elif empty_run < 1:
            compact.append("")
            empty_run += 1
    return "\n".join(compact).strip()


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_W}t":
            pieces.append(node.text or "")
        elif node.tag == f"{_W}tab":
            pieces.append("\t")
        elif node.tag in {f"{_W}br", f"{_W}cr"}:
            pieces.append("\n")
        # w:instrText is intentionally ignored: it is field code rather than
        # source prose and must not become a model instruction.
    value = "".join(pieces).strip()
    if not value:
        return ""
    if paragraph.find(f".//{_W}numPr") is not None:
        return f"• {value}"
    return value


def _extract_docx_xml(content: bytes) -> ExtractedDocument:
    try:
        with ZipFile(BytesIO(content)) as archive:
            try:
                document_xml = archive.read("word/document.xml")
            except KeyError as exc:
                raise DocumentExtractionError("DOCX 正文不存在") from exc
            image_count = sum(
                1
                for info in archive.infolist()
                if info.filename.casefold().startswith("word/media/") and not info.is_dir()
            )
    except BadZipFile as exc:
        raise DocumentExtractionError("DOCX 内容无效") from exc
    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        raise DocumentExtractionError("DOCX 正文格式无效") from exc

    paragraphs = [_paragraph_text(item) for item in root.iter(f"{_W}p")]
    return ExtractedDocument(
        text=_clean_text("\n".join(filter(None, paragraphs))),
        image_count=image_count,
    )


def _run_soffice(arguments: list[str], timeout_seconds: float) -> None:
    try:
        result = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise DocumentExtractionError("附件解析 Worker 未安装 LibreOffice") from exc
    except subprocess.TimeoutExpired as exc:
        raise DocumentExtractionTransientError("DOC 转换超时，请稍后自动重试") from exc
    if result.returncode != 0:
        # LibreOffice diagnostics can echo document metadata.  Never include
        # stdout/stderr in the user response or worker logs.
        raise DocumentExtractionError("DOC 转换失败，文件可能已损坏或受保护")


def _convert_doc_to_docx(
    *,
    content: bytes,
    soffice_path: str,
    timeout_seconds: float,
) -> bytes:
    with tempfile.TemporaryDirectory(prefix="nairag-attachment-") as directory_name:
        directory = Path(directory_name)
        source = directory / "source.doc"
        output = directory / "output"
        profile = directory / "profile"
        source.write_bytes(content)
        output.mkdir()
        profile.mkdir()
        _run_soffice(
            [
                soffice_path,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                "--nolockcheck",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                "docx",
                "--outdir",
                str(output),
                str(source),
            ],
            timeout_seconds,
        )
        converted = output / "source.docx"
        try:
            return converted.read_bytes()
        except OSError as exc:
            raise DocumentExtractionError("DOC 转换未生成有效 DOCX") from exc


def extract_word_document(
    content: bytes,
    *,
    suffix: str,
    soffice_path: str,
    timeout_seconds: float,
) -> ExtractedDocument:
    """Extract UTF-8-equivalent text and media count from a validated Word file."""

    if suffix == ".doc":
        content = _convert_doc_to_docx(
            content=content,
            soffice_path=soffice_path,
            timeout_seconds=timeout_seconds,
        )
    elif suffix != ".docx":
        raise DocumentExtractionError("附件仅支持 DOC 或 DOCX 解析")
    return _extract_docx_xml(content)


async def extract_word_document_async(
    content: bytes,
    *,
    suffix: str,
    soffice_path: str,
    timeout_seconds: float,
) -> ExtractedDocument:
    return await asyncio.to_thread(
        extract_word_document,
        content,
        suffix=suffix,
        soffice_path=soffice_path,
        timeout_seconds=timeout_seconds,
    )
