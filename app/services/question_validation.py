"""
app/services/question_validation.py
Server-side validation for AI-generated quiz questions.

Responsibilities:
  - Reject or repair malformed questions.
  - Deduplicate (exact and near-duplicate) question text.
  - Validate source labels against the known context set.
  - Validate question-type-specific structure rules.
  - Generate server-side source excerpts (never trust AI-generated excerpts).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.services.context_selection import SelectedChunk

logger = logging.getLogger(__name__)

VALID_QUESTION_TYPES = {"multiple_choice", "true_false", "identification"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}
EXCERPT_MAX_CHARS = 200


# ---------------------------------------------------------------------------
# Source reference (resolved, server-side)
# ---------------------------------------------------------------------------

@dataclass
class SourceReference:
    chunk_index: int
    page_number: int        # -1 when unknown
    filename: str
    excerpt: str            # always server-generated, never trusted from AI
    document_id: str | None = None


# ---------------------------------------------------------------------------
# Internal validated question representation
# ---------------------------------------------------------------------------

@dataclass
class ValidatedQuestion:
    question_type: str
    question_text: str
    options: list[str]
    correct_answer: str
    accepted_answers: list[str]
    explanation: str | None
    difficulty: str
    topic: str
    sources: list[SourceReference] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate_questions(
    raw_questions: list[dict[str, Any]],
    context_chunks: list[SelectedChunk],
    require_sources: bool = True,
) -> list[ValidatedQuestion]:
    """
    Validate and repair a list of raw AI question dicts.

    - Builds a SOURCE_N -> SelectedChunk map from the context.
    - Iterates questions, running all validation rules.
    - Returns only valid questions (rejects and logs the rest).
    """
    source_map = _build_source_map(context_chunks)
    seen_normalized: set[str] = set()
    results: list[ValidatedQuestion] = []

    for i, raw in enumerate(raw_questions):
        try:
            vq = _validate_one(raw, source_map, seen_normalized, require_sources, i)
            if vq is not None:
                seen_normalized.add(_normalize(vq.question_text))
                results.append(vq)
        except Exception as exc:
            logger.warning("Unexpected error validating question %d: %s", i, exc)

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_source_map(chunks: list[SelectedChunk]) -> dict[str, SelectedChunk]:
    """Build a dict: 'SOURCE_1' -> SelectedChunk, 'SOURCE_2' -> ..."""
    return {f"SOURCE_{i + 1}": chunk for i, chunk in enumerate(chunks)}


def _validate_one(
    raw: dict[str, Any],
    source_map: dict[str, SelectedChunk],
    seen_normalized: set[str],
    require_sources: bool,
    idx: int,
) -> ValidatedQuestion | None:
    """Validate a single raw question dict. Returns None to signal rejection."""

    # --- question_type ---
    qtype = (raw.get("question_type") or "multiple_choice").strip().lower()
    if qtype not in VALID_QUESTION_TYPES:
        logger.warning("Q[%d]: unsupported question_type '%s' — rejected.", idx, qtype)
        return None

    # --- question text ---
    q_text = (raw.get("question") or "").strip()
    if not q_text:
        logger.warning("Q[%d]: empty question text — rejected.", idx)
        return None

    normalized_text = _normalize(q_text)
    if normalized_text in seen_normalized:
        logger.warning("Q[%d]: duplicate question text — rejected.", idx)
        return None

    # Near-duplicate check against already-accepted questions
    for seen in seen_normalized:
        if _trigram_similarity(normalized_text, seen) > 0.85:
            logger.warning("Q[%d]: near-duplicate question — rejected.", idx)
            return None

    # --- difficulty ---
    difficulty = (raw.get("difficulty") or "medium").strip().lower()
    if difficulty not in VALID_DIFFICULTIES:
        logger.warning("Q[%d]: invalid difficulty '%s' — defaulting to medium.", idx, difficulty)
        difficulty = "medium"

    # --- topic ---
    topic = (raw.get("topic") or "General").strip()

    # --- type-specific validation ---
    options = [str(o).strip() for o in (raw.get("options") or [])]
    correct = (raw.get("correct_answer") or "").strip()
    accepted_raw = raw.get("accepted_answers") or []
    accepted = [str(a).strip().lower() for a in accepted_raw if str(a).strip()]

    if qtype == "multiple_choice":
        if len(options) < 2:
            logger.warning("Q[%d]: multiple_choice has fewer than 2 options — rejected.", idx)
            return None
        if len(set(o.lower() for o in options)) < len(options):
            logger.warning("Q[%d]: repeated options — rejected.", idx)
            return None
        if correct not in options:
            logger.warning("Q[%d]: correct_answer not in options — rejected.", idx)
            return None

    elif qtype == "true_false":
        if sorted([o.lower() for o in options]) != ["false", "true"]:
            logger.warning("Q[%d]: true_false options must be exactly ['True', 'False'] — fixing.", idx)
            options = ["True", "False"]
        correct_title = correct.title()
        if correct_title not in ("True", "False"):
            logger.warning("Q[%d]: true_false correct_answer must be True or False — rejected.", idx)
            return None
        correct = correct_title

    elif qtype == "identification":
        if not correct:
            logger.warning("Q[%d]: identification missing correct_answer — rejected.", idx)
            return None
        if not accepted:
            # Auto-add the primary answer as the sole accepted variant
            accepted = [correct.lower()]
        options = []  # identification has no options

    # --- source labels ---
    raw_labels: list[str] = [str(lbl).strip().upper() for lbl in (raw.get("source_labels") or [])]
    sources: list[SourceReference] = []

    for label in raw_labels:
        chunk = source_map.get(label)
        if chunk is None:
            logger.warning("Q[%d]: invented source label '%s' — ignored.", idx, label)
            continue
        excerpt = _make_excerpt(chunk.text)
        sources.append(SourceReference(
            chunk_index=chunk.chunk_index,
            page_number=chunk.page_number,
            filename=chunk.filename,
            excerpt=excerpt,
            document_id=getattr(chunk, "document_id", None),
        ))

    if require_sources and not sources:
        logger.warning("Q[%d]: no valid source labels — rejected.", idx)
        return None

    explanation = (raw.get("explanation") or None)
    if explanation:
        explanation = str(explanation).strip() or None

    return ValidatedQuestion(
        question_type=qtype,
        question_text=q_text,
        options=options,
        correct_answer=correct,
        accepted_answers=accepted,
        explanation=explanation,
        difficulty=difficulty,
        topic=topic,
        sources=sources,
    )


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, normalize unicode."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _trigram_similarity(a: str, b: str) -> float:
    """Fast trigram Jaccard similarity for near-duplicate detection."""
    def tg(s: str) -> set[str]:
        return {s[i:i+3] for i in range(len(s) - 2)}
    ta, tb = tg(a), tg(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _make_excerpt(chunk_text: str, max_chars: int = EXCERPT_MAX_CHARS) -> str:
    """Return a short excerpt from the chunk — always server-generated."""
    text = chunk_text.strip()
    if len(text) <= max_chars:
        return text
    # Cut at last word boundary before the limit
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars // 2:
        truncated = truncated[:last_space]
    return truncated + "…"


# ---------------------------------------------------------------------------
# Identification answer normalization (used during scoring)
# ---------------------------------------------------------------------------

def normalize_identification_answer(text: str) -> str:
    """
    Normalize a student's identification answer for comparison:
    - Trim whitespace
    - Lowercase
    - Collapse repeated spaces
    - Normalize unicode
    - Strip basic punctuation
    """
    text = unicodedata.normalize("NFKC", text).lower().strip()
    text = re.sub(r"[.,'\"!?;:]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
