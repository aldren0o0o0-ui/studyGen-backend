"""
quiz_generation.py — Phase 3
Full quiz generation pipeline:
  1. Allocate question types and difficulties deterministically (round-robin).
  2. Build labeled context (SOURCE_1, SOURCE_2, ...).
  3. Build v3 prompt via prompt_templates.
  4. Call Gemini, parse output.
  5. Validate via question_validation service (source mapping, dedup, etc.).
  6. Retry missing/invalid questions up to max_retries.
  7. Return a GeneratedQuiz dataclass for the router to persist.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from fastapi import HTTPException

from app.schemas.quizzes import GenerateQuizRequest
from app.services.context_selection import SelectedChunk
from app.services.prompt_templates import PROMPT_VERSION, build_quiz_prompt
from app.services.question_validation import (
    SourceReference,
    ValidatedQuestion,
    validate_questions,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Allocation helpers
# ---------------------------------------------------------------------------

def allocate_question_types(
    question_types: List[str],
    total: int,
) -> dict[str, int]:
    """
    Round-robin allocation so each type gets an even share.
    The difference between any two type counts is at most 1.

    e.g. types=[A,B,C], total=8 → {A:3, B:3, C:2}
    """
    n = len(question_types)
    if n == 0:
        return {}
    base, remainder = divmod(total, n)
    return {
        qtype: base + (1 if i < remainder else 0)
        for i, qtype in enumerate(question_types)
    }


def allocate_difficulties(difficulty: str, total: int) -> Optional[dict[str, int]]:
    """
    When difficulty='mixed', allocate easy/medium/hard in round-robin order.
    Returns None for fixed-difficulty requests (easy/medium/hard).

    e.g. total=8 → {easy:3, medium:3, hard:2}
    """
    if difficulty != "mixed":
        return None
    levels = ["easy", "medium", "hard"]
    base, remainder = divmod(total, 3)
    return {
        lvl: base + (1 if i < remainder else 0)
        for i, lvl in enumerate(levels)
    }


# ---------------------------------------------------------------------------
# Return type from this module
# ---------------------------------------------------------------------------

@dataclass
class GeneratedQuiz:
    title: str
    difficulty: str                        # may be "mixed" at quiz level
    document_id: Optional[str] = None
    question_types: List[str] = field(default_factory=list)
    coverage_mode: str = "balanced"
    source_file: str = "Document"
    chunks_used: int = 0
    prompt_version: str = "quiz_generation_v3"
    model_name: str = "gemini-2.5-flash"
    type_allocations: dict[str, int] = field(default_factory=dict)
    difficulty_allocations: Optional[dict[str, int]] = None
    configuration_json: dict = field(default_factory=dict)
    questions: List[ValidatedQuestion] = field(default_factory=list)
    scope_type: str = "DOCUMENT"
    folder_id: Optional[str] = None
    include_subfolders: bool = False
    source_document_ids: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_quiz(
    model,          # google.generativeai.GenerativeModel
    request: GenerateQuizRequest,
    context_chunks: List[Any],
    source_filename: str,
    max_retries: int = 2,
    *,
    coverage_plan: Any = None,
    scope_type: str = "DOCUMENT",
    folder_id: Optional[str] = None,
    include_subfolders: bool = False,
    source_document_ids: Optional[List[str]] = None,
) -> GeneratedQuiz:
    """
    Build prompt, call Gemini, validate, retry for missing questions.

    Raises:
        HTTPException 502: If valid questions < 1 after all retries.
        HTTPException 503: If Gemini API is unavailable.
    """
    if coverage_plan:
        type_allocs = coverage_plan.type_allocations
        diff_allocs = coverage_plan.difficulty_allocations
    else:
        type_allocs = allocate_question_types(request.question_types, request.question_count)
        diff_allocs = allocate_difficulties(request.difficulty, request.question_count)

    # Build labeled context: SOURCE_1, SOURCE_2, ...
    labeled_context = _build_labeled_context(context_chunks)
    source_map_list = list(context_chunks)   # positional; SOURCE_{i+1} = source_map_list[i]

    prompt = build_quiz_prompt(request, labeled_context, type_allocs, diff_allocs)
    model_name = getattr(model, "model_name", "gemini-2.5-flash")

    require_sources = getattr(request, "include_sources", True)

    validated: List[ValidatedQuestion] = []
    attempt = 0

    while attempt <= max_retries and len(validated) < request.question_count:
        if attempt == 0:
            raw_text = _call_gemini(model, prompt)
        else:
            needed = request.question_count - len(validated)
            logger.info("Retry %d: need %d more questions.", attempt, needed)
            retry_prompt = (
                f"The previous response was insufficient. "
                f"Generate exactly {needed} additional questions following the same rules.\n\n"
                + prompt
            )
            raw_text = _call_gemini(model, retry_prompt)

        raw_questions = _parse_raw(raw_text)
        if raw_questions is None:
            attempt += 1
            continue

        batch = validate_questions(raw_questions, source_map_list, require_sources=require_sources)

        # Avoid adding duplicates of already-accepted questions
        existing_texts = {q.question_text.lower() for q in validated}
        for q in batch:
            if q.question_text.lower() not in existing_texts:
                validated.append(q)
                existing_texts.add(q.question_text.lower())

        attempt += 1

    if not validated:
        raise HTTPException(
            status_code=502,
            detail="AI returned no valid questions after retries. Please try again.",
        )

    if len(validated) < request.question_count:
        logger.warning(
            "Wanted %d questions, got %d after %d retries.",
            request.question_count, len(validated), max_retries,
        )

    # Enforce per-type counts by trimming/truncating
    validated = _enforce_type_allocation(validated, type_allocs, diff_allocs)

    configuration = {
        "question_types": request.question_types,
        "coverage_mode": request.coverage_mode,
        "difficulty": request.difficulty,
        "type_allocations": type_allocs,
        "difficulty_allocations": diff_allocs,
        "include_sources": require_sources,
        "include_explanations": getattr(request, "include_explanations", True),
    }

    doc_ids = source_document_ids or ([request.document_id] if request.document_id else [])
    if scope_type == "FOLDER":
        quiz_title = f"{source_filename} Quiz"
    elif scope_type == "SELECTED_DOCUMENTS":
        quiz_title = f"Selected Documents Quiz ({len(doc_ids)} sources)"
    else:
        quiz_title = f"Quiz on {source_filename}"

    return GeneratedQuiz(
        title=quiz_title,
        document_id=request.document_id or (doc_ids[0] if doc_ids else None),
        difficulty=request.difficulty,
        question_types=request.question_types,
        coverage_mode=request.coverage_mode,
        source_file=source_filename,
        chunks_used=len(context_chunks),
        prompt_version=PROMPT_VERSION,
        model_name=model_name,
        type_allocations=type_allocs,
        difficulty_allocations=diff_allocs,
        configuration_json=configuration,
        questions=validated,
        scope_type=scope_type,
        folder_id=folder_id,
        include_subfolders=include_subfolders,
        source_document_ids=doc_ids,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_labeled_context(chunks: List[Any]) -> str:
    """Build context block with [SOURCE_N] labels."""
    parts = []
    for i, chunk in enumerate(chunks):
        label = f"[SOURCE_{i + 1}]"
        doc_label = getattr(chunk, "filename", "Document")
        meta = f"({doc_label}, Chunk {chunk.chunk_index}"
        page = getattr(chunk, "page_number", -1)
        if page is not None and page != -1:
            meta += f", Page {page}"
        meta += ")"
        parts.append(f"{label} {meta}\n{chunk.text}")
    return "\n\n---\n\n".join(parts)


def _call_gemini(model, prompt: str) -> str:
    """Call Gemini and return raw text. Raises HTTPException on API error."""
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as exc:
        logger.error("Gemini API error: %s", exc)
        raise HTTPException(status_code=503, detail=f"AI service unavailable: {exc}")


def _parse_raw(raw_text: str) -> list | None:
    """Strip markdown fences and parse JSON. Returns None on failure."""
    try:
        text = raw_text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
        data = json.loads(text)
        questions = data.get("questions")
        if not isinstance(questions, list):
            logger.warning("Parsed JSON missing 'questions' list.")
            return None
        return questions
    except (json.JSONDecodeError, ValueError, AttributeError) as exc:
        logger.warning("JSON parse failed: %s", exc)
        return None


def _enforce_type_allocation(
    questions: List[ValidatedQuestion],
    type_allocs: dict[str, int],
    diff_allocs: Optional[dict[str, int]],
) -> List[ValidatedQuestion]:
    """
    Select questions so each type gets its allocated count.
    For mixed difficulty, also enforce difficulty allocation within each type.
    Preserves document order (by original position) where possible.
    """
    buckets: dict[str, List[ValidatedQuestion]] = {qtype: [] for qtype in type_allocs}
    for q in questions:
        if q.question_type in buckets:
            buckets[q.question_type].append(q)

    result: List[ValidatedQuestion] = []
    for qtype, count in type_allocs.items():
        pool = buckets[qtype]

        if diff_allocs and qtype != "true_false":
            # For mixed difficulty: pick from each difficulty bucket
            diff_buckets: dict[str, List[ValidatedQuestion]] = {"easy": [], "medium": [], "hard": []}
            for q in pool:
                if q.difficulty in diff_buckets:
                    diff_buckets[q.difficulty].append(q)
            selected: List[ValidatedQuestion] = []
            # Proportionally sub-allocate difficulty within the type
            type_total = count
            type_diff_allocs = {
                d: round(cnt / sum(diff_allocs.values()) * type_total)
                for d, cnt in diff_allocs.items()
            }
            for diff, dcnt in type_diff_allocs.items():
                selected.extend(diff_buckets[diff][:dcnt])
            # Fill up to count if allocation arithmetic left a gap
            remaining = [q for q in pool if q not in selected]
            while len(selected) < count and remaining:
                selected.append(remaining.pop(0))
            result.extend(selected[:count])
        else:
            result.extend(pool[:count])

    return result
