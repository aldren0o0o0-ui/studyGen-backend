"""
context_selection.py — Phase 3
Deterministic, document-scoped chunk selection for quiz generation.

Phase 3 additions:
  - balanced: evenly distributes chunks across beginning/middle/end regions.
  - focused: accepts selected_chunk_indexes OR performs a topic_query
             against the same collection, always filtered to document_id.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING

from app.database.models.auth import LEGACY_OWNER_ID
from app.ownership_context import current_user_id

if TYPE_CHECKING:
    import chromadb

logger = logging.getLogger(__name__)


def _owned_where(document_id: str) -> dict:
    return {
        "$and": [
            {"user_id": {"$eq": current_user_id() or LEGACY_OWNER_ID}},
            {"document_id": {"$eq": document_id}},
        ]
    }


@dataclass
class SelectedChunk:
    text: str
    chunk_index: int
    page_number: int          # -1 when unknown
    document_id: str
    filename: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_context_chunks(
    collection: "chromadb.Collection",
    document_id: str,
    max_chars: int = 12_000,
    coverage_mode: str = "balanced",
    selected_chunk_indexes: Optional[List[int]] = None,
    topic_query: Optional[str] = None,
) -> List[SelectedChunk]:
    """
    Return a deterministically-sampled list of chunks for a given document.

    coverage_mode="balanced":
        Distributes chunks evenly across beginning, middle, and end of the
        document to maximize coverage.

    coverage_mode="focused":
        If selected_chunk_indexes is provided, uses those exact chunks.
        If topic_query is provided, performs semantic retrieval inside document_id.
        Cross-document contamination is impossible (strict document_id filter).

    Raises:
        ValueError: If no chunks exist, or focused mode has no selection criteria.
    """
    if coverage_mode == "focused":
        return _select_focused(
            collection, document_id, max_chars,
            selected_chunk_indexes, topic_query
        )
    # Default: balanced
    return _select_balanced(collection, document_id, max_chars)


# ---------------------------------------------------------------------------
# Balanced selection
# ---------------------------------------------------------------------------

def _select_balanced(
    collection: "chromadb.Collection",
    document_id: str,
    max_chars: int,
) -> List[SelectedChunk]:
    """Fetch all chunks and distribute evenly across document thirds."""
    all_chunks = _fetch_all(collection, document_id)

    total_chars = sum(len(c.text) for c in all_chunks)
    if total_chars <= max_chars:
        logger.info("Document %s: all %d chunks fit budget (%d chars).", document_id, len(all_chunks), total_chars)
        return all_chunks

    # Evenly sample across beginning / middle / end thirds
    selected = _region_sample(all_chunks, max_chars)
    logger.info("Document %s: balanced sample → %d/%d chunks.", document_id, len(selected), len(all_chunks))
    return selected


def _region_sample(chunks: List[SelectedChunk], max_chars: int) -> List[SelectedChunk]:
    """
    Sample chunks so that beginning, middle, and end of the document are
    all represented.  Picks from each third in round-robin order until
    the budget is consumed.
    """
    n = len(chunks)
    if n == 0:
        return []
    if n <= 3:
        return _trim_to_budget(chunks, max_chars)

    third = n // 3
    beginning = chunks[:third]
    middle = chunks[third: 2 * third]
    end = chunks[2 * third:]

    # Interleave regions evenly
    interleaved: List[SelectedChunk] = []
    for triple in zip(beginning, middle, end):
        interleaved.extend(triple)

    # Append any remainder chunks
    remainder = chunks[len(interleaved):]
    interleaved.extend(remainder)

    return _trim_to_budget(interleaved, max_chars)


def _trim_to_budget(chunks: List[SelectedChunk], max_chars: int) -> List[SelectedChunk]:
    result: List[SelectedChunk] = []
    used = 0
    for c in chunks:
        if used + len(c.text) > max_chars:
            break
        result.append(c)
        used += len(c.text)
    return result or [chunks[0]]


# ---------------------------------------------------------------------------
# Focused selection
# ---------------------------------------------------------------------------

def _select_focused(
    collection: "chromadb.Collection",
    document_id: str,
    max_chars: int,
    selected_chunk_indexes: Optional[List[int]],
    topic_query: Optional[str],
) -> List[SelectedChunk]:
    if selected_chunk_indexes:
        return _select_by_indexes(collection, document_id, max_chars, selected_chunk_indexes)
    if topic_query:
        return _select_by_topic(collection, document_id, max_chars, topic_query)
    raise ValueError(
        "focused coverage_mode requires either selected_chunk_indexes or topic_query."
    )


def _select_by_indexes(
    collection: "chromadb.Collection",
    document_id: str,
    max_chars: int,
    indexes: List[int],
) -> List[SelectedChunk]:
    """Return only chunks whose chunk_index is in the requested list."""
    all_chunks = _fetch_all(collection, document_id)
    index_set = set(indexes)
    selected = [c for c in all_chunks if c.chunk_index in index_set]
    if not selected:
        raise ValueError(
            f"No chunks found for document_id='{document_id}' "
            f"with indexes {indexes}. Check that the indexes are valid."
        )
    return _trim_to_budget(selected, max_chars)


def _select_by_topic(
    collection: "chromadb.Collection",
    document_id: str,
    max_chars: int,
    topic_query: str,
) -> List[SelectedChunk]:
    """Semantic retrieval strictly inside the given document_id."""
    n_results = 10  # Fetch top-10 semantically relevant chunks
    try:
        results = collection.query(
            query_texts=[topic_query],
            n_results=n_results,
            where=_owned_where(document_id),
            include=["documents", "metadatas"],
        )
    except Exception as exc:
        logger.warning("Topic query failed — falling back to balanced: %s", exc)
        return _select_balanced(collection, document_id, max_chars)

    docs = (results.get("documents") or [[]])[0]
    metas = (results.get("metadatas") or [[]])[0]

    if not docs:
        raise ValueError(
            f"No chunks found matching topic_query='{topic_query}' "
            f"for document_id='{document_id}'."
        )

    chunks: List[SelectedChunk] = []
    for doc, meta in zip(docs, metas):
        # Double-check document isolation (defence in depth)
        if (
            meta.get("document_id") != document_id
            or meta.get("user_id") != (current_user_id() or LEGACY_OWNER_ID)
        ):
            logger.error("Cross-document contamination blocked — chunk skipped.")
            continue
        chunks.append(SelectedChunk(
            text=doc,
            chunk_index=meta.get("chunk_index", 0),
            page_number=meta.get("page_number", -1),
            document_id=meta.get("document_id", document_id),
            filename=meta.get("filename", "unknown"),
        ))

    chunks.sort(key=lambda c: c.chunk_index)
    return _trim_to_budget(chunks, max_chars)


# ---------------------------------------------------------------------------
# Shared fetch helper
# ---------------------------------------------------------------------------

def _fetch_all(collection: "chromadb.Collection", document_id: str) -> List[SelectedChunk]:
    """Fetch ALL chunks for a document, sorted by chunk_index."""
    results = collection.get(
        where=_owned_where(document_id),
        include=["documents", "metadatas"],
    )

    raw_docs: List[str] = results.get("documents") or []
    raw_metas: List[dict] = results.get("metadatas") or []

    if not raw_docs:
        raise ValueError(f"No chunks found for document_id='{document_id}'.")

    chunks: List[SelectedChunk] = []
    for doc, meta in zip(raw_docs, raw_metas):
        if (
            meta.get("document_id") != document_id
            or meta.get("user_id") != (current_user_id() or LEGACY_OWNER_ID)
        ):
            continue
        chunks.append(SelectedChunk(
            text=doc,
            chunk_index=meta.get("chunk_index", 0),
            page_number=meta.get("page_number", -1),
            document_id=meta.get("document_id", document_id),
            filename=meta.get("filename", "unknown"),
        ))
    chunks.sort(key=lambda c: c.chunk_index)
    # Deduplicate adjacent near-identical chunks
    return _deduplicate(chunks)


def _deduplicate(chunks: List[SelectedChunk], threshold: float = 0.85) -> List[SelectedChunk]:
    if not chunks:
        return chunks
    result = [chunks[0]]
    for chunk in chunks[1:]:
        if _similarity(result[-1].text, chunk.text) < threshold:
            result.append(chunk)
    return result


def _similarity(a: str, b: str) -> float:
    def tg(s: str) -> set[str]:
        s = s.lower()
        return {s[i:i+3] for i in range(len(s) - 2)}
    ta, tb = tg(a), tg(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
