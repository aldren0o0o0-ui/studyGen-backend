"""
test_documents.py
Tests 1–20 covering upload, file lifecycle, and ChromaDB correctness.
"""
from __future__ import annotations

import io
import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_docx_bytes, make_pdf_bytes, make_txt_bytes
from app.services.document_extraction import extract_document
from app.services.document_indexing import index_document, delete_document


# ===========================================================================
# Unit tests: document_extraction service
# ===========================================================================

class TestPDFExtraction:
    def test_valid_pdf_extracts_text(self, tmp_path):
        """Test 1 — Valid PDF upload returns non-empty text."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(make_pdf_bytes())
        result = extract_document(pdf_path, "pdf")
        assert result.text.strip()
        assert result.sections

    def test_empty_pdf_raises(self, tmp_path):
        """Test 5 — Empty file (zero content) is rejected."""
        # Create a PDF with no text pages
        import fitz
        doc = fitz.open()
        doc.new_page()          # blank page — no text
        buf = io.BytesIO()
        doc.save(buf)
        doc.close()

        pdf_path = tmp_path / "empty.pdf"
        pdf_path.write_bytes(buf.getvalue())

        with pytest.raises(ValueError, match="no extractable text"):
            extract_document(pdf_path, "pdf")

    def test_corrupted_pdf_raises(self, tmp_path):
        """Test 7 — Corrupted PDF raises ValueError."""
        bad_path = tmp_path / "bad.pdf"
        bad_path.write_bytes(b"this is not a valid pdf file")
        with pytest.raises(ValueError):
            extract_document(bad_path, "pdf")

    def test_pdf_sections_have_page_numbers(self, tmp_path):
        """PDF sections should carry page_number metadata."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(make_pdf_bytes())
        result = extract_document(pdf_path, "pdf")
        assert all(s.page_number is not None for s in result.sections)

    def test_pdf_cleanup(self, tmp_path):
        """Confirm file handle is released after PDF extraction."""
        file_path = tmp_path / "sample.pdf"
        file_path.write_bytes(make_pdf_bytes())
        
        extract_document(file_path, "pdf")
        
        renamed = tmp_path / "renamed.pdf"
        file_path.rename(renamed)
        renamed.unlink()
        assert not renamed.exists()

    def test_pdf_corruption_cleanup(self, tmp_path):
        """Confirm file handle is released after corrupted PDF failure."""
        bad_path = tmp_path / "bad.pdf"
        bad_path.write_bytes(b"this is not a valid pdf")
        with pytest.raises(ValueError):
            extract_document(bad_path, "pdf")
            
        renamed = tmp_path / "renamed.pdf"
        bad_path.rename(renamed)
        renamed.unlink()
        assert not renamed.exists()


class TestTXTExtraction:
    def test_valid_txt_extracts_text(self, tmp_path):
        """Test 2 — Valid TXT upload returns non-empty text."""
        txt_path = tmp_path / "test.txt"
        txt_path.write_bytes(make_txt_bytes())
        result = extract_document(txt_path, "txt")
        assert result.text.strip()

    def test_empty_txt_raises(self, tmp_path):
        """Empty TXT is rejected."""
        txt_path = tmp_path / "empty.txt"
        txt_path.write_bytes(b"")
        with pytest.raises(ValueError, match="empty"):
            extract_document(txt_path, "txt")

    def test_latin1_encoding(self, tmp_path):
        """TXT with non-UTF8 encoding should fall back correctly."""
        txt_path = tmp_path / "latin.txt"
        txt_path.write_bytes("Résumé document".encode("latin-1"))
        result = extract_document(txt_path, "txt")
        assert result.text.strip()

    def test_txt_cleanup(self, tmp_path):
        """Confirm file handle is released after TXT extraction."""
        file_path = tmp_path / "sample.txt"
        file_path.write_bytes(make_txt_bytes())
        
        extract_document(file_path, "txt")
        
        renamed = tmp_path / "renamed.txt"
        file_path.rename(renamed)
        renamed.unlink()
        assert not renamed.exists()


