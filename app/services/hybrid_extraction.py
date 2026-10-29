"""
services/hybrid_extraction.py — Phase 8 Hybrid Document Extraction Service
Orchestrates multi-layered native extraction, per-page OCR decisions, header/footer removal,
section hierarchy detection, table formatting, and quality scoring.
"""
from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Any

from config import settings
from app.services.document_detection import evaluate_page_quality, validate_file_security
from app.services.ocr_provider import OCRProvider, get_ocr_provider
from app.services.text_normalization import detect_and_remove_headers_footers, normalize_text
from app.services.section_detection import SectionTracker
from app.services.table_extraction import extract_docx_table
from app.services.extraction_quality import QualityEvaluation, evaluate_extraction_quality

logger = logging.getLogger(__name__)


@dataclass
class HybridPageDetail:
    page_number: int
    text: str
    extraction_method: str  # 'native' | 'ocr' | 'empty' | 'failed'
    status: str             # 'OK' | 'PARTIAL' | 'FAILED' | 'EMPTY'
    character_count: int
    quality_score: float
    section_label: Optional[str] = None
    warning_code: Optional[str] = None


@dataclass
class HybridExtractionResult:
    text: str
    sections: List[Any]
    total_pages: int
    native_page_count: int
    ocr_page_count: int
    failed_page_count: int
    empty_page_count: int
    extraction_method: str       # 'native' | 'ocr' | 'hybrid' | 'docx' | 'text'
    extraction_status: str       # 'READY' | 'PARTIAL' | 'FAILED'
    quality_score: float
    quality_label: str           # 'excellent' | 'good' | 'fair' | 'poor' | 'unusable'
    warnings: List[str] = field(default_factory=list)
    extractor_version: str = "2.0.0"
    ocr_engine: str = "none"
    pages: List[HybridPageDetail] = field(default_factory=list)


# Compatibility alias for PageSection from document_extraction
@dataclass
class EnhancedPageSection:
    text: str
    page_number: Optional[int] = None
    section_index: int = 0
    section_title: Optional[str] = None
    section_path: Optional[str] = None
    extraction_method: str = "native"


