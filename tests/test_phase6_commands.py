from datetime import datetime

from app.commands.check_chat_consistency import audit
from app.commands.rebuild_chat_metrics import rebuild
from app.database.models.chat import ChatSession
from app.database.session import SessionLocal


def test_chat_commands_are_dry_run_and_idempotent():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        db.add(ChatSession(
            id="command-session",
            title="Command",
            status="ARCHIVED",
            document_scope_mode="single_document",
            system_mode="document_qa",
            message_count=99,
            regeneration_count=0,
            created_at=now,
            updated_at=now,
        ))
        db.commit()
    finally:
        db.close()

    dry = rebuild(apply=False)
    assert dry["sessions_changed"] == 1
    assert rebuild(apply=False)["sessions_changed"] == 1
    assert rebuild(apply=True)["sessions_changed"] == 1
    assert rebuild(apply=True)["sessions_changed"] == 0
    result = audit(repair_message_counts=False)
    assert result["incorrect_session_message_counts"] == 0
