"""Idempotently rebuild stored chat-session counters and activity timestamps."""
from __future__ import annotations

import argparse

from sqlalchemy import func, select

from app.database.models.chat import ChatMessage, ChatSession
from app.database.session import SessionLocal


def rebuild(*, apply: bool = False) -> dict[str, int]:
    db = SessionLocal()
    changed = 0
    scanned = 0
    try:
        for session in db.execute(select(ChatSession)).scalars():
            scanned += 1
            actual_count = int(db.execute(
                select(func.count(ChatMessage.id))
                .where(ChatMessage.chat_session_id == session.id)
            ).scalar_one() or 0)
            actual_last = db.execute(
                select(func.max(ChatMessage.created_at))
                .where(ChatMessage.chat_session_id == session.id)
            ).scalar_one()
            if session.message_count != actual_count or session.last_message_at != actual_last:
                changed += 1
                if apply:
                    session.message_count = actual_count
                    session.last_message_at = actual_last
        if apply:
            db.commit()
        else:
            db.rollback()
        return {"sessions_scanned": scanned, "sessions_changed": changed}
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild chat metrics (dry run by default).")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = rebuild(apply=args.apply)
    for key, value in result.items():
        print(f"{key}: {value}")
    print("mode:", "apply" if args.apply else "dry-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
