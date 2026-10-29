"""Balanced, strictly document-scoped retrieval for Phase 6 chat."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.database.models.auth import LEGACY_OWNER_ID
from app.ownership_context import current_user_id


@dataclass(frozen=True)
class ChatChunk:
    text: str
    document_id: str
    filename: str
    chunk_index: int
    page_number: int | None
    section_label: str | None
    relevance: float


_OVERVIEW_PATTERNS = (
    r"\bwhat (?:is|was) (?:this|the) document (?:about|all about)\b",
    r"\b(?:summari[sz]e|overview of) (?:this|the) (?:document|material|file)\b",
    r"\b(?:main idea|main points|key points|overall topic)\b",
)


def _is_overview_query(query: str) -> bool:
    return any(re.search(pattern, query, re.I) for pattern in _OVERVIEW_PATTERNS)


def _metadata_chunk(text: str, metadata: dict[str, Any], document_id: str, relevance: float) -> ChatChunk:
    page = metadata.get("page_number")
    return ChatChunk(
        text=str(text),
        document_id=document_id,
        filename=str(metadata.get("filename") or "Document"),
        chunk_index=int(metadata.get("chunk_index", 0)),
        page_number=int(page) if isinstance(page, (int, float)) and int(page) >= 0 else None,
        section_label=(
            str(metadata.get("section_label") or metadata.get("section"))
            if metadata.get("section_label") or metadata.get("section")
            else None
        ),
        relevance=relevance,
    )


def _owned_where(user_id: str, document_id: str) -> dict[str, Any]:
    return {
        "$and": [
            {"user_id": {"$eq": user_id or LEGACY_OWNER_ID}},
            {"document_id": {"$eq": document_id}},
        ]
    }


def _overview_chunks(
    collection: Any, user_id: str, document_id: str, limit: int
) -> list[ChatChunk]:
    """Evenly sample the selected document for whole-document questions."""
    raw = collection.get(
        where=_owned_where(user_id, document_id),
        include=["documents", "metadatas"],
    )
    chunks = [
        _metadata_chunk(text, metadata, document_id, 1.0)
        for text, metadata in zip(raw.get("documents") or [], raw.get("metadatas") or [])
        if (
            str(metadata.get("document_id")) == document_id
            and str(metadata.get("user_id")) == user_id
        )
    ]
    chunks.sort(key=lambda item: item.chunk_index)
    if len(chunks) <= limit:
        return chunks
    if limit <= 1:
        return chunks[:1]
    indexes = {
        round(position * (len(chunks) - 1) / (limit - 1))
        for position in range(limit)
    }
    return [chunks[index] for index in sorted(indexes)]


def _similarity(a: str, b: str) -> float:
    def grams(value: str) -> set[str]:
        normalized = " ".join(value.lower().split())
        return {normalized[i:i + 3] for i in range(max(0, len(normalized) - 2))}
    left, right = grams(a), grams(b)
    return len(left & right) / len(left | right) if left and right else 0.0


def retrieve_chat_chunks(
    collection: Any,
    query: str,
    document_ids: list[str],
    *,
    top_k: int,
    relevance_threshold: float,
    max_chunks_per_document: int,
    max_context_characters: int,
    user_id: str | None = None,
) -> list[ChatChunk]:
    """Query selected documents using Wave 2 canonical multi-document retrieval."""
    from app.services.retrieval_service import retrieve_context
    from config import settings

    packed_chunks, _ = retrieve_context(
        collection,
        query,
        document_ids,
        user_id=user_id,
        purpose="chat",
        settings=settings,
    )
    return packed_chunks