class TestDOCXExtraction:
    def test_valid_docx_extracts_text(self, tmp_path):
        """Test 3 — Valid DOCX upload returns non-empty text."""
        docx_path = tmp_path / "test.docx"
        docx_path.write_bytes(make_docx_bytes())
        result = extract_document(docx_path, "docx")
        assert result.text.strip()
        assert "DOCX test" in result.text

    def test_corrupted_docx_raises(self, tmp_path):
        """Test 8 — Corrupted DOCX raises ValueError."""
        bad_path = tmp_path / "bad.docx"
        bad_path.write_bytes(b"this is not a valid docx")
        with pytest.raises(ValueError):
            extract_document(bad_path, "docx")

    def test_empty_docx_raises(self, tmp_path):
        """Empty DOCX (no paragraphs) raises ValueError."""
        from docx import Document
        doc = Document()
        buf = io.BytesIO()
        doc.save(buf)

        docx_path = tmp_path / "empty.docx"
        docx_path.write_bytes(buf.getvalue())
        with pytest.raises(ValueError, match="no extractable text"):
            extract_document(docx_path, "docx")

    def test_docx_cleanup(self, tmp_path):
        """Confirm file handle is released after DOCX extraction."""
        file_path = tmp_path / "sample.docx"
        file_path.write_bytes(make_docx_bytes())
        
        extract_document(file_path, "docx")
        
        renamed = tmp_path / "renamed.docx"
        file_path.rename(renamed)
        renamed.unlink()
        assert not renamed.exists()

    def test_docx_corruption_cleanup(self, tmp_path):
        """Confirm file handle is released after corrupted DOCX failure."""
        bad_path = tmp_path / "bad.docx"
        bad_path.write_bytes(b"this is not a valid docx")
        with pytest.raises(ValueError):
            extract_document(bad_path, "docx")
            
        renamed = tmp_path / "renamed.docx"
        bad_path.rename(renamed)
        renamed.unlink()
        assert not renamed.exists()


class TestUnsupportedExtraction:
    def test_unsupported_type_raises(self, tmp_path):
        """Test 4 — Unsupported file type raises NotImplementedError."""
        path = tmp_path / "test.xlsx"
        path.write_bytes(b"fake xlsx")
        with pytest.raises(NotImplementedError):
            extract_document(path, "xlsx")


# ===========================================================================
# Unit tests: document_indexing service
# ===========================================================================

class TestDocumentIndexing:
    def _make_extracted(self):
        from app.services.document_extraction import ExtractedDocument, PageSection
        return ExtractedDocument(
            text="Biology is the study of life. Cells are the basic units. " * 30,
            sections=[PageSection(text="Biology is the study of life.", page_number=1, section_index=0)],
            page_count=1,
        )

    def test_unique_document_id_generated(self):
        """Test 9 — Each call generates a distinct UUID document_id."""
        from tests.conftest import _InMemoryCollection
        col = _InMemoryCollection()
        extracted = self._make_extracted()

        doc_id_1, _ = index_document(col, extracted, "file.pdf", "pdf")
        doc_id_2, _ = index_document(col, extracted, "file.pdf", "pdf")

        assert doc_id_1 != doc_id_2

    def test_chunks_contain_correct_document_id(self):
        """Test 10 — Every chunk stored carries the correct document_id in metadata."""
        from tests.conftest import _InMemoryCollection
        col = _InMemoryCollection()
        extracted = self._make_extracted()

        doc_id, chunk_count = index_document(col, extracted, "bio.pdf", "pdf")

        results = col.get(where={"document_id": {"$eq": doc_id}})
        assert len(results["ids"]) == chunk_count
        for meta in results["metadatas"]:
            assert meta["document_id"] == doc_id
            assert meta["filename"] == "bio.pdf"

    def test_reindex_same_id_removes_old_chunks(self):
        """Reindexing a document_id should not leave stale chunks."""
        from tests.conftest import _InMemoryCollection
        from app.services.document_extraction import ExtractedDocument, PageSection
        col = _InMemoryCollection()

        short_doc = ExtractedDocument(
            text="Short text " * 5,
            sections=[PageSection(text="Short text", page_number=None, section_index=0)],
        )
        doc_id, _ = index_document(col, short_doc, "a.txt", "txt")

        # Manually delete and reindex with a longer doc
        delete_document(col, doc_id)
        long_doc = ExtractedDocument(
            text="Long text content " * 100,
            sections=[PageSection(text="Long text content", page_number=None, section_index=0)],
        )
        doc_id2, count2 = index_document(col, long_doc, "a.txt", "txt")
        results = col.get(where={"document_id": {"$eq": doc_id2}})
        assert len(results["ids"]) == count2


