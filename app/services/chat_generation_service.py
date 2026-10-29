"""Structured Gemini generation and output validation for document chat."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.services.chat_citation_service import ValidatedCitation, validate_citation_labels
from app.services.chat_grounding_service import check_answer_support
from app.services.chat_prompts import CHAT_PROMPT_VERSION, build_chat_prompt
from app.services.chat_retrieval import ChatChunk

INSUFFICIENT_ANSWER = (
    "I could not find enough information about that in the selected documents. "
    "Try selecting another document or asking about a topic covered in these materials."
)
logger = logging.getLogger(__name__)


class ChatProviderUnavailable(RuntimeError):
    pass


class ChatInvalidOutput(RuntimeError):
    pass


@dataclass
class GeneratedChatAnswer:
    answer: str
    citations: list[ValidatedCitation]
    insufficient_evidence: bool
    follow_up_suggestions: list[str]
    prompt_version: str
    model_name: str
    input_token_estimate: int
    output_token_estimate: int


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def _suggestions(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        text = " ".join(str(item).split()).strip()
        if 3 <= len(text) <= 140 and text not in result:
            result.append(text)
        if len(result) == 3:
            break
    return result


def _has_hard_grounding_failure(reasons: list[str]) -> bool:
    """Only deterministic claim failures should suppress a cited answer.

    Named-entity matching is advisory because acronyms, OCR, punctuation, and
    hyphenation routinely differ between a PDF excerpt and a generated answer.
    """
    return "unsupported_numeric_claim" in reasons


def generate_extractive_fallback(
    *,
    question: str,
    chunks: list[ChatChunk],
    selected_document_ids: set[str],
) -> GeneratedChatAnswer:
    """Build a useful, citation-backed response without an AI provider."""
    stopwords = {
        "about", "could", "document", "does", "from", "have", "into", "mean",
        "please", "summary", "that", "their", "this", "what", "when", "where",
        "which", "with", "would",
    }
    query_terms = {
        token for token in re.findall(r"[a-z0-9]+", question.lower())
        if len(token) > 2 and token not in stopwords
    }
    candidates: list[tuple[int, float, int, int, str]] = []
    for source_index, chunk in enumerate(chunks, start=1):
        normalized = " ".join(chunk.text.split())
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+|(?=\b(?:TITLE|SECTION)\s+)", normalized)
            if len(sentence.strip()) >= 25
        ]
        if not sentences and normalized:
            sentences = [normalized]
        for sentence_index, sentence in enumerate(sentences):
            terms = set(re.findall(r"[a-z0-9]+", sentence.lower()))
            overlap = len(query_terms & terms)
            candidates.append(
                (overlap, chunk.relevance, -sentence_index, source_index, sentence)
            )

    candidates.sort(reverse=True)
    chosen: list[tuple[int, str]] = []
    seen_text: set[str] = set()
    used_sources: set[int] = set()
    for overlap, _, _, source_index, sentence in candidates:
        key = sentence.lower()
        if key in seen_text:
            continue
        # For broad questions, prefer coverage across chunks. For specific
        # questions, prioritize lexical overlap while still limiting repetition.
        if not query_terms and source_index in used_sources:
            continue
        if query_terms and overlap == 0 and chosen:
            continue
        chosen.append((source_index, sentence[:600]))
        seen_text.add(key)
        used_sources.add(source_index)
        if len(chosen) == 4:
            break

    if not chosen and chunks:
        chosen = [(1, " ".join(chunks[0].text.split())[:600])]
    labels = [f"SOURCE_{source_index}" for source_index, _ in chosen]
    citations = validate_citation_labels(labels, chunks, selected_document_ids)
    if not citations:
        return GeneratedChatAnswer(
            answer=INSUFFICIENT_ANSWER,
            citations=[],
            insufficient_evidence=True,
            follow_up_suggestions=["Summarize this document"],
            prompt_version=CHAT_PROMPT_VERSION,
            model_name="server-extractive-fallback",
            input_token_estimate=0,
            output_token_estimate=max(1, len(INSUFFICIENT_ANSWER) // 4),
        )

    bullet_text = "\n".join(f"• {sentence}" for _, sentence in chosen)
    answer = f"Based directly on the selected document:\n\n{bullet_text}"
    return GeneratedChatAnswer(
        answer=answer,
        citations=citations,
        insufficient_evidence=False,
        follow_up_suggestions=[
            "Summarize this document",
            "What are the key points in this document?",
        ],
        prompt_version=CHAT_PROMPT_VERSION,
        model_name="server-extractive-fallback",
        input_token_estimate=0,
        output_token_estimate=max(1, len(answer) // 4),
    )


def generate_chat_answer(
    model: Any,
    *,
    model_available: bool,
    unavailable_reason: str,
    model_name: str,
    question: str,
    mode: str,
    chunks: list[ChatChunk],
    selected_document_ids: set[str],
    recent_messages: list[tuple[str, str]],
    conversation_summary: str,
    max_retries: int,
) -> GeneratedChatAnswer:
    if not chunks:
        return GeneratedChatAnswer(
            answer=INSUFFICIENT_ANSWER,
            citations=[],
            insufficient_evidence=True,
            follow_up_suggestions=["Summarize this document"],
            prompt_version=CHAT_PROMPT_VERSION,
            model_name=model_name,
            input_token_estimate=0,
            output_token_estimate=max(1, len(INSUFFICIENT_ANSWER) // 4),
        )
    if not model_available:
        raise ChatProviderUnavailable(unavailable_reason or "AI provider is unavailable.")

    prompt = build_chat_prompt(
        question, mode, chunks, recent_messages, conversation_summary
    )
    last_error: Exception | None = None
    raw: dict[str, Any] | None = None
    for _ in range(max_retries + 1):
        try:
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"},
                request_options={"timeout": 45},
            )
            raw = _parse_json(response.text)
            if not isinstance(raw.get("answer"), str) or not raw["answer"].strip():
                raise ValueError("Missing answer.")
            break
        except Exception as exc:
            logger.warning(
                "Chat JSON generation attempt failed (%s): %s",
                type(exc).__name__,
                exc,
            )
            last_error = exc
            raw = None
    if raw is None:
        raise ChatInvalidOutput("The AI provider returned an invalid response.") from last_error

    answer = " ".join(raw["answer"].split()).strip()
    insufficient = bool(raw.get("insufficient_evidence", False))
    raw_labels = raw.get("source_labels") if isinstance(raw.get("source_labels"), list) else []

    # If the provider provided an answer from evidence but omitted source_labels, fallback to available sources
    if not raw_labels and chunks and not insufficient:
        raw_labels = [f"SOURCE_{i}" for i in range(1, min(len(chunks) + 1, 3))]

    citations = validate_citation_labels(raw_labels, chunks, selected_document_ids)

    # Citation validity remains mandatory, ensuring answer is grounded in scope
    if not insufficient:
        _, support_reasons = check_answer_support(answer, citations, chunks)
        if not citations or _has_hard_grounding_failure(support_reasons):
            answer = INSUFFICIENT_ANSWER
            citations = []
            insufficient = True
    if insufficient:
        citations = []

    return GeneratedChatAnswer(
        answer=answer,
        citations=citations,
        insufficient_evidence=insufficient,
        follow_up_suggestions=_suggestions(raw.get("follow_up_suggestions")),
        prompt_version=CHAT_PROMPT_VERSION,
        model_name=model_name,
        input_token_estimate=max(1, len(prompt) // 4),
        output_token_estimate=max(1, len(answer) // 4),
    )
