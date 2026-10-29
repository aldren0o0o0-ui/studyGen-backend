from __future__ import annotations

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text


def test_phase6_upgrade_downgrade_reupgrade_preserves_phase5(tmp_path, monkeypatch):
    database = tmp_path / "phase6.db"
    url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "b2c3d4e5f6a7")
    engine = create_engine(url)
    now = "2026-07-27 00:00:00"
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO documents "
            "(id, original_filename, file_type, file_size, chunk_count, status, created_at, updated_at) "
            "VALUES ('phase5-doc', 'notes.pdf', 'pdf', 100, 1, 'READY', :now, :now)"
        ), {"now": now})
        connection.execute(text(
            "INSERT INTO study_sets "
            "(id, title, set_type, status, card_count, created_at, updated_at) "
            "VALUES ('phase5-set', 'Set', 'manual', 'ACTIVE', 0, :now, :now)"
        ), {"now": now})

    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO chat_sessions "
            "(id, title, status, document_scope_mode, system_mode, message_count, "
            "regeneration_count, created_at, updated_at) "
            "VALUES ('chat', 'Chat', 'ACTIVE', 'single_document', 'document_qa', 2, 0, :now, :now)"
        ), {"now": now})
        connection.execute(text(
            "INSERT INTO chat_session_documents "
            "(id, chat_session_id, document_id, position, created_at) "
            "VALUES ('link', 'chat', 'phase5-doc', 1, :now)"
        ), {"now": now})
        connection.execute(text(
            "INSERT INTO chat_messages "
            "(id, chat_session_id, role, content, status, sequence_number, "
            "follow_up_suggestions_json, insufficient_evidence, created_at, updated_at) "
            "VALUES ('message', 'chat', 'assistant', 'Answer', 'COMPLETED', 1, '[]', 0, :now, :now)"
        ), {"now": now})
        connection.execute(text(
            "INSERT INTO chat_citations "
            "(id, message_id, document_id, chunk_index, excerpt, source_order, created_at) "
            "VALUES ('citation', 'message', 'phase5-doc', 0, 'Excerpt', 1, :now)"
        ), {"now": now})

    # Downgrade all post-Phase-5 revisions. Phase 7-style additive migrations
    # may exist after the Phase 6 chat revision.
    command.downgrade(config, "b2c3d4e5f6a7")
    assert "chat_sessions" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT title FROM study_sets WHERE id='phase5-set'"
        )).scalar_one() == "Set"
        assert connection.execute(text(
            "SELECT original_filename FROM documents WHERE id='phase5-doc'"
        )).scalar_one() == "notes.pdf"

    command.upgrade(config, "head")
    assert "chat_sessions" in inspect(engine).get_table_names()
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0016_phase12a_1_session_security"]
