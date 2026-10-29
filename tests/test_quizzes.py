"""
test_quizzes.py — Phase 3
Tests covering context selection, quiz generation, and API endpoint.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest

from tests.conftest import (
    _InMemoryCollection,
    make_pdf_bytes,
    SAMPLE_QUESTIONS,
)
from app.services.context_selection import select_context_chunks, SelectedChunk
from app.services.question_validation import validate_questions
from app.schemas.quizzes import GenerateQuizRequest


# ===========================================================================
# Unit tests: context_selection
# ===========================================================================

_TOPICS = [
    "Photosynthesis converts sunlight into glucose using chlorophyll in plant cells.",
    "Mitosis is the process of cell division producing two identical daughter cells.",
    "DNA replication ensures genetic information is copied before cell division occurs.",
    "RNA transcription transfers genetic code from DNA to messenger RNA molecules.",
    "Protein synthesis occurs at ribosomes translating mRNA into amino acid chains.",
    "Enzymes are biological catalysts that speed up chemical reactions in organisms.",
    "The cell membrane is a phospholipid bilayer controlling substance entry and exit.",
    "Osmosis moves water across membranes from low to high solute concentration areas.",
    "ATP is the energy currency of cells produced mainly in the mitochondria.",
    "Homeostasis maintains stable internal conditions despite external environmental changes.",
]


def _populate_collection(col: _InMemoryCollection, doc_id: str, n: int = 10) -> None:
    n = min(n, len(_TOPICS))
    col.add(
        documents=[_TOPICS[i] for i in range(n)],
        ids=[f"{doc_id}:{i}" for i in range(n)],
        metadatas=[
            {"document_id": doc_id, "filename": "bio.pdf", "file_type": "pdf",
             "chunk_index": i, "page_number": (i // 3) + 1}
            for i in range(n)
        ],
    )


class TestContextSelection:
    def test_document_not_found_raises(self):
        col = _InMemoryCollection()
        with pytest.raises(ValueError, match="No chunks found"):
            select_context_chunks(col, "nonexistent-id")

    def test_retrieval_scoped_to_document_id(self):
        col = _InMemoryCollection()
        doc_id_a = str(uuid.uuid4())
        doc_id_b = str(uuid.uuid4())

        _populate_collection(col, doc_id_a, n=5)
        _populate_collection(col, doc_id_b, n=5)

        chunks = select_context_chunks(col, doc_id_a, max_chars=999_999)
        for chunk in chunks:
            assert chunk.document_id == doc_id_a

    def test_focused_mode_by_topic(self):
        col = _InMemoryCollection()
        doc_id = str(uuid.uuid4())
        _populate_collection(col, doc_id, n=10)

        chunks = select_context_chunks(
            col, doc_id, max_chars=999_999,
            coverage_mode="focused", topic_query="Photosynthesis"
        )
        assert len(chunks) > 0


# ===========================================================================
# Unit tests: quiz_generation parsing
# ===========================================================================

class TestQuizParsing:
    def _dummy_chunks(self):
        return [SelectedChunk(text="some text", chunk_index=0, page_number=1,
                              document_id="x", filename="f.pdf")]

    def test_valid_json_parses_correctly(self):
        chunks = self._dummy_chunks()
        # Mocking the AI output
        raw = [
            {
                "question_type": "multiple_choice",
                "question": "What is the capital?",
                "options": ["Paris", "London", "Berlin", "Rome"],
                "correct_answer": "Paris",
                "source_labels": ["SOURCE_1"]
            }
        ]
        result = validate_questions(raw, chunks)
        assert len(result) == 1

    def test_invalid_options_filtered(self):
        chunks = self._dummy_chunks()
        bad_question = [{
            "question_type": "multiple_choice",
            "question": "What is X?",
            "options": ["A", "B", "C", "D"],
            "correct_answer": "Z",  # not in options
            "source_labels": ["SOURCE_1"]
        }]
        result = validate_questions(bad_question, chunks)
        assert len(result) == 0

    def test_duplicate_questions_filtered(self):
        chunks = self._dummy_chunks()
        same_q = {
            "question_type": "multiple_choice",
            "question": "What is the capital of France?",
            "options": ["Paris", "London", "Berlin", "Rome"],
            "correct_answer": "Paris",
            "source_labels": ["SOURCE_1"]
        }
        result = validate_questions([same_q, same_q], chunks)
        assert len(result) == 1


# ===========================================================================
# Integration tests: quiz generation endpoint
# ===========================================================================

class TestQuizEndpoint:
    def _upload_and_get_doc_id(self, client) -> str:
        resp = client.post(
            "/documents/upload",
            files={"file": ("notes.pdf", make_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 201
        return resp.json()["document_id"]

    def test_generate_quiz_returns_structured_response(self, client):
        doc_id = self._upload_and_get_doc_id(client)
        resp = client.post(
            "/quizzes/generate",
            json={
                "document_id": doc_id,
                "question_count": 3,
                "difficulty": "medium",
                "question_types": ["multiple_choice"]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["document_id"] == doc_id
        assert len(data["questions"]) > 0
        # Phase 3: ActiveQuizResponse hides correct answers
        assert "correct_answer" not in data["questions"][0]

    def test_invalid_gemini_json_handled_gracefully(self, client):
        doc_id = self._upload_and_get_doc_id(client)
        bad_model = MagicMock()
        bad_model.generate_content.return_value.text = "not json at all >>>"
        client.app.state.gemini_model = bad_model

        resp = client.post(
            "/quizzes/generate",
            json={"document_id": doc_id, "question_count": 3},
        )
        assert resp.status_code == 502

    def test_generate_quiz_returns_config_error_when_gemini_unavailable(self, client):
        doc_id = self._upload_and_get_doc_id(client)
        client.app.state.gemini_available = False
        client.app.state.gemini_unavailable_reason = "GEMINI_API_KEY is invalid."

        resp = client.post(
            "/quizzes/generate",
            json={"document_id": doc_id, "question_count": 3},
        )

        assert resp.status_code == 503
        assert resp.json()["detail"] == "GEMINI_API_KEY is invalid."

def _strip_markdown_fences(text: str) -> str:
    import re
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


# ===========================================================================
# Active-Quiz Security Tests (Recursive hidden field assertion)
# ===========================================================================

FORBIDDEN_ACTIVE_FIELDS = {
    "correct_answer",
    "accepted_answers",
    "accepted_answers_json",
    "explanation",
    "is_correct",
    "source_references_json",
}


def assert_no_hidden_fields(payload):
    """Recursively inspect active quiz payload to ensure no answer/explanation fields exist."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in FORBIDDEN_ACTIVE_FIELDS, f"Forbidden active field '{key}' found in payload!"
            assert_no_hidden_fields(value)
    elif isinstance(payload, list):
        for item in payload:
            assert_no_hidden_fields(item)


