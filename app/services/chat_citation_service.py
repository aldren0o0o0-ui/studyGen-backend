"""Trusted citation-label resolution and server excerpt creation."""
from __future__ import annotations

from dataclasses import dataclass

from app.services.chat_retrieval import ChatChunk

EXCERPT_MAX_CHARS = 240


@dataclass(frozen=True)
class ValidatedCitation:
    document_id: str
    filename: str
    chunk_index: int
    page_number: int | None
    section_label: str | None
    excerpt: str
    source_order: int


def make_excerpt(text: str, limit: int = EXCERPT_MAX_CHARS) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    shortened = normalized[:limit]
    boundary = shortened.rfind(" ")
    return shortened[:boundary if boundary > limit // 2 else limit].rstrip() + "…"


def validate_citation_labels(
    labels: list[str],
    chunks: list[ChatChunk],
    selected_document_ids: set[str],
) -> list[ValidatedCitation]:
    source_map = {f"SOURCE_{index}": chunk for index, chunk in enumerate(chunks, start=1)}
    result: list[ValidatedCitation] = []
    seen: set[tuple[str, int]] = set()
    for label in labels:
        chunk = source_map.get(str(label).strip().upper())
        if chunk is None or chunk.document_id not in selected_document_ids:
            continue
        key = (chunk.document_id, chunk.chunk_index)
        if key in seen:
            continue
        seen.add(key)
        result.append(ValidatedCitation(
            document_id=chunk.document_id,
            filename=chunk.filename,
            chunk_index=chunk.chunk_index,
            page_number=chunk.page_number,
            section_label=chunk.section_label,
            excerpt=make_excerpt(chunk.text),
            source_order=len(result) + 1,
        ))
    return result
