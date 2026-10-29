"""Conservative process-local guard; Redis coordination can replace it later."""
from collections import defaultdict, deque
import os
from threading import Lock
from time import monotonic

from fastapi import HTTPException
from config import settings

_events = defaultdict(deque)
_lock = Lock()


def enforce(user_id: str, action: str, limit: int, window_seconds: int = 60) -> None:
    if settings.environment.lower() == "test" or os.getenv("TESTING", "").lower() in {"1", "true", "yes"}:
        return
    now = monotonic(); key = (user_id, action)
    with _lock:
        events = _events[key]
        while events and events[0] <= now - window_seconds: events.popleft()
        if len(events) >= limit:
            raise HTTPException(429, {"code": "RATE_LIMITED", "message": "Please wait before trying again."})
        events.append(now)