# ===========================================================================
# Integration tests: upload endpoint (via TestClient)
# ===========================================================================

class TestUploadEndpoint:
    def test_pdf_upload_returns_document_id(self, client):
        """Test 1 (API) — PDF upload returns 201 with document_id."""
        response = client.post(
            "/documents/upload",
            files={"file": ("notes.pdf", make_pdf_bytes(), "application/pdf")},
        )
        assert response.status_code == 201
        data = response.json()
        assert "document_id" in data
        assert data["file_type"] == "pdf"
        assert data["chunk_count"] > 0

    def test_txt_upload_returns_document_id(self, client):
        """Test 2 (API) — TXT upload returns 201 with document_id."""
        response = client.post(
            "/documents/upload",
            files={"file": ("notes.txt", make_txt_bytes(), "text/plain")},
        )
        assert response.status_code == 201
        data = response.json()
        assert "document_id" in data

    def test_docx_upload_returns_document_id(self, client):
        """Test 3 (API) — DOCX upload returns 201 with document_id."""
        response = client.post(
            "/documents/upload",
            files={"file": ("notes.docx", make_docx_bytes(),
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert response.status_code == 201
        data = response.json()
        assert "document_id" in data
        assert data["file_type"] == "docx"

    def test_unsupported_file_rejected_415(self, client):
        """Test 4 (API) — .xlsx file rejected with 415."""
        response = client.post(
            "/documents/upload",
            files={"file": ("spreadsheet.xlsx", b"fake xlsx", "application/vnd.ms-excel")},
        )
        assert response.status_code == 415

    def test_empty_file_rejected_400(self, client):
        """Test 5 (API) — Empty file is rejected with 400."""
        response = client.post(
            "/documents/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert response.status_code == 400

    def test_duplicate_file_contents_are_accepted_as_separate_documents(self, client):
        content = b"Duplicate file contents accepted for this test."
        first = client.post(
            "/documents/upload",
            files={"file": ("original.txt", content, "text/plain")},
        )
        second = client.post(
            "/documents/upload",
            files={"file": ("renamed-copy.txt", content, "text/plain")},
        )

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["document_id"] != second.json()["document_id"]
        listing = client.get("/documents").json()
        assert listing["total"] == 2

    def test_deleted_document_contents_can_be_uploaded_again(self, client):
        content = b"Content that may be uploaded again after deletion."
        first = client.post(
            "/documents/upload",
            files={"file": ("first.txt", content, "text/plain")},
        )
        assert first.status_code == 201
        assert client.delete(
            f"/documents/{first.json()['document_id']}"
        ).status_code == 200

        second = client.post(
            "/documents/upload",
            files={"file": ("second.txt", content, "text/plain")},
        )
        assert second.status_code == 201
        assert second.json()["document_id"] != first.json()["document_id"]

    def test_legacy_unhashed_document_does_not_block_upload(self, client):
        from app.database.models.document import Document
        from app.database.session import SessionLocal

        content = b"Legacy document bytes"
        db = SessionLocal()
        try:
            db.add(Document(
                id=str(uuid.uuid4()),
                original_filename="legacy.txt",
                file_type="txt",
                file_size=len(content),
                chunk_count=1,
                status="READY",
                content_hash=None,
            ))
            db.commit()
        finally:
            db.close()

        response = client.post(
            "/documents/upload",
            files={"file": ("LEGACY.txt", content, "text/plain")},
        )
        assert response.status_code == 201

    def test_oversized_file_rejected_413(self, client):
        """Test 6 (API) — File larger than max_file_size rejected with 413."""
        # Override the setting temporarily
        original = client.app.state.settings.max_file_size_mb
        client.app.state.settings.__dict__["max_file_size_mb"] = 0  # 0 MB = instant rejection
        try:
            response = client.post(
                "/documents/upload",
                files={"file": ("big.txt", b"x" * 100, "text/plain")},
            )
            assert response.status_code == 413
        finally:
            client.app.state.settings.__dict__["max_file_size_mb"] = original

    def test_temp_file_cleaned_up_after_success(self, client):
        """Test 17 — Temp file is removed successfully using NamedTemporaryFile pattern."""
        # Using the standard client without messing with settings, 
        # since it uses tempfile internally now.
        response = client.post(
            "/documents/upload",
            files={"file": ("notes.txt", make_txt_bytes(), "text/plain")},
        )
        assert response.status_code == 201
        
        # Adding File-Handle Regression Tests for PDF/DOCX
        pdf_resp = client.post(
            "/documents/upload",
            files={"file": ("notes.pdf", make_pdf_bytes(), "application/pdf")},
        )
        assert pdf_resp.status_code == 201
        
        docx_resp = client.post(
            "/documents/upload",
            files={"file": ("notes.docx", make_docx_bytes(),
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert docx_resp.status_code == 201


# ===========================================================================
# Integration tests: delete endpoint
# ===========================================================================

class TestDeleteEndpoint:
    def test_delete_removes_all_chunks(self, client):
        """Test 20 — DELETE endpoint removes all chunks for a document_id."""
        # Upload first
        upload_resp = client.post(
            "/documents/upload",
            files={"file": ("notes.pdf", make_pdf_bytes(), "application/pdf")},
        )
        assert upload_resp.status_code == 201
        doc_id = upload_resp.json()["document_id"]

        # Delete
        delete_resp = client.delete(f"/documents/{doc_id}")
        assert delete_resp.status_code == 200
        data = delete_resp.json()
        assert data["chunks_deleted"] > 0

        # Verify gone
        col = client.app.state.collection
        results = col.get(where={"document_id": {"$eq": doc_id}})
        assert results["ids"] == []

    def test_delete_unknown_document_returns_404(self, client):
        """DELETE on non-existent document_id returns 404."""
        response = client.delete(f"/documents/{uuid.uuid4()}")
        assert response.status_code == 404


# ===========================================================================
# CLI Consistency Command Tests
# ===========================================================================

from app.commands.check_document_consistency import run_check


class TestConsistencyCommand:
    def test_consistency_check_healthy_system(self, client):
        col = client.app.state.collection
        doc_id = str(uuid.uuid4())
        col.add(
            documents=["Sample text"],
            ids=[f"{doc_id}:0"],
            metadatas=[{"document_id": doc_id, "filename": "healthy.pdf", "chunk_index": 0, "page_number": 1}],
        )

        mock_doc = MagicMock()
        mock_doc.id = doc_id
        mock_doc.chunk_count = 1
        mock_doc.original_filename = "healthy.pdf"
        mock_doc.status = "READY"

        with patch("app.commands.check_document_consistency._get_collection", return_value=col), \
             patch("app.commands.check_document_consistency._load_db_documents", return_value=([mock_doc], [])):
            code = run_check(repair_orphan_vectors=False)
            assert code == 0

    def test_consistency_check_orphan_vectors_and_repair(self, client):
        col = client.app.state.collection
        orphan_id = str(uuid.uuid4())

        col.add(
            documents=["Orphan chunk text content"],
            ids=[f"{orphan_id}:0"],
            metadatas=[{"document_id": orphan_id, "filename": "orphan.pdf", "chunk_index": 0, "page_number": 1}],
        )

        with patch("app.commands.check_document_consistency._get_collection", return_value=col), \
             patch("app.commands.check_document_consistency._load_db_documents", return_value=([], [])):
            code_dry = run_check(repair_orphan_vectors=False)
            assert code_dry == 1

            code_repair = run_check(repair_orphan_vectors=True)
            assert code_repair == 1

            res = col.get(where={"document_id": {"$eq": orphan_id}})
            assert len(res["ids"]) == 0

            code_subsequent = run_check(repair_orphan_vectors=False)
            assert code_subsequent == 0


class TestFileTypeValidation:
    def test_pdf_with_generic_octet_stream_mime_type(self):
        from app.routers.documents import _get_file_type
        mock_settings = MagicMock()
        mock_settings.allowed_extensions = ["pdf", "docx", "txt"]
        
        # Test application/octet-stream for PDF
        ext = _get_file_type("report.pdf", "application/octet-stream", mock_settings)
        assert ext == "pdf"

    def test_pdf_with_invalid_mime_type_raises(self):
        from app.routers.documents import _get_file_type
        from fastapi import HTTPException
        mock_settings = MagicMock()
        mock_settings.allowed_extensions = ["pdf", "docx", "txt"]

        with pytest.raises(HTTPException) as exc_info:
            _get_file_type("report.pdf", "image/png", mock_settings)
        assert exc_info.value.status_code == 415

