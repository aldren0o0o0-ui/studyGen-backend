"""
app/services/retrieval_service.py — Wave 2 Canonical Multi-Document Retrieval Engine
Provides reusable, deterministic candidate retrieval, global cross-document reranking,
deduplication, diversity control, and context packing across all AI scopes.
"""
from __future__ import annotations

import logging
import re
from typing import Any, List, Optional, Set, Tuple

from app.database.models.auth import LEGACY_OWNER_ID
from app.ownership_context import current_user_id
from app.services.chat_retrieval import ChatChunk, _is_overview_query, _metadata_chunk, _overview_chunks, _owned_where
from app.services.context_packing import pack_context
from config import settings as default_settings

logger = logging.getLogger(__name__)


def compute_keyword_bonus(query: str, text: str) -> float:
    """Lightweight lexical boost for exact keyword matches."""
    query_terms = {
        w.lower() for w in re.findall(r"\b[a-zA-Z0-9_]{3,}\b", query)
        if w.lower() not in {"what", "when", "where", "which", "about", "this", "that", "from", "with", "have"}
    }
    if not query_terms:
        return 0.0
    text_lower = text.lower()
    matches = sum(1 for term in query_terms if term in text_lower)
    # Up to +0.06 boost based on proportion of query terms matched
    return min(0.06, 0.06 * (matches / len(query_terms)))


def retrieve_candidates_for_document(
    collection: Any,
    query: str,
    document_id: str,
    owner_id: str,
    candidate_limit: int,
    relevance_threshold: float,
) -> List[ChatChunk]:
    """Retrieve top candidates for a single document."""
    kwargs = {
        "query_texts": [query],
        "n_results": candidate_limit,
        "where": _owned_where(owner_id, document_id),
    }
    try:
        raw = collection.query(**kwargs, include=["documents", "metadatas", "distances"])
    except TypeError:
        raw = collection.query(**kwargs)

    docs = (raw.get("documents") or [[]])[0]
    metas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]

    candidates: List[ChatChunk] = []
    for index, (text, metadata) in enumerate(zip(docs, metas)):
        # Defence in depth: filter out any chunk from another document or tenant
        if (
            str(metadata.get("document_id")) != document_id
            or str(metadata.get("user_id")) != owner_id
        ):
            continue

        dist = float(distances[index]) if index < len(distances) else 0.0
        # Chroma cosine distance ranges 0..2 (1 - cosine_similarity).
        relevance = max(0.0, min(1.0, 1.0 - (dist / 2.0)))
        if relevance < relevance_threshold:
            continue

        candidates.append(_metadata_chunk(text, metadata, document_id, relevance))

    candidates.sort(key=lambda item: (-item.relevance, item.chunk_index))
    return candidates


def retrieve_context(
    collection: Any,
    query: str,
    document_ids: List[str],
    *,
    user_id: Optional[str] = None,
    purpose: str = "chat",
    settings: Any = None,
) -> Tuple[List[ChatChunk], dict[str, Any]]:
    """
    Main reusable retrieval entrypoint.
    
    Phases:
    1. Tenant scoping & Overview detection
    2. Per-document candidate retrieval (bounded by RAG_CANDIDATES_PER_DOCUMENT)
    3. Global cross-document sorting & deterministic tie-breaking
    4. Deduplication & Context packing (pack_context)
    """
    if not document_ids:
        return [], {"candidate_count": 0, "selected_chunk_count": 0, "unique_sources": 0}

    cfg = settings or default_settings
    owner_id = user_id or current_user_id() or LEGACY_OWNER_ID

    top_k = getattr(cfg, "chat_retrieval_top_k", 12)
    relevance_threshold = getattr(cfg, "chat_relevance_threshold", 0.10)
    candidates_per_doc = getattr(cfg, "rag_candidates_per_document", 4)
    max_candidates_total = getattr(cfg, "rag_max_candidates", 48)
    token_budget = getattr(cfg, "rag_context_token_budget", 3000)
    char_budget = getattr(cfg, "chat_max_context_characters", 12000)
    diversity_margin = getattr(cfg, "rag_diversity_margin", 0.08)
    max_share_per_doc = getattr(cfg, "rag_max_share_per_document", 0.50)
    near_dup_thresh = getattr(cfg, "rag_near_duplicate_threshold", 0.85)

    is_overview = _is_overview_query(query)
    all_candidates: List[ChatChunk] = []

    # 1. Candidate Retrieval
    for document_id in document_ids:
        if is_overview:
            doc_candidates = _overview_chunks(collection, owner_id, document_id, candidates_per_doc)
        else:
            doc_candidates = retrieve_candidates_for_document(
                collection,
                query,
                document_id,
                owner_id,
                candidates_per_doc,
                relevance_threshold,
            )

        all_candidates.extend(doc_candidates)
        if len(all_candidates) >= max_candidates_total:
            break

    # If specific query produced zero candidates across all documents, sample overview chunks as fallback
    if not all_candidates:
        for document_id in document_ids:
            doc_candidates = _overview_chunks(collection, owner_id, document_id, max(1, candidates_per_doc // 2))
            all_candidates.extend(doc_candidates)
            if len(all_candidates) >= max_candidates_total:
                break

    # 2. Global Deterministic Cross-Document Reranking
    # Sort primarily by relevance score, secondary by document_id and chunk_index for stable tie-breaking
    all_candidates.sort(key=lambda item: (-item.relevance, item.document_id, item.chunk_index))

    # 3. Canonical Deduplication, Diversity Control, and Context Packing
    packed_chunks, metrics = pack_context(
        all_candidates,
        max_tokens=token_budget,
        max_chars=char_budget,
        top_k=top_k,
        diversity_margin=diversity_margin,
        max_share_per_doc=max_share_per_doc,
        near_dup_threshold=near_dup_thresh,
    )

    logger.debug(
        "RAG Retrieval: scope_docs=%d, candidates=%d, deduped=%d, selected=%d, sources=%d, est_tokens=%d",
        len(document_ids),
        metrics.get("candidate_count", 0),
        metrics.get("deduped_count", 0),
        metrics.get("selected_chunk_count", 0),
        metrics.get("unique_sources", 0),
        metrics.get("estimated_tokens", 0),
    )

    return packed_chunks, metrics
