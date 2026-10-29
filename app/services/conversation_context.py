"""Bounded, deterministic conversation context for retrieval and prompting."""
from __future__ import annotations

import re

from app.database.models.chat import ChatMessage

REFERENCE_PATTERN = re.compile(
    r"\b(it|that|this|they|them|the other one|those|these|former|latter)\b", re.I
)


def build_retrieval_query(current_message: str, prior_messages: list[ChatMessage]) -> str:
    """Use prior user wording only to resolve short referential follow-ups."""
    if not REFERENCE_PATTERN.search(current_message):
        return current_message[:1000]
    prior_users = [
        message.content for message in prior_messages
        if message.role == "user" and message.content.strip()
    ]
    if not prior_users:
        return current_message[:1000]
    return f"{prior_users[-1][:500]}\nFollow-up: {current_message}"[:1000]


def recent_history(messages: list[ChatMessage], limit: int) -> list[ChatMessage]:
    completed = [
        message for message in messages
        if message.role in {"user", "assistant"} and message.status == "COMPLETED"
    ]
    return completed[-limit:]


def deterministic_summary(messages: list[ChatMessage], max_chars: int = 1200) -> str:
    """Summarize older dialogue without treating assistant claims as evidence."""
    user_topics: list[str] = []
    for message in messages:
        if message.role != "user":
            continue
        cleaned = " ".join(message.content.split())
        if cleaned and cleaned not in user_topics:
            user_topics.append(cleaned[:240])
    if not user_topics:
        return ""
    summary = "Earlier student questions/topics: " + " | ".join(user_topics[-8:])
    return summary[:max_chars]
