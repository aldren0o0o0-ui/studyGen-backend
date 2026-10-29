import pytest
import uuid
from datetime import datetime, timezone, timedelta
from app.database.models import (
    StudySet,
    Flashcard,
    FlashcardReviewState,
    FlashcardReviewSession,
    FlashcardReviewSessionItem,
    StudyGoal,
    Document,
    Quiz,
    Question,
)
from app.services.flashcard_service import (
    create_study_set,
    get_study_set,
    list_study_sets,
    update_study_set,
    delete_study_set,
    add_flashcard,
    update_flashcard,
    delete_flashcard,
)
from app.services.flashcard_review_service import (
    create_review_session,
    get_review_session,
    reveal_session_card,
    rate_session_card,
    complete_review_session,
    calculate_flashcard_rating_schedule,
)
from app.services.quiz_to_flashcard_service import convert_quiz_questions_to_flashcards
from app.schemas.flashcards import (
    StudySetCreate,
    StudySetUpdate,
    FlashcardCreate,
    FlashcardUpdate,
    CreateReviewSessionRequest,
    ReviewRatingRequest,
)

def test_sm2_flashcard_schedule_logic():
    now = datetime.now(timezone.utc)
    mock_state = FlashcardReviewState(
        id="test_st",
        flashcard_id="fc1",
        review_status="LEARNING",
        ease_level=2.5,
        review_count=3,
        correct_count=2,
        incorrect_count=1,
        consecutive_correct=1,
        interval_days=5,
        last_reviewed_at=now,
        next_review_at=now,
        mastery_score=50.0,
    )
    
    # Rating "again" -> reset interval to 0 day
    next_dt, new_int, new_consec, new_status, was_corr, new_mastery = calculate_flashcard_rating_schedule(
        state=mock_state, rating="again", now=now
    )
    assert new_int == 0
    assert was_corr is False

    # Rating "easy" -> interval expanded
    next_dt, new_int, new_consec, new_status, was_corr, new_mastery = calculate_flashcard_rating_schedule(
        state=mock_state, rating="easy", now=now
    )
    assert new_int >= 4
    assert was_corr is True

def test_create_and_manage_study_set(db_session):
    set_in = StudySetCreate(
        title="Python Fundamentals",
        description="Core concepts of Python language",
        initial_cards=[
            FlashcardCreate(
                front_text="What is PEP 8?",
                back_text="The official style guide for Python code.",
                card_type="definition",
                difficulty="easy"
            ),
            FlashcardCreate(
                front_text="What is a list comprehension?",
                back_text="A concise way to create lists in Python.",
                card_type="concept",
                difficulty="medium"
            )
        ]
    )
    study_set = create_study_set(db_session, set_in)
    assert study_set.id is not None
    assert study_set.title == "Python Fundamentals"
    assert study_set.card_count == 2

    # Fetch
    fetched = get_study_set(db_session, study_set.id)
    assert fetched is not None
    assert len(fetched.cards) == 2

    # Update
    updated = update_study_set(db_session, study_set.id, StudySetUpdate(title="Python Core Concepts"))
    assert updated.title == "Python Core Concepts"

def test_flashcard_crud(db_session):
    study_set = create_study_set(db_session, StudySetCreate(title="Test Set"))
    
    # Add card
    card = add_flashcard(
        db_session,
        study_set.id,
        FlashcardCreate(front_text="Q1", back_text="A1", card_type="concept", difficulty="easy")
    )
    assert card.id is not None
    assert card.front_text == "Q1"
    
    # Update card
    updated_card = update_flashcard(db_session, card.id, FlashcardUpdate(front_text="Q1 Updated"))
    assert updated_card.front_text == "Q1 Updated"
    
    # Delete card
    delete_flashcard(db_session, card.id)
    db_session.refresh(study_set)
    assert study_set.card_count == 0

