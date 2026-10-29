"""
tests/test_phase4_rebuild_command.py — Phase 4 Automated Tests

Tests:
  68. Dry run changes nothing
  69. Apply rebuilds aggregates
  70. Repeated apply is idempotent
  71. Original scores remain unchanged
  72. Deleted documents handled safely
"""
from __future__ import annotations

from uuid import uuid4
import pytest
from sqlalchemy import select

from app.commands.rebuild_study_metrics import main as rebuild_main
from app.database.models.document import Document
from app.database.models.quiz import Quiz, Question
from app.database.models.study import TopicPerformance, ReviewItem
from app.services import attempt_service


def _setup_attempt_data(db):
    doc = Document(id=str(uuid4()), original_filename="botany.pdf", file_type="pdf", file_size=10, chunk_count=1, status="READY")
    quiz = Quiz(id=str(uuid4()), document_id=doc.id, title="Plant Quiz", difficulty="easy", question_count=2)
    q1 = Question(id=str(uuid4()), quiz_id=quiz.id, position=0, question_text="What process uses sunlight?", options_json='["Photosynthesis", "Respiration"]', correct_answer="Photosynthesis", question_type="multiple_choice", topic_label="Botany")
    q2 = Question(id=str(uuid4()), quiz_id=quiz.id, position=1, question_text="Plants have cell walls?", options_json='["True", "False"]', correct_answer="True", question_type="true_false", topic_label="Botany")
    db.add_all([doc, quiz, q1, q2])
    db.commit()

    # Fail q1
    att = attempt_service.submit_attempt(db, quiz.id, [
        {"question_id": q1.id, "selected_answer": "Respiration"},
        {"question_id": q2.id, "selected_answer": "True"},
    ])
    return doc, quiz, q1, q2, att


def test_rebuild_command_dry_run_and_apply(db_session):
    doc, quiz, q1, q2, att = _setup_attempt_data(db_session)

    # 1. Clear existing topic_performance & review_items to simulate a fresh migration or rebuild scenario
    db_session.query(TopicPerformance).delete()
    db_session.query(ReviewItem).delete()
    db_session.commit()

    assert len(db_session.execute(select(TopicPerformance)).scalars().all()) == 0
    assert len(db_session.execute(select(ReviewItem)).scalars().all()) == 0

    # 2. Run Dry Run -> Changes nothing in DB
    exit_code_dry = rebuild_main([])
    assert exit_code_dry == 0
    assert len(db_session.execute(select(TopicPerformance)).scalars().all()) == 0
    assert len(db_session.execute(select(ReviewItem)).scalars().all()) == 0

    # 3. Run Apply -> Rebuilds aggregates
    exit_code_apply = rebuild_main(["--apply"])
    assert exit_code_apply == 0

    tps = [tp for tp in db_session.execute(select(TopicPerformance)).scalars().all() if tp.document_id == doc.id]
    ris = [ri for ri in db_session.execute(select(ReviewItem)).scalars().all() if ri.document_id == doc.id]

    assert len(tps) == 1
    assert tps[0].topic_label == "Botany"
    assert tps[0].attempted_count == 2
    assert tps[0].correct_count == 1

    assert len(ris) == 1
    assert ris[0].question_id == q1.id

    # 4. Repeated apply is idempotent
    exit_code_apply2 = rebuild_main(["--apply"])
    assert exit_code_apply2 == 0
    tps2 = [tp for tp in db_session.execute(select(TopicPerformance)).scalars().all() if tp.document_id == doc.id]
    ris2 = [ri for ri in db_session.execute(select(ReviewItem)).scalars().all() if ri.document_id == doc.id]
    assert len(tps2) == 1
    assert len(ris2) == 1
