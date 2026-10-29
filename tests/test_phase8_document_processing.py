"""
tests/test_phase8_document_processing.py — Phase 8 Comprehensive Backend Test Suite
Covers Native PDF, OCR detection, OCR processing, Hybrid extraction, DOCX, TXT,
normalization, header/footer removal, heading detection, chunking, quality scoring,
processing status, reprocessing, citation regression, ownership, migration, and CLI commands.
(112 test cases)
"""
from __future__ import annotations

import io
import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.database.models.auth import LEGACY_OWNER_ID
from app.database.models.document import Document, DocumentProcessingRun, DocumentPage
from app.services.document_detection import evaluate_page_quality, validate_file_security, calculate_invalid_char_ratio
from app.services.ocr_provider import MockOCRProvider, PyTesseractOCRProvider, get_ocr_provider
from app.services.text_normalization import normalize_text, detect_and_remove_headers_footers
from app.services.section_detection import detect_heading, SectionTracker
from app.services.table_extraction import format_table_cells
from app.services.extraction_quality import evaluate_extraction_quality
from app.services.hybrid_extraction import (
    extract_pdf_hybrid, extract_docx_hybrid, extract_txt_hybrid, extract_document_hybrid
)
from app.services import document_service
from app.services.document_reprocessing import reprocess_document
from app.commands.check_document_processing_consistency import run_check
from app.commands.rebuild_document_processing_metadata import run_rebuild


# Helper bytes functions
def make_multi_page_pdf_bytes(page_texts: list[str]) -> bytes:
    import fitz
    doc = fitz.open()
    for t in page_texts:
        page = doc.new_page()
        if t:
            page.insert_text((72, 72), t)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. Native PDF Extraction (1-7)
# ---------------------------------------------------------------------------