def test_flashcard_review_session_flow(db_session):
    study_set = create_study_set(db_session, StudySetCreate(
        title="Review Session Test",
        initial_cards=[
            FlashcardCreate(front_text="Card 1 Q", back_text="Card 1 A"),
            FlashcardCreate(front_text="Card 2 Q", back_text="Card 2 A")
        ]
    ))

    # Start session
    session = create_review_session(
        db_session,
        CreateReviewSessionRequest(study_set_id=study_set.id, session_mode="all_cards")
    )
    assert session.id is not None
    assert session.actual_count == 2
    assert session.reviewed_count == 0
    assert session.status == "ACTIVE"
    
    items = session.items
    first_item = items[0]
    assert first_item.is_revealed is False

    # Reveal card
    revealed = reveal_session_card(db_session, session.id, first_item.id)
    assert revealed.back_text in ("Card 1 A", "Card 2 A")
    assert revealed.is_revealed is True

    # Rate card ("good")
    rating_res = rate_session_card(
        db_session,
        session.id,
        first_item.id,
        "good"
    )
    assert rating_res.is_revealed is True

    # Second item reveal & rate
    second_item = items[1]
    reveal_session_card(db_session, session.id, second_item.id)
    rate_session_card(
        db_session,
        session.id,
        second_item.id,
        "easy"
    )

    # Complete session
    summary = complete_review_session(db_session, session.id)
    assert summary.status == "COMPLETED"
    assert summary.reviewed_count == 2

def test_quiz_to_flashcard_conversion(db_session):
    # Setup mock document & quiz
    doc_id = str(uuid.uuid4())
    doc = Document(id=doc_id, original_filename="physics.pdf", file_type="pdf", file_size=1024, status="PROCESSING")
    db_session.add(doc)

    quiz_id = str(uuid.uuid4())
    quiz = Quiz(
        id=quiz_id,
        document_id=doc_id,
        title="Physics Quiz 1",
        difficulty="medium",
        question_count=1,
        chunks_used=1,
    )
    db_session.add(quiz)
    db_session.flush()

    question = Question(
        id=str(uuid.uuid4()),
        quiz_id=quiz_id,
        position=1,
        question_text="What is force?",
        options_json='["Mass x Acceleration", "Velocity / Time"]',
        correct_answer="Mass x Acceleration",
        explanation="Newton's second law",
        question_type="multiple_choice",
        difficulty="medium",
        topic_label="Kinematics",
        accepted_answers_json='["Mass x Acceleration"]',
    )
    db_session.add(question)
    db_session.commit()

    # Convert to flashcards
    converted_set, created_flashcards, _ = convert_quiz_questions_to_flashcards(
        db_session, quiz_id=quiz_id, custom_title="Physics Flashcards"
    )
    assert converted_set.id is not None
    assert converted_set.card_count == 1
    assert created_flashcards[0].front_text == "What is force?"
    assert "Mass x Acceleration" in created_flashcards[0].back_text

def test_api_endpoints_flashcards(client):
    # Create study set
    res = client.post("/study-sets", json={
        "title": "API Test Set",
        "initial_cards": [
            {"front_text": "API Q1", "back_text": "API A1"}
        ]
    })
    assert res.status_code == 201
    set_data = res.json()
    set_id = set_data["study_set_id"]

    # List study sets
    res_list = client.get("/study-sets")
    assert res_list.status_code == 200
    assert res_list.json()["total"] >= 1

    # Overdue study sets
    res_due = client.get("/study-sets/due")
    assert res_due.status_code == 200

    # Start session
    res_session = client.post("/flashcard-review-sessions", json={
        "study_set_id": set_id,
        "session_mode": "all_cards"
    })
    assert res_session.status_code == 201
    session_data = res_session.json()
    session_id = session_data["session_id"]
    item_id = session_data["items"][0]["item_id"]

    # Reveal card
    res_reveal = client.post(f"/flashcard-review-sessions/{session_id}/items/{item_id}/reveal")
    assert res_reveal.status_code == 200
    assert res_reveal.json()["back_text"] == "API A1"

    # Rate card
    res_rate = client.post(f"/flashcard-review-sessions/{session_id}/items/{item_id}/rate", json={
        "rating": "good"
    })
    assert res_rate.status_code == 200

    # Complete session
    res_comp = client.post(f"/flashcard-review-sessions/{session_id}/complete")
    assert res_comp.status_code == 200
    assert res_comp.json()["status"] == "COMPLETED"
