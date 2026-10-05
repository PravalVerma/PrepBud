"""Text extraction from uploaded files (AI_SYSTEM_DESIGN §5.1).

* PDF — pdfplumber, page by page. Tables are emitted as markdown tables in reading
  order so the chunker can keep them intact. Pages with (almost) no text layer are
  rendered and OCR'd with Tesseract (scanned / image-only pages).
* Images (PNG/JPEG) — OCR.
* Plain text — decoded (UTF-8, falling back to cp1252/latin-1); form feeds split pages.

The declared MIME type is checked against the file's magic bytes. Everything here
is synchronous and CPU-bound; callers run it in a worker thread.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal, Protocol

from app.config import ContentProcessingSettings
from app.core.logging import get_logger

logger = get_logger(__name__)

FileKind = Literal["pdf", "image", "text"]

MIME_KINDS: dict[str, FileKind] = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "text/plain": "text",
}


class ExtractionError(Exception):
    """The file cannot be turned into text. Permanent — retrying won't help."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class PageText:
    page_number: int | None
    text: str
    ocr: bool = False


@dataclass(slots=True)
class ExtractedText:
    pages: list[PageText]
    page_count: int
    ocr_pages: list[int] = field(default_factory=list)
    # Pages that needed OCR but no OCR engine was available.
    ocr_unavailable_pages: list[int] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.pages)


# --- OCR ------------------------------------------------------------------------------


class OCREngine(Protocol):
    def available(self) -> bool: ...

    def image_to_text(self, image: Any) -> str: ...


class TesseractOCR:
    def __init__(self, language: str = "eng") -> None:
        self.language = language

    def available(self) -> bool:
        return _tesseract_available()

    def image_to_text(self, image: Any) -> str:
        import pytesseract

        return str(pytesseract.image_to_string(image, lang=self.language))


@lru_cache(maxsize=1)
def _tesseract_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
    except Exception:
        logger.warning("tesseract not available; OCR disabled")
        return False
    return True


# --- Cleaning ---------------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_TRAILING_WS = re.compile(r"[ \t]+\n")
_MANY_BLANKS = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Normalise extracted text. Strips NULs/control chars (PostgreSQL TEXT rejects
    ``\\x00``), re-joins words hyphenated across line breaks, trims whitespace."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _TRAILING_WS.sub("\n", text)
    text = _MANY_BLANKS.sub("\n\n", text)
    return text.strip()


# --- File type detection --------------------------------------------------------------------


def sniff_kind(data: bytes) -> FileKind | None:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff"):
        return "image"
    return None


def resolve_kind(data: bytes, mime_type: str) -> FileKind:
    declared = MIME_KINDS.get(mime_type)
    if declared is None:
        raise ExtractionError("UNSUPPORTED_FILE_TYPE", f"Unsupported file type {mime_type}")
    sniffed = sniff_kind(data[:16])
    if declared == "text":
        if sniffed is not None:
            raise ExtractionError("FILE_TYPE_MISMATCH", "File content does not match its type")
        return "text"
    if sniffed != declared:
        raise ExtractionError("FILE_TYPE_MISMATCH", "File content does not match its type")
    return declared


# --- Extractors -------------------------------------------------------------------------


