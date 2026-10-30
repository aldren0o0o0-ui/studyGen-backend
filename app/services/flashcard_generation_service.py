"""app/services/flashcard_generation_service.py — Phase 5 / Wave 4
Generates grounded flashcards from single-document, multi-document, or folder context using
AIScopeResolver, RetrievalService, FlashcardCoveragePlanner, and Gemini AI.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from uuid import uuid4
from typing import Any, List, Optional, Set

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.database.models.document import Document
from app.database.models.flashcard import StudySet, StudySetSourceDocument, Flashcard, FlashcardReviewState
from app.schemas.ai_scope import AIScopeType
from app.schemas.flashcards import GenerateFlashcardsRequest
from app.services import ai_scope_service
from app.services.context_selection import select_context_chunks, SelectedChunk
from app.services.flashcard_coverage_planner import FlashcardCoveragePlanner, ScopedDocumentInfo
from app.services.flashcard_validation_service import filter_and_deduplicate_cards, _normalize_text
from app.services.prompt_templates import build_flashcard_prompt, FLASHCARD_PROMPT_VERSION
from app.services.question_validation import SourceReference
from app.services.retrieval_service import retrieve_context
from config import settings

logger = logging.getLogger(__name__)


def _clean_json_markdown(raw_text: str) -> str:
    """Strip ```json ... ``` code fences from Gemini output."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    return cleaned.strip()


