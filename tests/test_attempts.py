"""
test_attempts.py — Phase 3
Tests for attempts, scoring, and retry quiz generation.
"""
import pytest
from app.schemas.attempts import AttemptRequest, AnswerSubmission
from tests.conftest import make_pdf_bytes


class TestAttempts:
    def _upload_and_get_doc_id(self, client) -> str:
        resp = client.post(
            "/documents/upload",
            files={"file": ("notes.pdf", make_pdf_bytes(), "application/pdf")},
        )
        return resp.json()["document_id"]

    def _generate_quiz(self, client, doc_id: str) -> dict:
        resp = client.post(
            "/quizzes/generate",
            json={"document_id": doc_id, "question_count": 3, "difficulty": "medium"}
        )
        return resp.json()

    def test_submit_attempt_and_calculate_score(self, client):
        doc_id = self._upload_and_get_doc_id(client)
        quiz = self._generate_quiz(client, doc_id)
        quiz_id = quiz["quiz_id"]
        questions = quiz["questions"]

        # 3 questions, we only have options available because correct_answer is hidden
        answers = []
        for i, q in enumerate(questions):
            # Select the first option for all
            ans = q["options"][0] if q["options"] else "Answer"
            answers.append({"question_id": q["question_id"], "selected_answer": ans})

        resp = client.post(
            f"/quizzes/{quiz_id}/attempts",
            json={"answers": answers}
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["quiz_id"] == quiz_id
        assert data["score"] >= 0
        assert data["total_questions"] == 3
        assert "percentage" in data

    def test_list_attempts(self, client):
        resp = client.get("/attempts")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_retry_quiz_endpoint(self, client):
        # 1. Create a quiz
        doc_id = self._upload_and_get_doc_id(client)
        quiz = self._generate_quiz(client, doc_id)
        
        # 2. Submit an intentionally completely wrong attempt to force retry
        # We need at least one incorrect answer to trigger retry.
        # Since correct_answer is hidden, we'll try options[0] first.
        # If by some miracle all 3 are correct, the test might fail, but it's highly unlikely.
        # To be safe, we can inspect the attempt response and if it's 100%, do another attempt with different options.
        
        answers = []
        for q in quiz["questions"]:
            ans = q["options"][0] if q["options"] else "wrong_answer"
            answers.append({"question_id": q["question_id"], "selected_answer": ans})
        
        resp = client.post(f"/quizzes/{quiz['quiz_id']}/attempts", json={"answers": answers})
        data = resp.json()
        
        if data["score"] == data["total_questions"]:
            # All were correct! Submit again with options[1]
            answers = []
            for q in quiz["questions"]:
                ans = q["options"][1] if len(q["options"]) > 1 else "wrong"
                answers.append({"question_id": q["question_id"], "selected_answer": ans})
            resp = client.post(f"/quizzes/{quiz['quiz_id']}/attempts", json={"answers": answers})
            data = resp.json()

        attempt_id = data["attempt_id"]

        # 3. Retry quiz
        retry_resp = client.post(f"/attempts/{attempt_id}/retry-quiz")
        assert retry_resp.status_code == 201
        retry_data = retry_resp.json()
        
        # Should be a new quiz ID
        assert retry_data["quiz_id"] != quiz["quiz_id"]
        assert len(retry_data["questions"]) > 0


from unittest.mock import MagicMock
from tests.test_quizzes import assert_no_hidden_fields


class TestStrictRetryAndEdgeCases:
    def _upload_and_get_doc_id(self, client) -> str:
        resp = client.post(
            "/documents/upload",
            files={"file": ("retry_notes.pdf", make_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 201
        return resp.json()["document_id"]

    def test_retry_quiz_does_not_call_gemini_strict_mock(self, client):
        doc_id = self._upload_and_get_doc_id(client)

        gen_resp = client.post(
            "/quizzes/generate",
            json={"document_id": doc_id, "question_count": 3, "difficulty": "medium"},
        )
        quiz = gen_resp.json()
        quiz_id = quiz["quiz_id"]

        answers = [{"question_id": q["question_id"], "selected_answer": q["options"][0]} for q in quiz["questions"]]
        att_resp = client.post(f"/quizzes/{quiz_id}/attempts", json={"answers": answers})
        attempt_data = att_resp.json()

        if attempt_data["score"] == attempt_data["total_questions"]:
            answers = [{"question_id": q["question_id"], "selected_answer": q["options"][1]} for q in quiz["questions"]]
            att_resp = client.post(f"/quizzes/{quiz_id}/attempts", json={"answers": answers})
            attempt_data = att_resp.json()

        attempt_id = attempt_data["attempt_id"]

        strict_model = MagicMock()
        def _fail_call(*args, **kwargs):
            pytest.fail("Gemini API generation was called during retry generation!")
        strict_model.generate_content.side_effect = _fail_call
        strict_model.generate_quiz.side_effect = _fail_call
        client.app.state.gemini_model = strict_model

        retry_resp = client.post(f"/attempts/{attempt_id}/retry-quiz")
        assert retry_resp.status_code == 201
        retry_payload = retry_resp.json()

        assert_no_hidden_fields(retry_payload)
        assert retry_payload["quiz_id"] != quiz_id
        assert len(retry_payload["questions"]) > 0

    def test_retry_quiz_edge_cases(self, client):
        # 1. Nonexistent attempt -> 404
        resp = client.post("/attempts/nonexistent-attempt-id/retry-quiz")
        assert resp.status_code == 404

        # 2. Attempt with 100% score (no incorrect answers) -> 409 Conflict
        doc_id = self._upload_and_get_doc_id(client)
        quiz = client.post("/quizzes/generate", json={"document_id": doc_id, "question_count": 2}).json()

        detail = client.get(f"/quizzes/{quiz['quiz_id']}").json()
        correct_answers = [
            {"question_id": q["question_id"], "selected_answer": q["correct_answer"]}
            for q in detail["questions"]
        ]

        att_resp = client.post(f"/quizzes/{quiz['quiz_id']}/attempts", json={"answers": correct_answers})
        assert att_resp.json()["score"] == att_resp.json()["total_questions"]
        perfect_attempt_id = att_resp.json()["attempt_id"]

        retry_409 = client.post(f"/attempts/{perfect_attempt_id}/retry-quiz")
        assert retry_409.status_code == 409

    def test_multiple_retries_independent_and_immutable(self, client):
        doc_id = self._upload_and_get_doc_id(client)
        quiz = client.post("/quizzes/generate", json={"document_id": doc_id, "question_count": 3}).json()
        answers = [{"question_id": q["question_id"], "selected_answer": q["options"][0]} for q in quiz["questions"]]
        att = client.post(f"/quizzes/{quiz['quiz_id']}/attempts", json={"answers": answers}).json()

        if att["score"] == att["total_questions"]:
            answers = [{"question_id": q["question_id"], "selected_answer": q["options"][1]} for q in quiz["questions"]]
            att = client.post(f"/quizzes/{quiz['quiz_id']}/attempts", json={"answers": answers}).json()

        att_id = att["attempt_id"]

        retry1 = client.post(f"/attempts/{att_id}/retry-quiz").json()
        retry2 = client.post(f"/attempts/{att_id}/retry-quiz").json()

        assert retry1["quiz_id"] != retry2["quiz_id"]
        assert len(retry1["questions"]) == len(retry2["questions"])

    def test_completed_attempt_returns_feedback_after_submission(self, client):
        doc_id = self._upload_and_get_doc_id(client)
        quiz = client.post("/quizzes/generate", json={"document_id": doc_id, "question_count": 2}).json()
        answers = [{"question_id": q["question_id"], "selected_answer": q["options"][0]} for q in quiz["questions"]]
        att = client.post(f"/quizzes/{quiz['quiz_id']}/attempts", json={"answers": answers}).json()
        att_id = att["attempt_id"]

        resp = client.get(f"/attempts/{att_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "answers" in data
        assert len(data["answers"]) > 0
        first_ans = data["answers"][0]
        assert "correct_answer" in first_ans
        assert "is_correct" in first_ans
        assert "sources" in first_ans