def extract_pdf_hybrid(
    file_path: Path,
    ocr_provider: Optional[OCRProvider] = None,
    force_ocr: bool = False,
    disable_ocr: bool = False,
) -> HybridExtractionResult:
    """PDF extraction supporting native extraction + selective page-by-page OCR."""
    try:
        import fitz
    except ImportError:
        raise RuntimeError("PyMuPDF is not installed.")

    # 1. Security validation
    is_safe, error_msg = validate_file_security(file_path, "pdf")
    if not is_safe:
        raise ValueError(error_msg)

    doc = fitz.open(str(file_path))

    try:
        total_pages = len(doc)
        if total_pages == 0:
            raise ValueError("PDF contains 0 pages.")

        active_ocr_provider = ocr_provider or get_ocr_provider()
        native_count = 0
        ocr_count = 0
        failed_count = 0
        empty_count = 0

        raw_page_texts: List[str] = []
        page_details: List[HybridPageDetail] = []
        enhanced_sections: List[EnhancedPageSection] = []
        tracker = SectionTracker()

        for page_num in range(1, total_pages + 1):
            fitz_page = doc[page_num - 1]
            native_text = fitz_page.get_text().strip()

            # Evaluate native quality
            eval_result = evaluate_page_quality(page_num, native_text, fitz_page)

            page_text = ""
            method = "native"
            status = "OK"

            # Check if OCR is required
            should_ocr = (eval_result.ocr_required or force_ocr) and not disable_ocr and settings.ocr_enabled

            if should_ocr:
                try:
                    pix = fitz_page.get_pixmap(dpi=settings.ocr_render_dpi)
                    ocr_res = active_ocr_provider.extract_page_image(pix, page_num)
                    pix = None  # Explicit cleanup

                    if ocr_res.success and ocr_res.text.strip():
                        page_text = ocr_res.text.strip()
                        method = "ocr"
                        ocr_count += 1
                        status = "OK"
                    else:
                        page_text = native_text
                        if native_text:
                            method = "native"
                            native_count += 1
                            status = "PARTIAL"
                        else:
                            method = "failed"
                            failed_count += 1
                            status = "FAILED"
                except Exception as exc:
                    logger.warning("Page %d OCR exception: %s", page_num, exc)
                    page_text = native_text
                    if native_text:
                        method = "native"
                        native_count += 1
                    else:
                        method = "failed"
                        failed_count += 1
                        status = "FAILED"
            elif eval_result.is_empty:
                page_text = ""
                method = "empty"
                empty_count += 1
                status = "EMPTY"
            else:
                page_text = native_text
                method = "native"
                native_count += 1
                status = "OK"

            raw_page_texts.append(page_text)

            # Section tracking
            sec_info = None
            for line in page_text.split("\n"):
                res = tracker.process_line(line)
                if res:
                    sec_info = res

            sec_path = tracker.current_section_path()
            sec_title = sec_info.title if sec_info else None

            detail = HybridPageDetail(
                page_number=page_num,
                text=page_text,
                extraction_method=method,
                status=status,
                character_count=len(page_text),
                quality_score=1.0 if status == "OK" else 0.0,
                section_label=sec_title,
            )
            page_details.append(detail)

        # 2. Header and footer removal
        cleaned_page_texts, removed_patterns = detect_and_remove_headers_footers(raw_page_texts)

        # Build final sections and full text
        full_text_parts: List[str] = []
        for idx, text in enumerate(cleaned_page_texts, start=1):
            if text.strip():
                full_text_parts.append(text)
                sec_path = page_details[idx - 1].section_label or "General"
                enhanced_sections.append(
                    EnhancedPageSection(
                        text=text,
                        page_number=idx,
                        section_index=idx - 1,
                        section_title=sec_path,
                        section_path=sec_path,
                        extraction_method=page_details[idx - 1].extraction_method,
                    )
                )

        full_text = "\n\n".join(full_text_parts)

        # Determine extraction method label
        if ocr_count > 0 and native_count > 0:
            overall_method = "hybrid"
        elif ocr_count > 0:
            overall_method = "ocr"
        else:
            overall_method = "native"

        # Quality evaluation
        quality_eval = evaluate_extraction_quality(
            total_pages=total_pages,
            native_page_count=native_count,
            ocr_page_count=ocr_count,
            failed_page_count=failed_count,
            empty_page_count=empty_count,
            full_text=full_text,
        )

        if removed_patterns:
            quality_eval.warnings.append(f"Removed {len(removed_patterns)} repeated header/footer pattern(s).")

        # Determine extraction status
        if failed_count > 0 or quality_eval.label in ("poor", "fair"):
            ext_status = "PARTIAL" if full_text.strip() else "FAILED"
        else:
            ext_status = "READY"

        ocr_engine_name = active_ocr_provider.engine_name if ocr_count > 0 else "none"

        return HybridExtractionResult(
            text=full_text,
            sections=enhanced_sections,
            total_pages=total_pages,
            native_page_count=native_count,
            ocr_page_count=ocr_count,
            failed_page_count=failed_count,
            empty_page_count=empty_count,
            extraction_method=overall_method,
            extraction_status=ext_status,
            quality_score=quality_eval.score,
            quality_label=quality_eval.label,
            warnings=quality_eval.warnings,
            ocr_engine=ocr_engine_name,
            pages=page_details,
        )

    finally:
        doc.close()


