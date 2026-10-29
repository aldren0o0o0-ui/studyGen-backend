"""Conservative deterministic support checks, separate from citation validity."""
from __future__ import annotations

import re
import unicodedata

from app.services.chat_citation_service import ValidatedCitation
from app.services.chat_retrieval import ChatChunk


def _normalized_text(value: str) -> str:
    """Normalize punctuation variants before checking source-backed names."""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"[\u2010-\u2015\u2212]", "-", normalized)
    return " ".join(normalized.lower().split())


def check_answer_support(
    answer: str,
    citations: list[ValidatedCitation],
    chunks: list[ChatChunk],
) -> tuple[bool, list[str]]:
    if not citations:
        return False, ["no_valid_citation"]
    cited_keys = {(item.document_id, item.chunk_index) for item in citations}
    evidence = _normalized_text(" ".join(
        chunk.text for chunk in chunks
        if (chunk.document_id, chunk.chunk_index) in cited_keys
    ))
    reasons: list[str] = []

    # Strip out structural enumeration, markdown list numbers, source tags, and parentheticals
    cleaned_answer = re.sub(r"(?m)^\s*(?:[-*•]|\d+[\.\)])\s*", " ", answer)
    cleaned_answer = re.sub(r"\[SOURCE_\d+\]", " ", cleaned_answer)
    cleaned_answer = re.sub(
        r"\b(?:step|point|item|part|takeaway|takeaways|reasons?|factors?|methods?|ways?|questions?|section|page)\s+\d+\b",
        " ",
        cleaned_answer,
        flags=re.I,
    )
    cleaned_answer = re.sub(r"\(\d+\)", " ", cleaned_answer)

    # Extract substantive numeric claims (exclude single-digit structural counters 1-9)
    answer_numbers = {
        num for num in re.findall(r"\b\d+(?:\.\d+)?%?\b", cleaned_answer)
        if len(num.rstrip("%")) > 1 or "%" in num or "." in num
    }
    evidence_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", evidence))
    if answer_numbers - evidence_numbers:
        reasons.append("unsupported_numeric_claim")

    # Keep hyphenated names intact (for example, Sarbanes-Oxley Act).
    name = r"[A-Z][a-z]+(?:[-\u2010-\u2015\u2212][A-Z][a-z]+)*"
    entities = set(re.findall(rf"\b{name}(?:\s+{name})+\b", answer))
    unsupported = [
        entity for entity in entities
        if _normalized_text(entity) not in evidence
    ]
    if unsupported:
        reasons.append("unsupported_named_entity")
    return not reasons, reasons
