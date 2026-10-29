"""
services/extraction_quality.py — Phase 8 Extraction Quality Model
Computes a deterministic quality score (0.0 - 1.0) and quality label based on page
usability, OCR results, character count, and invalid character ratio.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from app.services.document_detection import calculate_invalid_char_ratio


@dataclass
class QualityEvaluation:
    score: float
    label: str  # 'excellent' | 'good' | 'fair' | 'poor' | 'unusable'
    warnings: List[str] = field(default_factory=list)


def evaluate_extraction_quality(
    total_pages: int,
    native_page_count: int,
    ocr_page_count: int,
    failed_page_count: int,
    empty_page_count: int,
    full_text: str,
) -> QualityEvaluation:
    """
    Computes a deterministic 0.0 to 1.0 extraction quality score and user-friendly label.
    """
    warnings: List[str] = []

    if total_pages <= 0:
        if full_text and len(full_text.strip()) > 50:
            return QualityEvaluation(score=1.0, label="excellent", warnings=[])
        return QualityEvaluation(score=0.0, label="unusable", warnings=["Document contains no usable text."])

    usable_pages = native_page_count + ocr_page_count
    usability_ratio = usable_pages / float(total_pages)

    invalid_ratio = calculate_invalid_char_ratio(full_text)

    # Penalties
    ocr_fail_penalty = (failed_page_count / float(total_pages)) * 0.5
    invalid_char_penalty = invalid_ratio * 0.5

    raw_score = usability_ratio - ocr_fail_penalty - invalid_char_penalty
    score = max(0.0, min(1.0, round(raw_score, 2)))

    # Warnings collection
    if failed_page_count > 0:
        warnings.append(f"{failed_page_count} page(s) could not be read during extraction.")
    if ocr_page_count > 0:
        warnings.append(f"{ocr_page_count} page(s) were scanned using OCR.")
    if empty_page_count > 0 and empty_page_count > total_pages * 0.3:
        warnings.append(f"{empty_page_count} empty page(s) were detected.")
    if invalid_ratio > 0.05:
        warnings.append("Document contains unusual or unreadable characters.")
    if not full_text.strip():
        score = 0.0
        warnings.append("No extractable text found in document.")

    # Quality Label Assignment
    if score >= 0.90:
        label = "excellent"
    elif score >= 0.75:
        label = "good"
    elif score >= 0.50:
        label = "fair"
    elif score >= 0.25:
        label = "poor"
    else:
        label = "unusable"

    return QualityEvaluation(score=score, label=label, warnings=warnings)