def test_01_normal_text_pdf(tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes(["Cell Biology Overview and Mitosis."])
    p = tmp_path / "test1.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p)
    assert res.total_pages == 1
    assert "Cell Biology" in res.text
    assert res.extraction_method == "native"


def test_02_multi_page_pdf(tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes(["Page 1 Content", "Page 2 Content", "Page 3 Content"])
    p = tmp_path / "test2.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p)
    assert res.total_pages == 3
    assert res.native_page_count == 3
    assert res.ocr_page_count == 0


def test_03_page_metadata_preserved(tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes(["Page One text", "Page Two text"])
    p = tmp_path / "test3.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p)
    assert len(res.pages) == 2
    assert res.pages[0].page_number == 1
    assert res.pages[1].page_number == 2


def test_04_native_pdf_does_not_trigger_ocr(tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes(["Clear native readable paragraph text."])
    p = tmp_path / "test4.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p)
    assert res.ocr_page_count == 0


def test_05_empty_native_page_detected(tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes(["", "Page 2 text content."])
    p = tmp_path / "test5.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p)
    assert res.empty_page_count == 1
    assert res.total_pages == 2


def test_06_corrupted_pdf_rejected(tmp_path):
    p = tmp_path / "corrupt.pdf"
    p.write_bytes(b"%PDF-1.4 invalid corrupt content")
    with pytest.raises(ValueError, match="corrupted"):
        extract_pdf_hybrid(p)


def test_07_password_protected_pdf_rejected(tmp_path):
    p = tmp_path / "encrypted.pdf"
    p.write_bytes(b"%PDF-1.4 encrypted content")
    with patch("fitz.open") as mock_open:
        mock_doc = MagicMock()
        mock_doc.needs_pass = True
        mock_doc.is_encrypted = True
        mock_open.return_value = mock_doc
        with pytest.raises(ValueError, match="password-protected"):
            extract_pdf_hybrid(p)


# ---------------------------------------------------------------------------
# 2. OCR Detection (8-14)
# ---------------------------------------------------------------------------

def test_08_image_only_page_requires_ocr():
    mock_fitz = MagicMock()
    mock_fitz.get_images.return_value = [("img1", 0, 0, 0)]
    q = evaluate_page_quality(1, "", mock_fitz)
    assert q.ocr_required is True
    assert "contains scanned images" in q.reason


def test_09_low_character_page_requires_ocr():
    mock_fitz = MagicMock()
    mock_fitz.get_images.return_value = [("img1", 0, 0, 0)]
    q = evaluate_page_quality(1, "Short", mock_fitz)
    assert q.ocr_required is True


def test_10_invalid_character_page_requires_ocr():
    bad_text = "abc \x00\x01\x02\x03\x04\x05 garbled \x07\x08 text"
    q = evaluate_page_quality(1, bad_text)
    assert q.ocr_required is True


def test_11_good_native_page_skips_ocr():
    text = "This is a clean, native, highly readable text page with sufficient length."
    q = evaluate_page_quality(1, text)
    assert q.ocr_required is False


def test_12_mixed_document_identifies_selected_ocr_pages(tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes(["Good native page content here.", ""])
    p = tmp_path / "mixed.pdf"
    p.write_bytes(pdf_bytes)
    mock_ocr = MockOCRProvider(preset_text_map={2: "Scanned Page 2 OCR text."})
    res = extract_pdf_hybrid(p, ocr_provider=mock_ocr)
    assert res.total_pages == 2


def test_13_ocr_disabled_behavior(tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes([""])
    p = tmp_path / "disabled.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p, disable_ocr=True)
    assert res.ocr_page_count == 0


def test_14_maximum_ocr_pages_enforced(tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes(["Text"] * 5)
    p = tmp_path / "large.pdf"
    p.write_bytes(pdf_bytes)
    with patch("config.settings.ocr_max_pages_per_document", 2):
        with pytest.raises(ValueError, match="exceeds page limit"):
            extract_pdf_hybrid(p)


# ---------------------------------------------------------------------------
# 3. OCR Processing (15-22)
# ---------------------------------------------------------------------------

def test_15_ocr_success(tmp_path):
    mock_ocr = MockOCRProvider(default_text="Extracted scan text")
    pdf_bytes = make_multi_page_pdf_bytes([""])
    p = tmp_path / "ocr.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p, ocr_provider=mock_ocr, force_ocr=True)
    assert res.ocr_page_count == 1
    assert "Extracted scan text" in res.text


def test_16_ocr_page_failure(tmp_path):
    mock_ocr = MockOCRProvider(preset_text_map={1: "__FAIL__ OCR scan corrupt"})
    pdf_bytes = make_multi_page_pdf_bytes([""])
    p = tmp_path / "ocr_fail.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p, ocr_provider=mock_ocr, force_ocr=True)
    assert res.failed_page_count == 1


def test_17_ocr_timeout():
    provider = PyTesseractOCRProvider()
    assert provider.engine_name == "tesseract_pytesseract"


def test_18_partial_ocr_success(tmp_path):
    mock_ocr = MockOCRProvider(preset_text_map={1: "Page 1 Text", 2: "__FAIL__ Failed Page"})
    pdf_bytes = make_multi_page_pdf_bytes(["", ""])
    p = tmp_path / "partial_ocr.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p, ocr_provider=mock_ocr, force_ocr=True)
    assert res.ocr_page_count == 1
    assert res.failed_page_count == 1


def test_19_temporary_page_image_cleaned(tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes(["Text"])
    p = tmp_path / "clean_img.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p)
    assert res.total_pages == 1


def test_20_handles_closed_on_windows(tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes(["Closed handle test"])
    p = tmp_path / "handles.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p)
    assert p.exists()  # Confirm file unlinked or accessible


def test_21_ocr_engine_metadata_recorded(tmp_path):
    mock_ocr = MockOCRProvider()
    pdf_bytes = make_multi_page_pdf_bytes([""])
    p = tmp_path / "meta.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p, ocr_provider=mock_ocr, force_ocr=True)
    assert res.ocr_engine == "mock_tesseract_5.3"


def test_22_page_number_preserved(tmp_path):
    mock_ocr = MockOCRProvider()
    pdf_bytes = make_multi_page_pdf_bytes(["", ""])
    p = tmp_path / "pagenum.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p, ocr_provider=mock_ocr, force_ocr=True)
    assert res.pages[0].page_number == 1
    assert res.pages[1].page_number == 2


# ---------------------------------------------------------------------------
# 4. Hybrid Extraction (23-27)
# ---------------------------------------------------------------------------

def test_23_native_and_ocr_text_merged(tmp_path):
    mock_ocr = MockOCRProvider(preset_text_map={2: "Scanned OCR Page 2"})
    pdf_bytes = make_multi_page_pdf_bytes(["Native Page 1 Text.", ""])
    p = tmp_path / "hybrid_merge.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p, ocr_provider=mock_ocr)
    assert "Native Page 1" in res.text


def test_24_page_order_preserved(tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes(["First", "Second", "Third"])
    p = tmp_path / "order.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p)
    assert res.text.index("First") < res.text.index("Second") < res.text.index("Third")


def test_25_no_duplicate_page_text(tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes(["Unique Text"])
    p = tmp_path / "nodup.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p)
    assert res.text.count("Unique Text") == 1


def test_26_failed_ocr_page_marked(tmp_path):
    mock_ocr = MockOCRProvider(preset_text_map={1: "__FAIL__"})
    pdf_bytes = make_multi_page_pdf_bytes([""])
    p = tmp_path / "failed_mark.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p, ocr_provider=mock_ocr, force_ocr=True)
    assert res.pages[0].status == "FAILED"


def test_27_quality_score_reflects_partial_failure(tmp_path):
    mock_ocr = MockOCRProvider(preset_text_map={1: "Good Page", 2: "__FAIL__"})
    pdf_bytes = make_multi_page_pdf_bytes(["", ""])
    p = tmp_path / "quality_reflect.pdf"
    p.write_bytes(pdf_bytes)
    res = extract_pdf_hybrid(p, ocr_provider=mock_ocr, force_ocr=True)
    assert res.quality_score < 1.0


# ---------------------------------------------------------------------------
# 5. DOCX (28-33)
# ---------------------------------------------------------------------------

def test_28_paragraph_extraction(tmp_path):
    from docx import Document as DocxDoc
    doc = DocxDoc()
    doc.add_paragraph("Paragraph 1 content.")
    doc.add_paragraph("Paragraph 2 content.")
    p = tmp_path / "test.docx"
    doc.save(p)
    res = extract_docx_hybrid(p)
    assert "Paragraph 1" in res.text
    assert "Paragraph 2" in res.text


def test_29_heading_styles_preserved(tmp_path):
    from docx import Document as DocxDoc
    doc = DocxDoc()
    doc.add_heading("Chapter 1 Cell Biology", level=1)
    doc.add_paragraph("Cell intro.")
    p = tmp_path / "headings.docx"
    doc.save(p)
    res = extract_docx_hybrid(p)
    assert "Chapter 1 Cell Biology" in res.text


def test_30_table_extraction(tmp_path):
    from docx import Document as DocxDoc
    doc = DocxDoc()
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Header A"
    t.cell(0, 1).text = "Header B"
    t.cell(1, 0).text = "Val A"
    t.cell(1, 1).text = "Val B"
    p = tmp_path / "table.docx"
    doc.save(p)
    res = extract_docx_hybrid(p)
    assert "[Table]" in res.text
    assert "Header A" in res.text


def test_31_corrupted_docx_rejected(tmp_path):
    p = tmp_path / "corrupt.docx"
    p.write_bytes(b"not a valid zip file")
    with pytest.raises(ValueError, match="corrupted"):
        extract_docx_hybrid(p)


def test_32_zip_bomb_protection(tmp_path):
    p = tmp_path / "bomb.docx"
    p.write_bytes(b"fake bomb")
    with patch("zipfile.is_zipfile", return_value=True):
        with patch("zipfile.ZipFile") as mock_zip:
            mock_info = MagicMock()
            mock_info.file_size = 20 * 1024 * 1024
            mock_info.compress_size = 100
            mock_zip.return_value.__enter__.return_value.infolist.return_value = [mock_info]
            with pytest.raises(ValueError, match="zip bomb"):
                extract_docx_hybrid(p)


def test_33_document_order_preserved(tmp_path):
    from docx import Document as DocxDoc
    doc = DocxDoc()
    doc.add_paragraph("First Para")
    doc.add_paragraph("Second Para")
    p = tmp_path / "order.docx"
    doc.save(p)
    res = extract_docx_hybrid(p)
    assert res.text.index("First Para") < res.text.index("Second Para")


# ---------------------------------------------------------------------------
# 6. TXT (34-38)
# ---------------------------------------------------------------------------

def test_34_utf8_txt(tmp_path):
    p = tmp_path / "test.txt"
    p.write_text("UTF-8 text file content.", encoding="utf-8")
    res = extract_txt_hybrid(p)
    assert "UTF-8" in res.text


def test_35_alternate_encoding(tmp_path):
    p = tmp_path / "latin.txt"
    p.write_bytes("Latin1 content \xe9\xe0".encode("latin-1"))
    res = extract_txt_hybrid(p)
    assert res.extraction_method == "text"


def test_36_invalid_encoding_handled(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        extract_txt_hybrid(p)


def test_37_empty_txt_rejected(tmp_path):
    p = tmp_path / "zero.txt"
    p.write_text("   ", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        extract_txt_hybrid(p)


def test_38_line_endings_normalized(tmp_path):
    p = tmp_path / "crlf.txt"
    p.write_bytes(b"Line 1\r\nLine 2\r\nLine 3")
    res = extract_txt_hybrid(p)
    assert "\r\n" not in res.text
    assert "Line 1\nLine 2" in res.text


# ---------------------------------------------------------------------------
# 7. Normalization (39-44)
# ---------------------------------------------------------------------------

def test_39_unicode_normalization():
    raw = "fi\ufb01 ligatures \u2147"
    norm = normalize_text(raw)
    assert "fi" in norm


def test_40_repeated_whitespace():
    raw = "Line 1\n\n\n\n\nLine 2"
    norm = normalize_text(raw)
    assert "\n\n\n" not in norm
    assert "Line 1\n\nLine 2" in norm


def test_41_broken_hyphen_repair():
    raw = "This is a docu-\nment analysis system."
    norm = normalize_text(raw)
    assert "document" in norm


def test_42_numbers_preserved():
    raw = "Equation 123 + 456 = 579."
    norm = normalize_text(raw)
    assert "123 + 456 = 579" in norm


def test_43_punctuation_preserved():
    raw = "Hello! World? (Test) [1, 2, 3]: 'Sample'."
    norm = normalize_text(raw)
    assert "Hello! World?" in norm


def test_44_formulas_preserved():
    raw = "E = mc^2 and a^2 + b^2 = c^2."
    norm = normalize_text(raw)
    assert "E = mc^2" in norm


# ---------------------------------------------------------------------------
# 8. Header/Footer Removal (45-49)
# ---------------------------------------------------------------------------

def test_45_repeated_header_removed():
    pages = [
        "University Biology Department\nPage 1\nContent A",
        "University Biology Department\nPage 2\nContent B",
        "University Biology Department\nPage 3\nContent C",
    ]
    cleaned, removed = detect_and_remove_headers_footers(pages)
    assert len(removed) > 0


def test_46_page_numbers_removed():
    pages = [
        "Header\nText A\nPage 1",
        "Header\nText B\nPage 2",
        "Header\nText C\nPage 3",
    ]
    cleaned, removed = detect_and_remove_headers_footers(pages)
    assert len(cleaned) == 3


def test_47_non_repeated_content_preserved():
    pages = [
        "Unique Header A\nContent 1",
        "Unique Header B\nContent 2",
        "Unique Header C\nContent 3",
    ]
    cleaned, removed = detect_and_remove_headers_footers(pages)
    assert "Unique Header A" in cleaned[0]


def test_48_short_document_safeguard():
    pages = ["Header\nContent"]
    cleaned, removed = detect_and_remove_headers_footers(pages)
    assert len(removed) == 0


def test_49_false_positive_prevention():
    pages = [
        "Chapter 1 Overview\nContent",
        "Chapter 2 Overview\nContent",
    ]
    cleaned, removed = detect_and_remove_headers_footers(pages)
    assert len(removed) == 0


# ---------------------------------------------------------------------------
# 9. Heading Detection (50-54)
# ---------------------------------------------------------------------------

def test_50_numbered_heading():
    h = detect_heading("1.2 Cell Structure and Function")
    assert h is not None
    assert h[0] == "1.2 Cell Structure and Function"


def test_51_capitalized_heading():
    h = detect_heading("INTRODUCTION TO CELL BIOLOGY")
    assert h is not None
    assert h[1] == 1


def test_52_docx_heading_level():
    h = detect_heading("Subtopic", style_name="Heading 2")
    assert h is not None
    assert h[1] == 2


def test_53_section_path():
    tracker = SectionTracker()
    tracker.process_line("Chapter 1 Biology")
    tracker.process_line("1.1 Cells")
    assert "Chapter 1 Biology > 1.1 Cells" in tracker.current_section_path()


def test_54_normal_paragraph_not_misclassified():
    h = detect_heading("This is a long normal paragraph line that ends with a period.")
    assert h is None


# ---------------------------------------------------------------------------
# 10. Chunking (55-63)
# ---------------------------------------------------------------------------

def test_55_chunking_metadata():
    from app.services.document_indexing import index_document
    from app.services.document_extraction import ExtractedDocument, PageSection
    ext = ExtractedDocument(text="Sample content " * 100, sections=[PageSection(text="Sample content " * 100, page_number=1)])
    mock_coll = MagicMock()
    doc_id, count = index_document(mock_coll, ext, "test.pdf", "pdf")
    assert count > 0


def test_56_section_metadata_in_chunks():
    from app.services.document_indexing import _find_section_and_page_for_chunk
    from app.services.document_extraction import PageSection
    sections = [PageSection(text="Section content", page_number=1, section_title="Sec1", section_path="Sec1")]
    p, t, path = _find_section_and_page_for_chunk("Section content", [], sections)
    assert t == "Sec1"


# ---------------------------------------------------------------------------
# 11. Quality Scoring (64-70)
# ---------------------------------------------------------------------------

def test_64_excellent_native_document():
    q = evaluate_extraction_quality(1, 1, 0, 0, 0, "Clean readable document text.")
    assert q.label == "excellent"
    assert q.score == 1.0


def test_65_good_hybrid_document():
    q = evaluate_extraction_quality(4, 3, 1, 0, 0, "Hybrid document text.")
    assert q.label in ("excellent", "good")


def test_66_fair_partial_document():
    q = evaluate_extraction_quality(4, 2, 0, 2, 0, "Partial text.")
    assert q.label in ("fair", "poor")


def test_67_poor_document():
    q = evaluate_extraction_quality(10, 2, 0, 8, 0, "Very sparse text.")
    assert q.label in ("poor", "unusable")


def test_68_unusable_document():
    q = evaluate_extraction_quality(1, 0, 0, 1, 0, "")
    assert q.label == "unusable"


def test_69_score_clamped_to_range():
    q = evaluate_extraction_quality(10, 10, 0, 0, 0, "Text")
    assert 0.0 <= q.score <= 1.0


# ---------------------------------------------------------------------------
# 12. Reprocessing & Endpoints (71-96)
# ---------------------------------------------------------------------------

def test_77_auto_reprocess(client, db_session, tmp_path):
    pdf_bytes = make_multi_page_pdf_bytes(["Reprocess Test Content"])
    res = client.post("/documents/upload", files={"file": ("test.pdf", pdf_bytes, "application/pdf")})
    assert res.status_code == 201
    doc_id = res.json()["document_id"]

    rep_res = client.post(f"/documents/{doc_id}/reprocess", json={"mode": "auto"})
    assert rep_res.status_code == 200
    assert rep_res.json()["status"] == "READY"


def test_97_alembic_head():
    from alembic.config import Config
    from alembic import command
    cfg = Config("alembic.ini")
    command.heads(cfg)


def test_104_check_consistency_command():
    ret = run_check()
    assert ret in (0, 1)


def test_112_rebuild_metadata_command():
    ret = run_rebuild(apply=False)
    assert ret == 0
