"""Text extraction: PDF (tables, OCR fallback), images, plain text, type checks."""

from __future__ import annotations

import pytest

from app.config import ContentProcessingSettings
from app.services.content import text_extractor
from app.services.content.text_extractor import (
    ExtractionError,
    TesseractOCR,
    clean_text,
    extract_text,
    resolve_kind,
    sniff_kind,
)
from tests.fakes import FakeOCR, make_pdf, make_png

CFG = ContentProcessingSettings()


class TestCleanText:
    def test_strips_nul_and_control_chars(self) -> None:
        assert clean_text("a\x00b\x07c\td") == "abc\td"

    def test_rejoins_hyphenated_line_breaks(self) -> None:
        assert clean_text("photo-\nsynthesis is") == "photosynthesis is"

    def test_normalises_newlines_and_blank_runs(self) -> None:
        assert clean_text("a\r\nb  \n\n\n\nc\r") == "a\nb\n\nc"

    def test_keeps_math_symbols(self) -> None:
        assert clean_text("x² + √y ≥ π") == "x² + √y ≥ π"


class TestFileTypes:
    def test_sniffing(self) -> None:
        assert sniff_kind(b"%PDF-1.7...") == "pdf"
        assert sniff_kind(make_png()) == "image"
        assert sniff_kind(b"\xff\xd8\xff\xe0jpeg") == "image"
        assert sniff_kind(b"hello") is None

    @pytest.mark.parametrize(
        ("data", "mime"),
        [
            (b"hello", "application/pdf"),
            (b"%PDF-1.4", "image/png"),
            (b"%PDF-1.4", "text/plain"),
            (b"plain", "image/jpeg"),
        ],
    )
    def test_declared_type_must_match_content(self, data: bytes, mime: str) -> None:
        with pytest.raises(ExtractionError) as exc:
            resolve_kind(data, mime)
        assert exc.value.code == "FILE_TYPE_MISMATCH"

    def test_unsupported_type(self) -> None:
        with pytest.raises(ExtractionError) as exc:
            resolve_kind(b"x", "application/zip")
        assert exc.value.code == "UNSUPPORTED_FILE_TYPE"


class TestPdf:
    def test_pages_extracted_in_order(self) -> None:
        pdf = make_pdf(["Page one talks about limits.", "Page two talks about derivatives."])
        result = extract_text(pdf, "application/pdf", CFG, FakeOCR(available=False))
        assert result.page_count == 2
        assert [p.page_number for p in result.pages] == [1, 2]
        assert "limits" in result.pages[0].text
        assert "derivatives" in result.pages[1].text
        assert result.ocr_pages == []

    def test_tables_become_markdown(self) -> None:
        pdf = make_pdf(
            ["Squares of small numbers are listed below."],
            table_rows=[["n", "square"], ["2", "4"], ["3", "9"]],
        )
        text = extract_text(pdf, "application/pdf", CFG, FakeOCR(available=False)).pages[0].text
        assert "| n | square |" in text
        assert "| --- | --- |" in text
        assert "| 3 | 9 |" in text
        assert text.index("Squares") < text.index("| n | square |")

    def test_image_only_page_is_ocred(self) -> None:
        ocr = FakeOCR("Recognised text from a scanned page")
        pdf = make_pdf(["A normal text page with plenty of words in it."], image_page=True)
        result = extract_text(pdf, "application/pdf", CFG, ocr)
        assert result.ocr_pages == [2]
        assert result.pages[1].ocr is True
        assert result.pages[1].text == "Recognised text from a scanned page"
        assert ocr.calls == 1

    def test_missing_ocr_on_partial_scan_is_reported_not_fatal(self) -> None:
        pdf = make_pdf(["A normal text page with plenty of words in it."], image_page=True)
        result = extract_text(pdf, "application/pdf", CFG, FakeOCR(available=False))
        assert result.ocr_unavailable_pages == [2]
        assert result.pages[0].text

    def test_scanned_pdf_without_ocr_fails_clearly(self) -> None:
        pdf = make_pdf([""], image_page=True)
        with pytest.raises(ExtractionError) as exc:
            extract_text(pdf, "application/pdf", CFG, FakeOCR(available=False))
        assert exc.value.code == "OCR_UNAVAILABLE"

    def test_page_limit(self) -> None:
        pdf = make_pdf(["a", "b", "c"])
        with pytest.raises(ExtractionError) as exc:
            extract_text(pdf, "application/pdf", ContentProcessingSettings(max_pages=2), FakeOCR())
        assert exc.value.code == "TOO_MANY_PAGES"

    def test_corrupt_pdf(self) -> None:
        with pytest.raises(ExtractionError) as exc:
            extract_text(b"%PDF-1.4 garbage" * 10, "application/pdf", CFG, FakeOCR())
        assert exc.value.code == "PDF_UNREADABLE"


