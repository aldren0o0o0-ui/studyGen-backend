"""
services/spaced_repetition_service.py — Phase 4

Deterministic spaced repetition schedule calculator.

Schedule (from spec):
  interval_days=0  (DUE):  first incorrect or reset after wrong review
  1st correct review:       next = +1 day  (interval=1)
  2nd consecutive correct:  next = +3 days (interval=3)
  3rd consecutive correct:  next = +7 days (interval=7)
  4th+ consecutive correct: next = min(prev_interval * 2, 30 days)
  Incorrect during review:  interval=0, next=today, consecutive_correct=0

All dates stored as naive UTC datetimes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Interval table for the first few correct streaks (index = consecutive_correct AFTER this answer)
_INTERVAL_TABLE: dict[int, int] = {
    1: 1,   # 1st correct → review again in 1 day
    2: 3,   # 2nd consecutive correct → 3 days
    3: 7,   # 3rd consecutive correct → 7 days
}
_MAX_INTERVAL = 30  # days
_MASTERY_THRESHOLD = 4  # consecutive correct reviews needed for MASTERED


def calculate_next_review(
    *,
    consecutive_correct: int,
    current_interval_days: int,
    answered_correctly: bool,
    now: datetime | None = None,
) -> tuple[datetime, int, int, str]:
    """
    Calculate spaced-repetition state after one review answer.

    Args:
        consecutive_correct: current streak before this answer
        current_interval_days: current interval before this answer
        answered_correctly: whether this review answer was correct
        now: override for testing (defaults to utcnow)

    Returns:
        (next_review_at, new_interval_days, new_consecutive_correct, new_status)

    Status values: DUE | UPCOMING | MASTERED
    """
    if now is None:
        now = _utcnow()

    if not answered_correctly:
        # Reset: due immediately
        return now, 0, 0, "DUE"

    new_consecutive = consecutive_correct + 1

    if new_consecutive in _INTERVAL_TABLE:
        new_interval = _INTERVAL_TABLE[new_consecutive]
    else:
        # 4th+ correct: double previous interval, capped at MAX
        new_interval = min(current_interval_days * 2 if current_interval_days > 0 else 7, _MAX_INTERVAL)

    next_dt = now + timedelta(days=new_interval)
    status = "MASTERED" if new_consecutive >= _MASTERY_THRESHOLD else "UPCOMING"
    return next_dt, new_interval, new_consecutive, status


def calculate_initial_schedule(now: datetime | None = None) -> tuple[datetime, int]:
    """
    Return (next_review_at, interval_days) for a brand-new incorrect answer.
    First incorrect answer is always DUE today.
    """
    if now is None:
        now = _utcnow()
    return now, 0


def calculate_priority_score(
    incorrect_count: int,
    consecutive_correct: int,
    days_overdue: float,
) -> float:
    """
    Higher priority = more urgent.

    Formula:
      base = incorrect_count * 2
      overdue_bonus = max(days_overdue, 0) * 1.5
      streak_penalty = consecutive_correct * 0.5  (more correct → less urgent)
    Clamped to [0.1, 100.0].
    """
    base = incorrect_count * 2.0
    overdue_bonus = max(days_overdue, 0.0) * 1.5
    streak_penalty = consecutive_correct * 0.5
    score = base + overdue_bonus - streak_penalty
    return max(0.1, min(100.0, score))
