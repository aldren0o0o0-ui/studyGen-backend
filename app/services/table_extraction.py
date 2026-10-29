"""
services/table_extraction.py — Phase 8 Table Extraction & Structured Text Preservation
Converts DOCX and PDF tables into clean, human-readable structured markdown/text format.
"""
from __future__ import annotations

from typing import List, Any, Optional


def format_table_cells(matrix: List[List[str]]) -> str:
    """
    Formats a 2D matrix of table cell strings into a clean Markdown table format:
      [Table]
      | Header 1 | Header 2 |
      | --- | --- |
      | Value A  | Value B  |
      [/Table]
    """
    if not matrix or not any(matrix):
        return ""

    # Remove completely empty rows
    valid_rows = [row for row in matrix if any(cell.strip() for cell in row)]
    if not valid_rows:
        return ""

    # Determine max columns
    max_cols = max(len(row) for row in valid_rows)
    # Pad shorter rows
    padded_rows = [row + [""] * (max_cols - len(row)) for row in valid_rows]

    lines = ["[Table]"]
    header = padded_rows[0]
    lines.append("| " + " | ".join(c.strip().replace("\n", " ") for c in header) + " |")
    lines.append("| " + " | ".join("---" for _ in range(max_cols)) + " |")

    for row in padded_rows[1:]:
        lines.append("| " + " | ".join(c.strip().replace("\n", " ") for c in row) + " |")

    lines.append("[/Table]")
    return "\n".join(lines)


def extract_docx_table(docx_table: Any) -> str:
    """Extract cell text from python-docx Table object into structured table string."""
    try:
        matrix: List[List[str]] = []
        for row in docx_table.rows:
            row_cells = [cell.text.strip() for cell in row.cells]
            matrix.append(row_cells)
        return format_table_cells(matrix)
    except Exception:
        return ""