class TestActiveQuizSecurity:
    def _upload_and_get_doc_id(self, client) -> str:
        resp = client.post(
            "/documents/upload",
            files={"file": ("security_test.pdf", make_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 201
        return resp.json()["document_id"]

    def test_generate_quiz_active_payload_security(self, client):
        doc_id = self._upload_and_get_doc_id(client)
        resp = client.post(
            "/quizzes/generate",
            json={"document_id": doc_id, "question_count": 3, "difficulty": "medium"},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert_no_hidden_fields(payload)

    def test_get_quizzes_list_active_payload_security(self, client):
        resp = client.get("/quizzes")
        assert resp.status_code == 200
        payload = resp.json()
        assert_no_hidden_fields(payload)

    def test_get_document_detail_active_payload_security(self, client):
        doc_id = self._upload_and_get_doc_id(client)
        resp = client.get(f"/documents/{doc_id}")
        assert resp.status_code == 200
        payload = resp.json()
        assert_no_hidden_fields(payload)


# ===========================================================================
# Allocation Tests (Question types and Difficulty)
# ===========================================================================

from app.services.quiz_generation import allocate_question_types, allocate_difficulties


class TestAllocationLogic:
    def test_question_type_allocations(self):
        types = ["multiple_choice", "true_false", "identification"]

        # 1 question with 3 selected types: allocate in request order
        alloc1 = allocate_question_types(types, 1)
        assert sum(alloc1.values()) == 1
        assert alloc1["multiple_choice"] == 1
        assert alloc1["true_false"] == 0
        assert alloc1["identification"] == 0

        # 2 questions with 3 selected types
        alloc2 = allocate_question_types(types, 2)
        assert sum(alloc2.values()) == 2
        assert alloc2["multiple_choice"] == 1
        assert alloc2["true_false"] == 1
        assert alloc2["identification"] == 0

        # 3 questions with 3 selected types
        alloc3 = allocate_question_types(types, 3)
        assert sum(alloc3.values()) == 3
        assert alloc3 == {"multiple_choice": 1, "true_false": 1, "identification": 1}

        # 8 questions with 3 selected types
        alloc8 = allocate_question_types(types, 8)
        assert sum(alloc8.values()) == 8
        assert alloc8 == {"multiple_choice": 3, "true_false": 3, "identification": 2}
        counts = list(alloc8.values())
        assert max(counts) - min(counts) <= 1

        # 20 questions with 3 selected types
        alloc20 = allocate_question_types(types, 20)
        assert sum(alloc20.values()) == 20
        assert alloc20 == {"multiple_choice": 7, "true_false": 7, "identification": 6}

        # Determinism check: same request produces exact same allocation
        alloc_repeat = allocate_question_types(types, 8)
        assert alloc8 == alloc_repeat

    def test_mixed_difficulty_allocations(self):
        # Non-mixed returns None
        assert allocate_difficulties("medium", 5) is None

        # Mixed difficulty allocates easy, medium, hard round-robin
        diff8 = allocate_difficulties("mixed", 8)
        assert diff8 is not None
        assert sum(diff8.values()) == 8
        assert diff8 == {"easy": 3, "medium": 3, "hard": 2}
        assert "mixed" not in diff8  # No stored question has 'mixed'


# ===========================================================================
# Source Validation Tests
# ===========================================================================

class TestSourceValidation:
    def _dummy_chunks(self, doc_id="doc-123"):
        return [
            SelectedChunk(text="Chunk 1 content regarding photosynthesis.", chunk_index=0, page_number=1, document_id=doc_id, filename="bio.pdf"),
            SelectedChunk(text="Chunk 2 content regarding cellular respiration.", chunk_index=1, page_number=2, document_id=doc_id, filename="bio.pdf"),
        ]

    def test_valid_source_label_mapping(self):
        chunks = self._dummy_chunks()
        raw = [{
            "question_type": "multiple_choice",
            "question": "What is in chunk 1?",
            "options": ["Photosynthesis", "Respiration"],
            "correct_answer": "Photosynthesis",
            "source_labels": ["SOURCE_1"]
        }]
        res = validate_questions(raw, chunks, require_sources=True)
        assert len(res) == 1
        assert len(res[0].sources) == 1
        assert res[0].sources[0].chunk_index == 0
        assert res[0].sources[0].page_number == 1
        assert res[0].sources[0].filename == "bio.pdf"

    def test_invented_source_label_rejection(self):
        chunks = self._dummy_chunks()
        raw = [{
            "question_type": "multiple_choice",
            "question": "Invented label question?",
            "options": ["A", "B"],
            "correct_answer": "A",
            "source_labels": ["SOURCE_99"]  # Invented
        }]
        res = validate_questions(raw, chunks, require_sources=True)
        assert len(res) == 0  # rejected because no valid sources remained

    def test_source_label_wrong_document(self):
        # Context chunks belong to doc-123. A chunk from doc-999 is NOT in source_map.
        chunks = self._dummy_chunks("doc-123")
        raw = [{
            "question_type": "multiple_choice",
            "question": "Wrong doc question?",
            "options": ["X", "Y"],
            "correct_answer": "X",
            "source_labels": ["SOURCE_5"]  # Only SOURCE_1 and SOURCE_2 exist
        }]
        res = validate_questions(raw, chunks, require_sources=True)
        assert len(res) == 0

    def test_server_generated_excerpt_from_real_chunk(self):
        chunks = [SelectedChunk(text="A " * 200, chunk_index=0, page_number=1, document_id="d", filename="f.pdf")]
        raw = [{
            "question_type": "multiple_choice",
            "question": "Long excerpt question?",
            "options": ["A", "B"],
            "correct_answer": "A",
            "source_labels": ["SOURCE_1"]
        }]
        res = validate_questions(raw, chunks)
        assert len(res) == 1
        excerpt = res[0].sources[0].excerpt
        assert len(excerpt) <= 205
        assert excerpt.startswith("A ")

    def test_ai_provided_excerpt_ignored(self):
        chunks = self._dummy_chunks()
        raw = [{
            "question_type": "multiple_choice",
            "question": "AI excerpt test?",
            "options": ["A", "B"],
            "correct_answer": "A",
            "source_labels": ["SOURCE_1"],
            "excerpt": "Fake AI excerpt that should be completely ignored"
        }]
        res = validate_questions(raw, chunks)
        assert len(res) == 1
        assert "Fake AI excerpt" not in res[0].sources[0].excerpt
        assert "photosynthesis" in res[0].sources[0].excerpt

    def test_multiple_valid_source_labels_mapping(self):
        chunks = self._dummy_chunks()
        raw = [{
            "question_type": "multiple_choice",
            "question": "Multi source question?",
            "options": ["A", "B"],
            "correct_answer": "A",
            "source_labels": ["SOURCE_1", "SOURCE_2"]
        }]
        res = validate_questions(raw, chunks)
        assert len(res) == 1
        assert len(res[0].sources) == 2
        assert res[0].sources[0].chunk_index == 0
        assert res[0].sources[1].chunk_index == 1


# ===========================================================================
# Question-Type-Aware Grounding Support Tests
# ===========================================================================

from app.services.question_validation import normalize_identification_answer


class TestGroundingSupport:
    def test_identification_grounding_and_normalization(self):
        # Test normalization logic
        assert normalize_identification_answer("  Glucose! ") == "glucose"
        assert normalize_identification_answer("ATP (Energy)") == "atp (energy)"
        assert normalize_identification_answer("Cell Division...") == "cell division"

        # Safe singular/plural and case differences
        raw = [{
            "question_type": "identification",
            "question": "What organelle produces ATP?",
            "correct_answer": "Mitochondria",
            "accepted_answers": ["mitochondria", "mitochondrion"],
            "source_labels": ["SOURCE_1"]
        }]
        chunks = [SelectedChunk(text="ATP is produced in the mitochondria.", chunk_index=0, page_number=1, document_id="d", filename="f.pdf")]
        res = validate_questions(raw, chunks)
        assert len(res) == 1
        assert res[0].correct_answer == "Mitochondria"
        assert "mitochondrion" in res[0].accepted_answers

    def test_multiple_choice_grounding(self):
        chunks = [SelectedChunk(text="Chlorophyll absorbs red and blue light.", chunk_index=0, page_number=1, document_id="d", filename="f.pdf")]
        raw = [{
            "question_type": "multiple_choice",
            "question": "Which pigment absorbs light?",
            "options": ["Chlorophyll", "Hemoglobin", "Melanin", "Carotene"],
            "correct_answer": "Chlorophyll",
            "source_labels": ["SOURCE_1"]
        }]
        res = validate_questions(raw, chunks)
        assert len(res) == 1
        assert res[0].correct_answer == "Chlorophyll"
        assert "Chlorophyll" in res[0].options

    def test_true_false_grounding(self):
        chunks = [SelectedChunk(text="DNA replication is semiconservative.", chunk_index=0, page_number=1, document_id="d", filename="f.pdf")]
        raw = [{
            "question_type": "true_false",
            "question": "DNA replication is semiconservative.",
            "options": ["True", "False"],
            "correct_answer": "True",
            "source_labels": ["SOURCE_1"]
        }]
        res = validate_questions(raw, chunks)
        assert len(res) == 1
        assert res[0].options == ["True", "False"]
        assert res[0].correct_answer == "True"


# ===========================================================================
# Expanded Focused Retrieval Tests
# ===========================================================================

class TestFocusedRetrievalExpanded:
    def test_topic_query_filters_by_document_id(self):
        col = _InMemoryCollection()
        doc_id_1 = str(uuid.uuid4())
        doc_id_2 = str(uuid.uuid4())

        _populate_collection(col, doc_id_1, n=5)
        _populate_collection(col, doc_id_2, n=5)

        chunks = select_context_chunks(
            col, doc_id_1, max_chars=999_999,
            coverage_mode="focused", topic_query="Mitosis"
        )
        assert len(chunks) > 0
        for c in chunks:
            assert c.document_id == doc_id_1

    def test_topic_query_no_match_raises_error(self):
        col = _InMemoryCollection()
        doc_id = str(uuid.uuid4())
        # Empty collection for doc_id
        with pytest.raises(ValueError, match="No chunks found"):
            select_context_chunks(
                col, doc_id, max_chars=999_999,
                coverage_mode="focused", topic_query="Quantum Mechanics"
            )

    def test_selected_chunk_indexes_validation_and_ordering(self):
        col = _InMemoryCollection()
        doc_id = str(uuid.uuid4())
        _populate_collection(col, doc_id, n=10)

        # Out of order and duplicate indexes requested: [3, 1, 3]
        chunks = select_context_chunks(
            col, doc_id, max_chars=999_999,
            coverage_mode="focused", selected_chunk_indexes=[3, 1, 3]
        )
        # Should return sorted by document chunk index: [1, 3]
        indices = [c.chunk_index for c in chunks]
        assert indices == [1, 3]

    def test_selected_chunk_indexes_nonexistent_raises(self):
        col = _InMemoryCollection()
        doc_id = str(uuid.uuid4())
        _populate_collection(col, doc_id, n=5)

        with pytest.raises(ValueError, match="No chunks found"):
            select_context_chunks(
                col, doc_id, max_chars=999_999,
                coverage_mode="focused", selected_chunk_indexes=[99, 100]
            )