class TestImagesAndText:
    def test_image_is_ocred(self) -> None:
        result = extract_text(make_png(), "image/png", CFG, FakeOCR("Concept from a photo"))
        assert result.pages[0].text == "Concept from a photo"
        assert result.ocr_pages == [1]

    def test_image_without_ocr(self) -> None:
        with pytest.raises(ExtractionError) as exc:
            extract_text(make_png(), "image/png", CFG, FakeOCR(available=False))
        assert exc.value.code == "OCR_UNAVAILABLE"

    def test_unreadable_image(self) -> None:
        with pytest.raises(ExtractionError) as exc:
            extract_text(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "image/png", CFG, FakeOCR())
        assert exc.value.code == "IMAGE_UNREADABLE"

    def test_utf8_text_with_bom(self) -> None:
        result = extract_text("﻿Naïve café — ∑".encode(), "text/plain", CFG, FakeOCR())
        assert result.pages[0].text == "Naïve café — ∑"
        assert result.pages[0].page_number is None

    def test_cp1252_fallback(self) -> None:
        result = extract_text("café".encode("cp1252"), "text/plain", CFG, FakeOCR())
        assert result.pages[0].text == "café"

    def test_form_feeds_split_pages(self) -> None:
        result = extract_text(b"one\fTwo\f\fthree", "text/plain", CFG, FakeOCR())
        assert [(p.page_number, p.text) for p in result.pages] == [
            (1, "one"),
            (2, "Two"),
            (4, "three"),
        ]
        assert result.page_count == 4
        assert result.char_count == len("oneTwothree")

    def test_empty_text(self) -> None:
        with pytest.raises(ExtractionError) as exc:
            extract_text(b"   \n\n ", "text/plain", CFG, FakeOCR())
        assert exc.value.code == "NO_TEXT"


def test_tesseract_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    import pytesseract

    text_extractor._tesseract_available.cache_clear()
    monkeypatch.setattr(pytesseract, "get_tesseract_version", lambda: "5.3")
    monkeypatch.setattr(pytesseract, "image_to_string", lambda image, lang: f"text in {lang}")
    ocr = TesseractOCR("eng")
    assert ocr.available() is True
    assert ocr.image_to_text(object()) == "text in eng"
    text_extractor._tesseract_available.cache_clear()


def test_tesseract_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import pytesseract

    def missing() -> str:
        raise pytesseract.TesseractNotFoundError()

    text_extractor._tesseract_available.cache_clear()
    monkeypatch.setattr(pytesseract, "get_tesseract_version", missing)
    assert TesseractOCR().available() is False
    text_extractor._tesseract_available.cache_clear()


@pytest.mark.skipif(not TesseractOCR().available(), reason="tesseract binary not installed")
def test_real_tesseract_reads_an_image() -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1200, 200), "white")
    font = ImageFont.load_default(size=64)
    ImageDraw.Draw(image).text((30, 50), "Photosynthesis", fill="black", font=font)
    assert "photosynthesis" in TesseractOCR().image_to_text(image).lower()