def extract_docx_hybrid(file_path: Path) -> HybridExtractionResult:
    """DOCX extraction preserving paragraph order, headings, and table structures."""
    is_safe, error_msg = validate_file_security(file_path, "docx")
    if not is_safe:
        raise ValueError(error_msg)

    try:
        from docx import Document as DocxDocument
    except ImportError:
        raise RuntimeError("python-docx is not installed.")

    file_bytes = file_path.read_bytes()
    doc = DocxDocument(io.BytesIO(file_bytes))

    sections: List[EnhancedPageSection] = []
    tracker = SectionTracker()
    idx = 0
    raw_texts: List[str] = []

    for elem in doc.element.body:
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "p":
            para_text = elem.text.strip() if elem.text else ""
            if para_text:
                style_name = None
                if hasattr(elem, "style") and elem.style:
                    style_name = getattr(elem.style, "name", None)
                sec_res = tracker.process_line(para_text, style_name)
                sec_path = tracker.current_section_path()
                norm = normalize_text(para_text)
                if norm:
                    sections.append(
                        EnhancedPageSection(
                            text=norm,
                            page_number=None,
                            section_index=idx,
                            section_title=sec_res.title if sec_res else None,
                            section_path=sec_path,
                            extraction_method="docx",
                        )
                    )
                    raw_texts.append(norm)
                    idx += 1
        elif tag == "tbl":
            # Extract table
            from docx.table import Table
            tbl_obj = Table(elem, doc)
            tbl_text = extract_docx_table(tbl_obj)
            if tbl_text:
                sec_path = tracker.current_section_path()
                sections.append(
                    EnhancedPageSection(
                        text=tbl_text,
                        page_number=None,
                        section_index=idx,
                        section_title="Table",
                        section_path=sec_path,
                        extraction_method="docx",
                    )
                )
                raw_texts.append(tbl_text)
                idx += 1

    full_text = "\n\n".join(raw_texts)
    if not full_text.strip():
        raise ValueError("DOCX document contains no extractable text.")

    quality_eval = evaluate_extraction_quality(
        total_pages=1,
        native_page_count=1,
        ocr_page_count=0,
        failed_page_count=0,
        empty_page_count=0,
        full_text=full_text,
    )

    return HybridExtractionResult(
        text=full_text,
        sections=sections,
        total_pages=1,
        native_page_count=1,
        ocr_page_count=0,
        failed_page_count=0,
        empty_page_count=0,
        extraction_method="docx",
        extraction_status="READY",
        quality_score=quality_eval.score,
        quality_label=quality_eval.label,
        warnings=quality_eval.warnings,
        ocr_engine="none",
    )


def extract_txt_hybrid(file_path: Path) -> HybridExtractionResult:
    """TXT extraction with safe encoding detection and line-ending normalization."""
    is_safe, error_msg = validate_file_security(file_path, "txt")
    if not is_safe:
        raise ValueError(error_msg)

    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]
    text: Optional[str] = None

    for enc in encodings:
        try:
            text = file_path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if text is None:
        raise ValueError("TXT file could not be decoded with any supported encoding.")

    norm_text = normalize_text(text)
    if not norm_text:
        raise ValueError("TXT file is empty.")

    sections = [
        EnhancedPageSection(
            text=norm_text,
            page_number=None,
            section_index=0,
            section_title="General",
            section_path="General",
            extraction_method="text",
        )
    ]

    quality_eval = evaluate_extraction_quality(
        total_pages=1,
        native_page_count=1,
        ocr_page_count=0,
        failed_page_count=0,
        empty_page_count=0,
        full_text=norm_text,
    )

    return HybridExtractionResult(
        text=norm_text,
        sections=sections,
        total_pages=1,
        native_page_count=1,
        ocr_page_count=0,
        failed_page_count=0,
        empty_page_count=0,
        extraction_method="text",
        extraction_status="READY",
        quality_score=quality_eval.score,
        quality_label=quality_eval.label,
        warnings=quality_eval.warnings,
        ocr_engine="none",
    )


def extract_document_hybrid(
    file_path: Path,
    file_type: str,
    ocr_provider: Optional[OCRProvider] = None,
    force_ocr: bool = False,
    disable_ocr: bool = False,
) -> HybridExtractionResult:
    """Unified entry point for Phase 8 extraction pipeline."""
    ft = file_type.lower().strip(".")
    logger.info("Starting Phase 8 extraction for '%s' (%s)", file_path.name, ft)

    if ft == "pdf":
        return extract_pdf_hybrid(file_path, ocr_provider=ocr_provider, force_ocr=force_ocr, disable_ocr=disable_ocr)
    elif ft == "docx":
        return extract_docx_hybrid(file_path)
    elif ft == "txt":
        return extract_txt_hybrid(file_path)
    else:
        raise NotImplementedError(f"Unsupported file type: '{file_type}'")