def _table_markdown(rows: list[list[str | None]]) -> str:
    cleaned = [
        [(cell or "").replace("\n", " ").replace("|", "/").strip() for cell in row] for row in rows
    ]
    cleaned = [row for row in cleaned if any(row)]
    if not cleaned:
        return ""
    width = max(len(r) for r in cleaned)
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]
    lines = ["| " + " | ".join(cleaned[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in cleaned[1:]]
    return "\n".join(lines)


def _pdf_page_text(page: Any) -> str:
    """Page text in reading order with tables rendered as markdown blocks."""
    try:
        tables = sorted(page.find_tables(), key=lambda t: t.bbox[1])
    except Exception:  # table detection is best effort
        tables = []
    if not tables:
        return page.extract_text() or ""

    x0, top, x1, bottom = page.bbox
    parts: list[str] = []
    cursor = top
    try:
        for table in tables:
            t_top, t_bottom = max(table.bbox[1], cursor), min(table.bbox[3], bottom)
            if t_top > cursor:
                parts.append(page.crop((x0, cursor, x1, t_top)).extract_text() or "")
            parts.append(_table_markdown(table.extract()))
            cursor = max(cursor, t_bottom)
        if cursor < bottom:
            parts.append(page.crop((x0, cursor, x1, bottom)).extract_text() or "")
    except Exception:  # odd geometry — fall back to the plain text layer
        return page.extract_text() or ""
    return "\n\n".join(p for p in parts if p.strip())


def _extract_pdf(data: bytes, cfg: ContentProcessingSettings, ocr: OCREngine) -> ExtractedText:
    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError("PDF_UNREADABLE", "The PDF could not be opened") from exc
    with pdf:
        try:
            page_count = len(pdf.pages)
        except Exception as exc:
            raise ExtractionError("PDF_UNREADABLE", "The PDF could not be read") from exc
        if page_count > cfg.max_pages:
            raise ExtractionError(
                "TOO_MANY_PAGES", f"The PDF has {page_count} pages (limit {cfg.max_pages})"
            )
        result = ExtractedText(pages=[], page_count=page_count)
        for number, page in enumerate(pdf.pages, start=1):
            try:
                text = clean_text(_pdf_page_text(page))
            except Exception:
                logger.warning("pdf page text extraction failed", extra={"page": number})
                text = ""
            used_ocr = False
            if len(text) < cfg.ocr_min_chars_per_page:
                if ocr.available():
                    image = page.to_image(resolution=cfg.ocr_resolution_dpi).original
                    ocr_text = clean_text(ocr.image_to_text(image))
                    if len(ocr_text) > len(text):
                        text, used_ocr = ocr_text, True
                        result.ocr_pages.append(number)
                else:
                    result.ocr_unavailable_pages.append(number)
            page.close()  # release pdfminer layout caches (keeps memory flat on long PDFs)
            result.pages.append(PageText(page_number=number, text=text, ocr=used_ocr))
    return result


def _extract_image(data: bytes, ocr: OCREngine) -> ExtractedText:
    from PIL import Image, UnidentifiedImageError

    if not ocr.available():
        raise ExtractionError("OCR_UNAVAILABLE", "Text recognition (OCR) is not available")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            text = clean_text(ocr.image_to_text(image))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ExtractionError("IMAGE_UNREADABLE", "The image could not be read") from exc
    return ExtractedText(pages=[PageText(1, text, ocr=True)], page_count=1, ocr_pages=[1])


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _extract_text(data: bytes) -> ExtractedText:
    raw = _decode(data)
    parts = raw.split("\f") if "\f" in raw else [raw]
    pages = [
        PageText(page_number=i if len(parts) > 1 else None, text=clean_text(p))
        for i, p in enumerate(parts, start=1)
    ]
    return ExtractedText(pages=[p for p in pages if p.text], page_count=len(parts))


def extract_text(
    data: bytes, mime_type: str, cfg: ContentProcessingSettings, ocr: OCREngine
) -> ExtractedText:
    kind = resolve_kind(data, mime_type)
    if kind == "pdf":
        result = _extract_pdf(data, cfg, ocr)
    elif kind == "image":
        result = _extract_image(data, ocr)
    else:
        result = _extract_text(data)

    if not any(p.text.strip() for p in result.pages):
        if result.ocr_unavailable_pages:
            raise ExtractionError(
                "OCR_UNAVAILABLE",
                "The document has no text layer and text recognition (OCR) is not available",
            )
        raise ExtractionError("NO_TEXT", "No readable text was found in the document")
    return result