def generate_flashcards_from_document(
    db: Session,
    collection: Any,
    gemini_model: Any,
    req: GenerateFlashcardsRequest,
    user_id: Optional[str] = None,
) -> tuple[StudySet, List[Flashcard], int]:
    """
    Unified flashcard generation endpoint supporting DOCUMENT, SELECTED_DOCUMENTS, and FOLDER scopes.
    """
    effective_user_id = user_id or "legacy_owner"

    from app.schemas.ai_scope import AIScopeRequest, AIScopeType
    from app.services.ai_scope_service import resolve_ai_scope, AIScopeError

    if req.scope_type:
        st = req.scope_type.lower()
        if st in ("folder", AIScopeType.FOLDER.value):
            scope_type = AIScopeType.FOLDER
        elif st in ("selected_documents", AIScopeType.SELECTED_DOCUMENTS.value):
            scope_type = AIScopeType.SELECTED_DOCUMENTS
        else:
            scope_type = AIScopeType.DOCUMENT
    elif req.folder_id:
        scope_type = AIScopeType.FOLDER
    elif req.document_ids and len(req.document_ids) > 1:
        scope_type = AIScopeType.SELECTED_DOCUMENTS
    else:
        scope_type = AIScopeType.DOCUMENT

    doc_id = req.document_id or (req.document_ids[0] if req.document_ids and len(req.document_ids) == 1 else None)
    doc_ids = req.document_ids or ([req.document_id] if req.document_id else None)

    scope_req = AIScopeRequest(
        scope_type=scope_type,
        document_id=doc_id if scope_type == AIScopeType.DOCUMENT else None,
        document_ids=doc_ids if scope_type == AIScopeType.SELECTED_DOCUMENTS else None,
        folder_id=req.folder_id if scope_type == AIScopeType.FOLDER else None,
        include_subfolders=req.include_subfolders if scope_type == AIScopeType.FOLDER else False,
    )

    # 1. Resolve Canonical Scope
    try:
        resolved = resolve_ai_scope(db, user_id=effective_user_id, request=scope_req)
    except AIScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    if not resolved.documents:
        raise HTTPException(422, "No ready documents found in the selected scope.")

    resolved_doc_ids = [d.document_id for d in resolved.documents]
    resolved_doc_map = {d.document_id: d.filename for d in resolved.documents}

    # 2. Inspect document chunk counts and plan coverage
    scoped_docs: List[ScopedDocumentInfo] = []
    for d in resolved.documents:
        count = 1
        if collection is not None:
            try:
                res = collection.get(where={"document_id": d.document_id}, include=["metadatas"])
                if res and res.get("ids"):
                    count = len(res["ids"])
            except Exception:
                count = 1
        is_focused = bool(
            req.topic_query and req.topic_query.strip().lower() in d.filename.lower()
        )
        scoped_docs.append(
            ScopedDocumentInfo(
                document_id=d.document_id,
                filename=d.filename,
                chunk_count=count,
                is_focused=is_focused,
            )
        )

    focus_mode = req.focus_mode or (
        req.coverage_mode
        if req.coverage_mode in ("key_concepts", "definitions", "exam_review")
        else "balanced"
    )

    plan = FlashcardCoveragePlanner.plan_coverage(
        documents=scoped_docs,
        total_cards=req.card_count,
        focus_mode=focus_mode,
        card_types=req.card_types,
        difficulty=req.difficulty,
    )

    # 3. Retrieve Context Chunks
    source_map: dict[str, SourceReference] = {}
    parts: list[str] = []

    if len(resolved_doc_ids) == 1 and req.selected_chunk_indexes:
        # User selected specific chunk indexes for single document
        context_chunks = select_context_chunks(
            collection=collection,
            document_id=resolved_doc_ids[0],
            coverage_mode="balanced",
            selected_chunk_indexes=req.selected_chunk_indexes,
            topic_query=req.topic_query,
        )
        for i, chunk in enumerate(context_chunks):
            label = f"SOURCE_{i + 1}"
            source_map[label] = SourceReference(
                chunk_index=chunk.chunk_index,
                page_number=chunk.page_number,
                filename=chunk.filename,
                excerpt=chunk.text[:200],
                document_id=resolved_doc_ids[0],
            )
            meta = f"(Chunk {chunk.chunk_index}"
            if chunk.page_number is not None and chunk.page_number > 0:
                meta += f", Page {chunk.page_number}"
            meta += f", {chunk.filename})"
            parts.append(f"[{label}] {meta}\n{chunk.text}")
    else:
        # Multi-doc or general retrieval via Wave 2 RetrievalService
        query = req.topic_query if req.topic_query else "key concepts terminology definitions main ideas principles"
        retrieved_chunks, _ = retrieve_context(
            collection=collection,
            document_ids=resolved_doc_ids,
            query=query,
        )

        for i, chunk in enumerate(retrieved_chunks):
            label = f"SOURCE_{i + 1}"
            source_map[label] = SourceReference(
                chunk_index=chunk.chunk_index,
                page_number=chunk.page_number,
                filename=chunk.filename,
                excerpt=chunk.text[:200],
                document_id=chunk.document_id,
            )
            meta = f"(Chunk {chunk.chunk_index}"
            if chunk.page_number is not None and chunk.page_number > 0:
                meta += f", Page {chunk.page_number}"
            meta += f", {chunk.filename})"
            parts.append(f"[{label}] {meta}\n{chunk.text}")

    if not parts:
        raise HTTPException(422, "No matching document context found for generation.")

    labeled_context = "\n\n---\n\n".join(parts)

    # 4. Generate Flashcards with LLM (with Deficit Retry support)
    valid_cards: list[dict[str, Any]] = []
    skipped_count = 0
    study_set_title: Optional[str] = req.title

    needed = req.card_count
    max_attempts = 2
    attempt = 0

    while needed > 0 and attempt < max_attempts:
        attempt += 1
        prompt = build_flashcard_prompt(
            card_count=needed,
            difficulty=req.difficulty,
            card_types=req.card_types,
            labeled_context=labeled_context,
            include_hints=req.include_hints,
            include_sources=req.include_sources,
            focus_mode=focus_mode,
        )

        model_name = settings.gemini_model
        try:
            response = gemini_model.generate_content(prompt)
            raw_text = response.text
        except Exception as exc:
            logger.error("Gemini API call failed for flashcard generation: %s", exc)
            if not valid_cards:
                raise HTTPException(503, f"AI generation service unavailable: {exc}")
            break

        try:
            cleaned_json = _clean_json_markdown(raw_text)
            data = json.loads(cleaned_json)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse Gemini response as JSON: %s\nRaw: %s", exc, raw_text)
            if not valid_cards:
                raise HTTPException(502, f"AI returned malformed output: {exc}")
            break

        if not study_set_title and data.get("study_set_title"):
            study_set_title = data.get("study_set_title")

        cards_raw = data.get("cards", [])
        if isinstance(cards_raw, list) and cards_raw:
            existing_fronts = {c.get("front") or c.get("front_text") or "" for c in valid_cards}
            new_valid, inv_cnt, dup_cnt = filter_and_deduplicate_cards(
                cards_raw,
                existing_fronts=existing_fronts,
                source_map=source_map,
                resolved_doc_ids=set(resolved_doc_ids),
            )
            skipped_count += inv_cnt + dup_cnt
            valid_cards.extend(new_valid)
            needed = req.card_count - len(valid_cards)

    if not valid_cards:
        raise HTTPException(502, "All generated flashcards failed validation or were duplicate.")

    # 5. Create StudySet
    if not study_set_title:
        if scope_type == AIScopeType.FOLDER:
            folder_name = resolved.folder_name or "Folder"
            study_set_title = f"{folder_name} Flashcards"
        elif scope_type == AIScopeType.SELECTED_DOCUMENTS:
            study_set_title = f"Selected Documents ({len(resolved.documents)}) Flashcards"
        else:
            primary_doc = resolved.documents[0]
            study_set_title = f"{primary_doc.filename} Flashcards"

    study_set_id = str(uuid4())
    set_type_map = {
        AIScopeType.DOCUMENT: "generated_from_document",
        AIScopeType.FOLDER: "generated_from_folder",
        AIScopeType.SELECTED_DOCUMENTS: "generated_from_selected_documents",
    }
    set_type = set_type_map.get(scope_type, "generated_from_document")

    study_set = StudySet(
        id=study_set_id,
        user_id=effective_user_id,
        title=study_set_title,
        description=f"Generated from {len(resolved.documents)} source document(s) ({len(valid_cards)} cards)",
        document_id=resolved_doc_ids[0] if len(resolved_doc_ids) == 1 else None,
        topic_label=req.topic_query if focus_mode in ("focused", "definitions", "key_concepts") else None,
        set_type=set_type,
        status="ACTIVE",
        card_count=0,
        scope_type=scope_type.value.upper(),
        folder_id=req.folder_id if scope_type == AIScopeType.FOLDER else None,
        include_subfolders=req.include_subfolders if scope_type == AIScopeType.FOLDER else False,
    )
    db.add(study_set)
    db.flush()

    # Persist StudySetSourceDocument snapshots
    for d in resolved.documents:
        src_row = StudySetSourceDocument(
            id=str(uuid4()),
            study_set_id=study_set_id,
            document_id=d.document_id,
        )
        db.add(src_row)

    # 6. Map source labels and persist Flashcards
    created_flashcards: List[Flashcard] = []

    for pos, card_dict in enumerate(valid_cards, start=1):
        front = card_dict["front"].strip()
        back = card_dict["back"].strip()
        hint = card_dict.get("hint")
        if hint and not isinstance(hint, str):
            hint = None

        card_type = card_dict.get("card_type", "concept")
        difficulty = card_dict.get("difficulty", "medium")
        topic = card_dict.get("topic")

        # Map source_labels -> trusted server excerpts
        source_labels = card_dict.get("source_labels", [])
        sources = []
        primary_doc_id = resolved_doc_ids[0] if len(resolved_doc_ids) == 1 else None

        if isinstance(source_labels, list):
            for label in source_labels:
                if isinstance(label, str) and label in source_map:
                    ref = source_map[label]
                    sources.append(asdict(ref))
                    if not primary_doc_id and getattr(ref, "document_id", None):
                        primary_doc_id = ref.document_id

        if not primary_doc_id:
            primary_doc_id = resolved_doc_ids[0]

        card_id = str(uuid4())

        flashcard = Flashcard(
            id=card_id,
            study_set_id=study_set_id,
            document_id=primary_doc_id,
            source_question_id=None,
            front_text=front,
            back_text=back,
            hint_text=hint,
            topic_label=topic,
            difficulty=difficulty,
            card_type=card_type,
            source_references_json=sources,
            generation_method="folder_ai" if scope_type == AIScopeType.FOLDER else ("selected_documents_ai" if scope_type == AIScopeType.SELECTED_DOCUMENTS else "document_ai"),
            prompt_version=FLASHCARD_PROMPT_VERSION,
            model_name=settings.gemini_model,
            position=pos,
            status="ACTIVE",
            is_user_edited=False,
            source_validation_status="valid",
        )
        db.add(flashcard)

        # Review state (SM-2 initialized)
        review_state = FlashcardReviewState(
            id=str(uuid4()),
            flashcard_id=card_id,
            review_status="NEW",
            ease_level=2.5,
            review_count=0,
            correct_count=0,
            incorrect_count=0,
            consecutive_correct=0,
            interval_days=0,
            last_reviewed_at=None,
            next_review_at=None,
            mastery_score=0.0,
        )
        db.add(review_state)
        created_flashcards.append(flashcard)

    study_set.card_count = len(created_flashcards)
    db.commit()
    db.refresh(study_set)

    return study_set, created_flashcards, skipped_count
