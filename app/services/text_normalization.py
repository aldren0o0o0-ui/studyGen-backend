"""
services/text_normalization.py — Phase 8 Text Normalization & Header/Footer Removal
Provides Unicode normalization, hyphen repair, whitespace cleaning, and cross-page
repeated header/footer pattern detection and removal.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import List, Tuple, Set

logger = logging.getLogger(__name__)


def normalize_text(text: str) -> str:
    """
    Standard text normalization:
      - Unicode NFKC normalization
      - Line ending normalization (\r\n -> \n)
      - Broken hyphenation repair at line ends (e.g. 'docu-\nment' -> 'document')
      - Excess whitespace and blank line collapsing (max 2 consecutive newlines)
    """
    if not text:
        return ""

    # 1. Unicode normalization (NFKC)
    text = unicodedata.normalize("NFKC", text)

    # 2. Line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 3. Repair broken hyphenation at line breaks: word-\nword -> wordword
    # Match lowercase/uppercase letters, hyphen, newline, lowercase letters
    text = re.sub(r"(\b[A-Za-z]{2,})-\n([a-z]{2,}\b)", r"\1\2", text)

    # 4. Remove control characters except newline and tab
    text = "".join(ch for ch in text if ch in ("\n", "\t") or unicodedata.category(ch)[0] != "C")

    # 5. Trim trailing whitespace per line
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)

    # 6. Collapse >2 consecutive blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def detect_and_remove_headers_footers(page_texts: List[str], repetition_threshold: float = 0.5) -> Tuple[List[str], List[str]]:
    """
    Detects lines repeated across multiple pages at the very top (header)
    or bottom (footer) and strips them.

    Args:
        page_texts: List of extracted text strings per page.
        repetition_threshold: Ratio of pages a line must appear on to be considered header/footer (default 50%).

    Returns:
        (cleaned_page_texts, removed_patterns)
    """
    if len(page_texts) < 3:
        # Repetition detection is unreliable for < 3 pages — skip to prevent false positives
        return page_texts, []

    total_pages = len(page_texts)
    header_counts: dict[str, int] = {}
    footer_counts: dict[str, int] = {}

    for text in page_texts:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if not lines:
            continue

        # Check top 2 lines for header candidate
        top_lines = lines[:2]
        for line in top_lines:
            # Mask standalone page numbers like "Page 1", "1", "- 1 -"
            masked = re.sub(r"\b(page\s*)?\d+\b", "[PAGE_NUM]", line.lower()).strip()
            if len(masked) > 3 and not re.match(r"^\d+$", masked):
                header_counts[masked] = header_counts.get(masked, 0) + 1

        # Check bottom 2 lines for footer candidate
        bottom_lines = lines[-2:]
        for line in bottom_lines:
            masked = re.sub(r"\b(page\s*)?\d+\b", "[PAGE_NUM]", line.lower()).strip()
            if len(masked) > 3:
                footer_counts[masked] = footer_counts.get(masked, 0) + 1

    # Identify headers/footers exceeding threshold
    target_count = max(2, int(total_pages * repetition_threshold))
    repeated_headers = {pattern for pattern, count in header_counts.items() if count >= target_count}
    repeated_footers = {pattern for pattern, count in footer_counts.items() if count >= target_count}
    all_repeated = repeated_headers | repeated_footers

    if not all_repeated:
        return page_texts, []

    removed_patterns = list(all_repeated)
    logger.info("Detected %d repeated header/footer pattern(s)", len(removed_patterns))

    cleaned_pages: List[str] = []
    for text in page_texts:
        lines = text.split("\n")
        filtered_lines = []
        for line in lines:
            normalized_line = line.strip()
            masked = re.sub(r"\b(page\s*)?\d+\b", "[PAGE_NUM]", normalized_line.lower()).strip()
            if masked in all_repeated and len(lines) > 3:
                continue  # Skip repeated header/footer line
            filtered_lines.append(line)

        cleaned_text = "\n".join(filtered_lines)
        cleaned_pages.append(normalize_text(cleaned_text))

    return cleaned_pages, removed_patterns
