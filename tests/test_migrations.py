import os
import pytest
from pathlib import Path
from alembic.config import Config
from alembic import command
from sqlalchemy import create_engine, text

ALEMBIC_INI_PATH = os.path.join(os.path.dirname(__file__), "..", "alembic.ini")

@pytest.fixture
def temp_db_engine(tmp_path):
    # Create a clean sqlite DB
    db_path = tmp_path / "test_migration.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    yield engine, db_url
    engine.dispose()


def test_alembic_lifecycle(temp_db_engine, monkeypatch):
    engine, db_url = temp_db_engine
    
    # Override the database URL in environment for Alembic
    monkeypatch.setenv("DATABASE_URL", db_url)

    # Configure Alembic
    alembic_cfg = Config(ALEMBIC_INI_PATH)
    # Set script location relative to alembic.ini
    script_location = os.path.join(os.path.dirname(ALEMBIC_INI_PATH), "alembic")
    alembic_cfg.set_main_option("script_location", script_location)
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    # 1. Upgrade to Phase 2 (0001)
    command.upgrade(alembic_cfg, "0001")

    # Insert mock Phase 2 data
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO documents (id, original_filename, file_type, file_size, chunk_count, status, created_at, updated_at)
            VALUES ('doc1', 'test.pdf', 'pdf', 1000, 5, 'READY', '2026-07-20 12:00:00', '2026-07-20 12:00:00')
        """))
        conn.execute(text("""
            INSERT INTO quizzes (id, document_id, title, difficulty, question_count, question_types_json, chunks_used, created_at)
            VALUES ('quiz1', 'doc1', 'Test Quiz', 'medium', 1, '["multiple_choice"]', 2, '2026-07-20 12:00:00')
        """))
        conn.execute(text("""
            INSERT INTO questions (id, quiz_id, position, question_text, options_json, correct_answer, explanation, source_chunk_indexes_json, created_at)
            VALUES ('q1', 'quiz1', 0, 'What?', '["A","B"]', 'A', 'Exp', '[0, 1]', '2026-07-20 12:00:00')
        """))

    # 2. Upgrade to Phase 3 (head)
    command.upgrade(alembic_cfg, "head")

    # Verify backfills
    with engine.begin() as conn:
        res = conn.execute(text("SELECT coverage_mode, prompt_version, quiz_mode FROM quizzes WHERE id='quiz1'")).fetchone()
        assert res.coverage_mode == "balanced"
        assert res.prompt_version == "legacy_phase2"
        assert res.quiz_mode == "generated"

        res_q = conn.execute(text("SELECT question_type, difficulty FROM questions WHERE id='q1'")).fetchone()
        assert res_q.question_type == "multiple_choice"
        assert res_q.difficulty == "medium"  # inherited from quiz

    # 3. Downgrade to 0001
    command.downgrade(alembic_cfg, "0001")

    # Confirm Phase 3 columns are gone but records exist
    with engine.begin() as conn:
        res_q = conn.execute(text("SELECT * FROM questions WHERE id='q1'")).fetchone()
        assert "question_type" not in res_q._mapping.keys()

    # 4. Re-upgrade to head
    command.upgrade(alembic_cfg, "head")
    with engine.begin() as conn:
        res_q2 = conn.execute(text("SELECT question_type FROM questions WHERE id='q1'")).fetchone()
        assert res_q2.question_type == "multiple_choice"


class TestLegacyMigrationEndpoint:
    def test_legacy_migration_disabled_by_default_returns_410(self, client):
        payload = [
            {
                "id": "legacy-1",
                "quiz_title": "Old Quiz",
                "timestamp": "2026-01-01T00:00:00Z",
                "questions": [
                    {
                        "question": "Old Question",
                        "options": ["A", "B"],
                        "answer": "A"
                    }
                ]
            }
        ]
        resp = client.post("/quizzes/migrate", json=payload)
        assert resp.status_code == 410

    def test_legacy_migration_when_enabled(self, client):
        client.app.state.settings.enable_legacy_history_migration = True
        try:
            payload = [
                {
                    "id": "legacy-1",
                    "quiz_title": "Migrated Quiz 1",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "questions": [
                        {
                            "question": "What is Python?",
                            "options": ["Language", "Snake"],
                            "answer": "Language"
                        }
                    ]
                }
            ]
            resp = client.post("/quizzes/migrate", json=payload)
            assert resp.status_code == 201
            assert resp.json()["imported"] == 1

            # Duplicate migration (idempotency check): running same payload again imports 0
            resp_dupe = client.post("/quizzes/migrate", json=payload)
            assert resp_dupe.status_code == 201
            assert resp_dupe.json()["imported"] == 0
        finally:
            client.app.state.settings.enable_legacy_history_migration = False

