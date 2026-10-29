import json
import pytest
from starlette.testclient import TestClient
from main import app

client = TestClient(app)
from tests.conftest import make_pdf_bytes

resp = client.post("/documents/upload", files={"file": ("notes.pdf", make_pdf_bytes(), "application/pdf")})
doc_id = resp.json()["document_id"]

quiz_resp = client.post("/quizzes/generate", json={"document_id": doc_id, "question_count": 3, "difficulty": "medium"})
quiz = quiz_resp.json()
quiz_id = quiz["quiz_id"]

answers = []
for q in quiz["questions"]:
    answers.append({"question_id": q["question_id"], "selected_answer": "wrong_answer"})

resp = client.post(f"/quizzes/{quiz_id}/attempts", json={"answers": answers})
print(resp.status_code)
print(resp.json())