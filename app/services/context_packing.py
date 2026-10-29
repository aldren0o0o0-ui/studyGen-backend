"""
app/services/context_packing.py — Wave 2 Multi-Document Context Packing & Deduplication
Handles deterministic token budgeting, exact/near duplicate pruning, adjacent chunk overlap
reduction, and relevance-aware diversity packing.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, List, Optional, Set, Tuple

from app.services.chat_retrieval import ChatChunk


def normalize_text_for_comparison(text: str) -> str:
    """Normalize whitespace and punctuation for exact/near duplicate checking."""
    normalized = unicodedata.normalize("NFKC", text or "")
    return " ".join(normalized.lower().split())


def estimate_tokens(text: str) -> int:
    """
    Lightweight deterministic token estimation.
    Uses ~4 characters per token ratio with word count adjustment.
    """
    if not text:
        return 0
    words = len(text.split())
    chars = len(text)
    # Average of char-based and word-based estimation
    char_estimate = chars / 4.0
    word_estimate = words * 1.33
    return max(1, int(round((char_estimate + word_estimate) / 2.0)))


def compute_text_similarity(a: str, b: str) -> float:
    """
    Compute Jaccard similarity across 3-character character n-grams and word tokens.
    Fast, deterministic, and language-resilient.
    """
    norm_a = normalize_text_for_comparison(a)
    norm_b = normalize_text_for_comparison(b)
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0

    # 3-char n-grams
    def ngrams(val: str) -> Set[str]:
        return {val[i : i + 3] for i in range(max(0, len(val) - 2))}

    grams_a, grams_b = ngrams(norm_a), ngrams(norm_b)
    gram_sim = len(grams_a & grams_b) / len(grams_a | grams_b) if grams_a and grams_b else 0.0

    # Word tokens
    words_a, words_b = set(norm_a.split()), set(norm_b.split())
    word_sim = len(words_a & words_b) / len(words_a | words_b) if words_a and words_b else 0.0

    # Blend gram (structure) and word (vocabulary) similarity
    return max(gram_sim, word_sim)


def is_near_duplicate(text_a: str, text_b: str, threshold: float = 0.85) -> bool:
    """Check if two chunk texts are exact or near-duplicates."""
    if normalize_text_for_comparison(text_a) == normalize_text_for_comparison(text_b):
        return True
    return compute_text_similarity(text_a, text_b) >= threshold


def is_adjacent_overlap(chunk_a: ChatChunk, chunk_b: ChatChunk, overlap_threshold: float = 0.65) -> bool:
    """
    Check if two chunks from the same document are adjacent and heavily overlap.
    """
    if chunk_a.document_id != chunk_b.document_id:
        return False
    if abs(chunk_a.chunk_index - chunk_b.chunk_index) == 1:
        return compute_text_similarity(chunk_a.text, chunk_b.text) >= overlap_threshold
    return False


def deduplicate_candidates(
    candidates: List[ChatChunk],
    near_dup_threshold: float = 0.85,
    overlap_threshold: float = 0.65,
) -> Tuple[List[ChatChunk], int]:
    """
    Prunes exact duplicates, near-duplicates, and same-document adjacent overlapping chunks.
    Assumes candidates are sorted by relevance descending.
    """
    deduped: List[ChatChunk] = []
    suppressed_count = 0

    for candidate in candidates:
        is_dup = False
        for kept in deduped:
            # 1. Exact or near-duplicate across any document
            if is_near_duplicate(candidate.text, kept.text, threshold=near_dup_threshold):
                is_dup = True
                break
            # 2. Adjacent chunk overlap in the same document
            if is_adjacent_overlap(candidate, kept, overlap_threshold=overlap_threshold):
                is_dup = True
                break
        if is_dup:
            suppressed_count += 1
            continue
        deduped.append(candidate)

    return deduped, suppressed_count


def select_diversity_aware_chunks(
    ranked_chunks: List[ChatChunk],
    top_k: int = 12,
    diversity_margin: float = 0.08,
    max_share_per_doc: float = 0.50,
) -> List[ChatChunk]:
    """
    Relevance-first diversity selection.
    
    Principles:
    1. Highest relevance candidate always leads.
    2. When multiple candidates have comparable relevance (within diversity_margin),
       candidates from underrepresented documents are given priority.
    3. Soft domination guard: a single multi-chunk document cannot consume more than
       `max_share_per_doc` of top_k slots IF other documents have candidates within the diversity margin.
    4. If only one document has relevant material, it is allowed to use all needed slots.
    """
    if not ranked_chunks:
        return []
    if len(ranked_chunks) <= top_k:
        return ranked_chunks

    max_per_doc_slots = max(2, int(round(top_k * max_share_per_doc)))
    selected: List[ChatChunk] = []
    doc_counts: dict[str, int] = {}
    remaining = list(ranked_chunks)

    while remaining and len(selected) < top_k:
        top_relevance = remaining[0].relevance
        # Find candidates within diversity margin of the top candidate
        qualifying_indices = [
            idx for idx, c in enumerate(remaining)
            if (top_relevance - c.relevance) <= diversity_margin
        ]

        # Among qualifying candidates, prefer those from documents with lower selection counts
        best_idx = qualifying_indices[0]
        best_doc_count = doc_counts.get(remaining[best_idx].document_id, 0)

        for q_idx in qualifying_indices:
            candidate = remaining[q_idx]
            c_doc_count = doc_counts.get(candidate.document_id, 0)
            # If candidate's document has fewer slots selected, choose it for diversity
            if c_doc_count < best_doc_count:
                best_idx = q_idx
                best_doc_count = c_doc_count

        chosen = remaining.pop(best_idx)

        # Check soft domination cap: if chosen document has reached cap AND other candidates exist
        if (
            doc_counts.get(chosen.document_id, 0) >= max_per_doc_slots
            and len(doc_counts) > 1
            and any(c.document_id != chosen.document_id for c in remaining)
        ):
            # Find next best candidate from another document within margin
            alt_idx = next(
                (idx for idx, c in enumerate(remaining) if c.document_id != chosen.document_id),
                None,
            )
            if alt_idx is not None and (top_relevance - remaining[alt_idx].relevance) <= (diversity_margin * 1.5):
                remaining.append(chosen)  # re-queue for later
                chosen = remaining.pop(alt_idx)

        selected.append(chosen)
        doc_counts[chosen.document_id] = doc_counts.get(chosen.document_id, 0) + 1

    return selected


def pack_context(
    ranked_chunks: List[ChatChunk],
    *,
    max_tokens: int = 3000,
    max_chars: int = 12000,
    top_k: int = 12,
    diversity_margin: float = 0.08,
    max_share_per_doc: float = 0.50,
    near_dup_threshold: float = 0.85,
) -> Tuple[List[ChatChunk], dict[str, Any]]:
    """
    Canonical context packing function for multi-document RAG.
    
    Returns:
        (packed_chunks, metrics_dict)
    """
    # 1. Prune duplicates & overlapping chunks
    deduped, suppressed_count = deduplicate_candidates(
        ranked_chunks, near_dup_threshold=near_dup_threshold
    )

    # 2. Select with relevance-aware diversity control
    diverse_chunks = select_diversity_aware_chunks(
        deduped,
        top_k=top_k,
        diversity_margin=diversity_margin,
        max_share_per_doc=max_share_per_doc,
    )

    # 3. Budget for tokens and characters
    packed: List[ChatChunk] = []
    used_tokens = 0
    used_chars = 0
    doc_distribution: dict[str, int] = {}

    for chunk in diverse_chunks:
        # Include source header overhead estimate (~20 tokens per chunk)
        chunk_tok = estimate_tokens(chunk.text) + 20
        chunk_len = len(chunk.text)

        if used_tokens + chunk_tok > max_tokens or used_chars + chunk_len > max_chars:
            continue

        packed.append(chunk)
        used_tokens += chunk_tok
        used_chars += chunk_len
        doc_distribution[chunk.document_id] = doc_distribution.get(chunk.document_id, 0) + 1

    metrics = {
        "candidate_count": len(ranked_chunks),
        "deduped_count": len(deduped),
        "suppressed_count": suppressed_count,
        "selected_chunk_count": len(packed),
        "unique_sources": len(doc_distribution),
        "source_distribution": doc_distribution,
        "estimated_tokens": used_tokens,
        "total_characters": used_chars,
    }

    return packed, metrics
