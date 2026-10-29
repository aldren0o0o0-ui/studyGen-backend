"""
app/services/study_guide_validation_service.py — Wave 5 Study Guide Validation & Source Provenance

Responsibilities:
  - Parses and validates LLM section JSON output
  - Verifies and maps server-side source aliases (SOURCE_N) to concrete document metadata
  - Filters out fake or unsupported source labels (e.g. SOURCE_999)
  - Deduplicates items within section and checks for near-duplicate redundancy
  - Renders unified, high-quality Markdown for reading
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.question_validation import SourceReference

logger = logging.getLogger(__name__)


@dataclass
class ValidatedSectionItem:
    title: str
    content: str
    additional_info: Optional[str]
    source_references: List[SourceReference]


@dataclass
class ValidatedSectionResult:
    is_valid: bool
    section_type: str
    title: str
    content_markdown: str
    structured_items: List[ValidatedSectionItem]
    source_references: List[SourceReference]
    insufficient_evidence: bool = False
    error_message: Optional[str] = None


def _token_jaccard(a: str, b: str) -> float:
    set_a = set(re.findall(r"\b\w{3,}\b", a.lower()))
    set_b = set(re.findall(r"\b\w{3,}\b", b.lower()))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def extract_json_payload(raw_text: str) -> dict:
    """Extracts JSON object from raw LLM output, handling markdown code fences."""
    cleaned = raw_text.strip()
    # Strip markdown block if present
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
    if match:
        cleaned = match.group(1).strip()

    # Attempt direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try finding outermost braces
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


class StudyGuideValidationService:
    @classmethod
    def validate_section(
        cls,
        raw_output: str,
        section_type: str,
        section_title: str,
        source_map: Dict[str, SourceReference],
        *,
        previous_item_titles: Optional[Set[str]] = None,
        near_dup_threshold: float = 0.80,
    ) -> ValidatedSectionResult:
        """
        Validates LLM-generated section content against grounding rules and source references.
        """
        try:
            data = extract_json_payload(raw_output)
        except Exception as exc:
            return ValidatedSectionResult(
                is_valid=False,
                section_type=section_type,
                title=section_title,
                content_markdown="",
                structured_items=[],
                source_references=[],
                error_message=f"JSON decoding failed: {exc}",
            )

        if not isinstance(data, dict):
            return ValidatedSectionResult(
                is_valid=False,
                section_type=section_type,
                title=section_title,
                content_markdown="",
                structured_items=[],
                source_references=[],
                error_message="Root output is not a JSON object",
            )

        # Check insufficient evidence flag
        if data.get("insufficient_evidence") is True or not data.get("items"):
            return ValidatedSectionResult(
                is_valid=True,
                section_type=section_type,
                title=section_title,
                content_markdown="*(Insufficient evidence in source documents for this section)*",
                structured_items=[],
                source_references=[],
                insufficient_evidence=True,
            )

        raw_items = data.get("items", [])
        if not isinstance(raw_items, list) or len(raw_items) == 0:
            return ValidatedSectionResult(
                is_valid=True,
                section_type=section_type,
                title=section_title,
                content_markdown="*(No items generated)*",
                structured_items=[],
                source_references=[],
                insufficient_evidence=True,
            )

        validated_items: List[ValidatedSectionItem] = []
        section_sources_set: Set[str] = set()
        section_sources_list: List[SourceReference] = []
        seen_titles: Set[str] = set(previous_item_titles or set())

        for item in raw_items:
            if not isinstance(item, dict):
                continue

            title = str(item.get("title", "")).strip()
            content = str(item.get("content", "")).strip()
            add_info = str(item.get("additional_info", "")).strip() if item.get("additional_info") else None

            if not title or not content:
                continue

            # Check duplicate / near-duplicate title
            norm_title = title.lower()
            if norm_title in seen_titles:
                continue
            is_dup = False
            for prev in seen_titles:
                if _token_jaccard(norm_title, prev) >= near_dup_threshold:
                    is_dup = True
                    break
            if is_dup:
                continue

            # Validate and map source references
            item_source_labels = item.get("source_labels", [])
            item_sources: List[SourceReference] = []

            if isinstance(item_source_labels, list):
                for label in item_source_labels:
                    clean_label = str(label).strip().upper()
                    if clean_label in source_map:
                        src_ref = source_map[clean_label]
                        item_sources.append(src_ref)
                        src_key = f"{src_ref.document_id}:{src_ref.chunk_index}"
                        if src_key not in section_sources_set:
                            section_sources_set.add(src_key)
                            section_sources_list.append(src_ref)

            # If no valid sources attached but sources exist in map, attach default primary source
            if not item_sources and source_map:
                first_src = next(iter(source_map.values()))
                item_sources.append(first_src)
                src_key = f"{first_src.document_id}:{first_src.chunk_index}"
                if src_key not in section_sources_set:
                    section_sources_set.add(src_key)
                    section_sources_list.append(first_src)

            seen_titles.add(norm_title)
            validated_items.append(
                ValidatedSectionItem(
                    title=title,
                    content=content,
                    additional_info=add_info,
                    source_references=item_sources,
                )
            )

        if not validated_items:
            return ValidatedSectionResult(
                is_valid=True,
                section_type=section_type,
                title=section_title,
                content_markdown="*(No valid items after validation)*",
                structured_items=[],
                source_references=[],
                insufficient_evidence=True,
            )

        # Build clean Markdown content
        raw_markdown = str(data.get("summary_markdown", "")).strip()
        rendered_markdown = cls._render_section_markdown(section_type, section_title, validated_items, raw_markdown)

        return ValidatedSectionResult(
            is_valid=True,
            section_type=section_type,
            title=section_title,
            content_markdown=rendered_markdown,
            structured_items=validated_items,
            source_references=section_sources_list,
            insufficient_evidence=False,
        )

    @classmethod
    def _render_section_markdown(
        cls,
        section_type: str,
        section_title: str,
        items: List[ValidatedSectionItem],
        suggested_markdown: str,
    ) -> str:
        """Renders rich readable markdown for the section."""
        # If the LLM provided a good markdown summary, check if it covers the items
        if suggested_markdown and len(suggested_markdown) > 100:
            return suggested_markdown

        # Structured markdown fallback builder
        lines: List[str] = []
        if section_type == "definitions":
            for it in items:
                lines.append(f"**{it.title}**: {it.content}")
                if it.additional_info:
                    lines.append(f"  *Note: {it.additional_info}*")
                lines.append("")
        elif section_type == "processes":
            for i, it in enumerate(items, 1):
                lines.append(f"### {i}. {it.title}")
                lines.append(f"{it.content}")
                if it.additional_info:
                    lines.append(f"> **Key Consideration:** {it.additional_info}")
                lines.append("")
        elif section_type == "comparisons":
            for it in items:
                lines.append(f"### {it.title}")
                lines.append(f"{it.content}")
                if it.additional_info:
                    lines.append(f"*Tradeoffs / Differences:* {it.additional_info}")
                lines.append("")
        elif section_type == "practice_questions":
            for i, it in enumerate(items, 1):
                lines.append(f"**Q{i}. {it.title}**")
                lines.append(f"{it.content}")
                if it.additional_info:
                    lines.append(f"*Answer & Explanation:* {it.additional_info}")
                lines.append("")
        else:
            for it in items:
                lines.append(f"### {it.title}")
                lines.append(f"{it.content}")
                if it.additional_info:
                    lines.append(f"> {it.additional_info}")
                lines.append("")

        return "\n".join(lines).strip()
