"""
document_extraction.py — Phase 8 Compatibility Adapter
Delegates to hybrid_extraction.py while maintaining original dataclasses and signatures.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from app.services.hybrid_extraction import extract_document_hybrid

logger = logging.getLogger(__name__)


@dataclass
class PageSection:
    """Represents one logical section extracted from the document."""
    text: str
    page_number: Optional[int] = None   # PDF page; None for DOCX/TXT
    section_index: int = 0              # paragraph / page index
    section_title: Optional[str] = None
    section_path: Optional[str] = None
    extraction_method: str = "native"


@dataclass
class ExtractedDocument:
    text: str                           # full concatenated text
    sections: List[PageSection] = field(default_factory=list)
    page_count: Optional[int] = None    # available for PDFs only
    # Phase 8 metadata
    extraction_method: str = "native"
    extraction_status: str = "READY"
    quality_score: float = 1.0
    quality_label: str = "excellent"
    warnings: List[str] = field(default_factory=list)


def extract_document(file_path: Path, file_type: str) -> ExtractedDocument:
    """
    Entry point used by the document-indexing service and tests.
    Delegates to Phase 8 hybrid extraction engine.
    """
    res = extract_document_hybrid(file_path, file_type)
    if not res.text.strip():
        raise ValueError("PDF contains no extractable text (may be a scanned image).")

    sections = [
        PageSection(
            text=sec.text,
            page_number=sec.page_number,
            section_index=sec.section_index,
            section_title=getattr(sec, "section_title", None),
            section_path=getattr(sec, "section_path", None),
            extraction_method=getattr(sec, "extraction_method", "native"),
        )
        for sec in res.sections
    ]

    return ExtractedDocument(
        text=res.text,
        sections=sections,
        page_count=res.total_pages,
        extraction_method=res.extraction_method,
        extraction_status=res.extraction_status,
        quality_score=res.quality_score,
        quality_label=res.quality_label,
        warnings=res.warnings,
    )
