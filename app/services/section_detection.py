"""
services/section_detection.py — Phase 8 Heading Detection & Section Hierarchy
Infers headings from text characteristics (short lines, numbering, capitalization, font style)
and builds section hierarchy metadata (e.g. "Chapter 1 > 1.2 Mitosis").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SectionInfo:
    title: str
    level: int  # 1 for H1, 2 for H2, 3 for H3
    section_path: str  # e.g., "Chapter 1 > 1.2 Cell Division"


# Heading detection regex patterns
NUMBERED_HEADING_REGEX = re.compile(
    r"^((?:Chapter|Section|Part)\s+\d+|(?:[0-9]{1,2}\.){1,3}[0-9]{0,2})\s+([A-Z0-9].*)",
    re.IGNORECASE
)
ALL_CAPS_HEADING_REGEX = re.compile(r"^[A-Z0-9\s\,\-\:]{4,60}$")
TITLE_CASE_HEADING_REGEX = re.compile(r"^[A-Z][a-z0-9]+(?:\s+[A-Z0-9][a-z0-9]+){0,5}$")


def detect_heading(line: str, style_name: Optional[str] = None) -> Optional[Tuple[str, int]]:
    """
    Detects if a line of text is a heading.
    Returns (heading_title, level) or None.
    """
    line_clean = line.strip()
    if not line_clean or len(line_clean) > 80 or line_clean.endswith("."):
        return None

    # 1. DOCX explicit style detection
    if style_name:
        style_lower = style_name.lower()
        if "heading 1" in style_lower or style_lower == "h1":
            return line_clean, 1
        elif "heading 2" in style_lower or style_lower == "h2":
            return line_clean, 2
        elif "heading 3" in style_lower or style_lower == "h3":
            return line_clean, 3

    # 2. Numbered pattern (e.g., "1.2 Cell Division" or "Chapter 3: Mitosis")
    m = NUMBERED_HEADING_REGEX.match(line_clean)
    if m:
        prefix = m.group(1).lower()
        if "chapter" in prefix or "part" in prefix or not "." in prefix:
            level = 1
        elif prefix.count(".") == 1:
            level = 2
        else:
            level = 3
        return line_clean, level

    # 3. All Caps short line (e.g. "INTRODUCTION", "CELL BIOLOGY OVERVIEW")
    if ALL_CAPS_HEADING_REGEX.match(line_clean) and any(c.isalpha() for c in line_clean):
        return line_clean, 1

    # 4. Short title case line without trailing period
    if TITLE_CASE_HEADING_REGEX.match(line_clean) and len(line_clean.split()) <= 6:
        return line_clean, 2

    return None


class SectionTracker:
    """Tracks active section path across paragraphs and pages."""

    def __init__(self):
        self._hierarchy: dict[int, str] = {}  # level -> title

    def process_line(self, line: str, style_name: Optional[str] = None) -> Optional[SectionInfo]:
        detected = detect_heading(line, style_name)
        if not detected:
            return None

        title, level = detected
        # Clear deeper hierarchy levels
        keys_to_remove = [k for k in self._hierarchy.keys() if k >= level]
        for k in keys_to_remove:
            del self._hierarchy[k]

        self._hierarchy[level] = title
        path_parts = [self._hierarchy[k] for k in sorted(self._hierarchy.keys())]
        section_path = " > ".join(path_parts)

        return SectionInfo(title=title, level=level, section_path=section_path)

    def current_section_path(self) -> str:
        if not self._hierarchy:
            return "General"
        return " > ".join(self._hierarchy[k] for k in sorted(self._hierarchy.keys()))
