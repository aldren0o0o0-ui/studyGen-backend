"""app/services/flashcard_validation_service.py — Phase 5 / Wave 4
Deterministic validation and deduplication for flashcards.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Sequence, Set

logger = logging.getLogger(__name__)

SUPPORTED_CARD_TYPES = {"basic", "definition", "concept", "question_answer"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}


def _normalize_text(text: str) -> str:
    """Normalize whitespace and lower-case for comparison."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _tokenize(text: str) -> set[str]:
    """Tokenize text into alphanumeric word set for similarity comparisons."""
    return set(re.findall(r"\w+", text.lower()))


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Calculate token Jaccard similarity."""
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def validate_flashcard_dict(
    card: dict[str, Any],
    source_map: Optional[Dict[str, Any]] = None,
    resolved_doc_ids: Optional[Set[str]] = None,
) -> tuple[bool, str]:
    """
    Validate a raw card dictionary from AI or user input.
    Returns (is_valid, error_reason).
    """
    front = card.get("front") or card.get("front_text") or ""
    back = card.get("back") or card.get("back_text") or ""

    if not isinstance(front, str) or not front.strip():
        return False, "front_text is empty or missing"
    if not isinstance(back, str) or not back.strip():
        return False, "back_text is empty or missing"

    front_norm = _normalize_text(front)
    back_norm = _normalize_text(back)

    if len(front.strip()) > 2000:
        return False, "front_text exceeds 2000 character limit"
    if len(back.strip()) > 4000:
        return False, "back_text exceeds 4000 character limit"

    if front_norm == back_norm:
        return False, "front_text and back_text are identical"

    # Check if back answer is trivially revealed in front question (for answers > 4 chars)
    if len(back_norm) > 4 and back_norm in front_norm:
        return False, "front_text reveals the answer directly"

    card_type = card.get("card_type", "concept")
    if card_type not in SUPPORTED_CARD_TYPES:
        return False, f"unsupported card_type '{card_type}'"

    difficulty = card.get("difficulty", "medium")
    if difficulty not in VALID_DIFFICULTIES:
        return False, f"unsupported difficulty '{difficulty}'"

    # Validate source provenance if source_map is supplied
    if source_map is not None:
        source_labels = card.get("source_labels", [])
        if not isinstance(source_labels, list) or not source_labels:
            return False, "card has no source_labels"

        valid_sources = [lbl for lbl in source_labels if isinstance(lbl, str) and lbl in source_map]
        if not valid_sources:
            return False, f"card contains only invalid or hallucinated source labels: {source_labels}"

        if resolved_doc_ids is not None:
            # Verify that referenced source documents are in the resolved scope
            doc_in_scope = False
            for lbl in valid_sources:
                ref = source_map[lbl]
                doc_id = getattr(ref, "document_id", None) or (ref.get("document_id") if isinstance(ref, dict) else None)
                if doc_id and doc_id in resolved_doc_ids:
                    doc_in_scope = True
                    break
                elif doc_id is None:
                    # Single doc or no doc_id tracked on ref
                    doc_in_scope = True
                    break
            if not doc_in_scope:
                return False, "referenced source document is outside the resolved AI scope"

    return True, ""


def filter_and_deduplicate_cards(
    raw_cards: list[dict[str, Any]],
    existing_fronts: set[str] | None = None,
    source_map: Optional[Dict[str, Any]] = None,
    resolved_doc_ids: Optional[Set[str]] = None,
    near_dup_threshold: float = 0.75,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    Validate and deduplicate a list of raw cards.

    Args:
        raw_cards: list of raw card dicts
        existing_fronts: set of normalized front texts already in the target study set
        source_map: optional mapping from SOURCE_N to SourceReference
        resolved_doc_ids: optional set of authorized document IDs
        near_dup_threshold: Jaccard similarity threshold for near duplicates

    Returns:
        (valid_unique_cards, invalid_count, duplicate_count)
    """
    if existing_fronts is None:
        existing_fronts = set()

    seen_fronts = set(existing_fronts)
    seen_token_sets: list[set[str]] = [_tokenize(f) for f in existing_fronts]
    valid_cards: list[dict[str, Any]] = []
    invalid_count = 0
    duplicate_count = 0

    for raw_card in raw_cards:
        is_valid, reason = validate_flashcard_dict(
            raw_card, source_map=source_map, resolved_doc_ids=resolved_doc_ids
        )
        if not is_valid:
            logger.warning("Rejecting invalid flashcard: %s", reason)
            invalid_count += 1
            continue

        front = raw_card.get("front") or raw_card.get("front_text") or ""
        front_norm = _normalize_text(front)

        # Exact duplicate
        if front_norm in seen_fronts:
            logger.info("Rejecting exact duplicate flashcard front: '%s'", front[:50])
            duplicate_count += 1
            continue

        # Near duplicate (token overlap >= 0.85)
        card_tokens = _tokenize(front)
        if len(card_tokens) >= 3:
            is_near_dup = False
            for existing_tokens in seen_token_sets:
                sim = _jaccard_similarity(card_tokens, existing_tokens)
                if sim >= near_dup_threshold:
                    logger.info(
                        "Rejecting near-duplicate flashcard (similarity=%.2f): '%s'",
                        sim,
                        front[:50],
                    )
                    is_near_dup = True
                    break
            if is_near_dup:
                duplicate_count += 1
                continue

        seen_fronts.add(front_norm)
        seen_token_sets.append(card_tokens)
        valid_cards.append(raw_card)

    return valid_cards, invalid_count, duplicate_count
