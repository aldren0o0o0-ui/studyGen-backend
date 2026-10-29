"""
services/document_detection.py — Phase 8 Document Characteristics & OCR Decision Engine
Enforces security limits (encrypted PDF, ZIP bomb, corrupted files) and evaluates
per-page native extraction quality to decide whether OCR is required.
"""
from __future__ import annotations

import logging
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Any

from config import settings

logger = logging.getLogger(__name__)

PRINTABLE_CHARS = set(string.printable)


@dataclass
class PageQualityCheck:
    page_number: int
    text: str
    char_count: int
    invalid_char_ratio: float
    is_empty: bool
    ocr_required: bool
    reason: str


def validate_file_security(file_path: Path, file_type: str) -> Tuple[bool, Optional[str]]:
    """
    Security check for encrypted PDFs, corrupted files, and DOCX zip bomb payloads.
    Returns (is_safe, error_message).
    """
    ft = file_type.lower().strip(".")

    # 1. PDF security check
    if ft == "pdf":
        doc = None
        try:
            import fitz
            doc = fitz.open(str(file_path))
            if doc.needs_pass or doc.is_encrypted:
                return False, "This PDF is password-protected. Remove the password and upload it again."
            if len(doc) > settings.ocr_max_pages_per_document:
                return False, f"Document exceeds page limit of {settings.ocr_max_pages_per_document} pages (found {len(doc)})."
        except Exception as exc:
            if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
                return False, "This PDF is password-protected. Remove the password and upload it again."
            return False, f"Cannot open PDF (possibly corrupted): {exc}"
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    # 2. DOCX zip bomb & security check
    elif ft == "docx":
        try:
            import io
            import zipfile
            file_bytes = file_path.read_bytes()
            bytes_stream = io.BytesIO(file_bytes)
            if not zipfile.is_zipfile(bytes_stream):
                return False, "DOCX file is corrupted or not a valid archive."
            
            total_uncompressed = 0
            with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
                for info in zf.infolist():
                    total_uncompressed += info.file_size
                    # Zip bomb check: > 100MB uncompressed or compression ratio > 100x
                    if info.compress_size > 0:
                        ratio = info.file_size / float(info.compress_size)
                        if ratio > 100 and info.file_size > 10 * 1024 * 1024:
                            return False, "DOCX file rejected: possible decompression payload or zip bomb."
            if total_uncompressed > 100 * 1024 * 1024:
                return False, "DOCX file rejected: uncompressed payload exceeds safety limit (100MB)."
        except Exception as exc:
            return False, f"Cannot open DOCX (possibly corrupted): {exc}"

    # 3. TXT safety check
    elif ft == "txt":
        size = file_path.stat().st_size
        if size == 0:
            return False, "TXT file is empty."
        if size > settings.max_file_size_bytes:
            return False, f"File too large. Max: {settings.max_file_size_mb} MB."

    return True, None


def calculate_invalid_char_ratio(text: str) -> float:
    """Calculate ratio of non-printable or replacement characters in text."""
    if not text:
        return 0.0
    invalid_count = sum(1 for c in text if c not in PRINTABLE_CHARS and c not in ("\n", "\r", "\t"))
    return invalid_count / len(text)


def evaluate_page_quality(page_number: int, native_text: str, fitz_page: Optional[Any] = None) -> PageQualityCheck:
    """
    Evaluates whether native extracted text for a PDF page is sufficient
    or if OCR is required.
    """
    text = (native_text or "").strip()
    char_count = len(text)

    if char_count == 0:
        # Check if page contains images
        has_images = False
        if fitz_page is not None and hasattr(fitz_page, "get_images"):
            try:
                images = fitz_page.get_images()
                has_images = len(images) > 0
            except Exception:
                has_images = False
        
        if has_images:
            return PageQualityCheck(
                page_number=page_number,
                text="",
                char_count=0,
                invalid_char_ratio=0.0,
                is_empty=False,
                ocr_required=True,
                reason="Page contains scanned images but zero native text",
            )
        else:
            return PageQualityCheck(
                page_number=page_number,
                text="",
                char_count=0,
                invalid_char_ratio=0.0,
                is_empty=True,
                ocr_required=False,
                reason="Empty page with no text or images",
            )

    # Calculate invalid character ratio
    invalid_ratio = calculate_invalid_char_ratio(text)

    # Decision 1: Low character count threshold
    if char_count < settings.ocr_min_native_characters:
        # If the page has an image background, OCR is required
        has_images = False
        if fitz_page is not None and hasattr(fitz_page, "get_images"):
            try:
                has_images = len(fitz_page.get_images()) > 0
            except Exception:
                has_images = False

        if has_images or char_count < 10:
            return PageQualityCheck(
                page_number=page_number,
                text=text,
                char_count=char_count,
                invalid_char_ratio=invalid_ratio,
                is_empty=False,
                ocr_required=True,
                reason=f"Native text below character threshold ({char_count} < {settings.ocr_min_native_characters})",
            )

    # Decision 2: High proportion of invalid or garbled characters
    if invalid_ratio > settings.ocr_invalid_character_ratio:
        return PageQualityCheck(
            page_number=page_number,
            text=text,
            char_count=char_count,
            invalid_char_ratio=invalid_ratio,
            is_empty=False,
            ocr_required=True,
            reason=f"High ratio of invalid characters ({invalid_ratio:.2f} > {settings.ocr_invalid_character_ratio:.2f})",
        )

    # Native text is sufficient
    return PageQualityCheck(
        page_number=page_number,
        text=text,
        char_count=char_count,
        invalid_char_ratio=invalid_ratio,
        is_empty=False,
        ocr_required=False,
        reason="Native text quality OK",
    )
