"""Deterministic handling for direct boundary/system-prompt attacks."""
from __future__ import annotations

import re

_BOUNDARY_REQUESTS = (
    r"\b(reveal|show|print|repeat)\b.{0,30}\b(system|hidden|developer)\s+prompt\b",
    r"\b(search|query|access|read)\b.{0,30}\b(all|every|unselected)\s+documents?\b",
    r"\b(use|answer from)\b.{0,20}\b(outside|general|internet|web)\s+(knowledge|sources?)\b",
)


def is_boundary_attack(message: str) -> bool:
    return any(re.search(pattern, message, re.I | re.S) for pattern in _BOUNDARY_REQUESTS)


BOUNDARY_RESPONSE = (
    "I can only answer from the documents selected for this chat, and I cannot "
    "provide hidden instructions or search other materials. Ask about a topic "
    "covered in the selected documents."
)
