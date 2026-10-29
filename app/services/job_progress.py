"""
services/job_progress.py — Phase 9 Deterministic Job Progress and Stage Definitions
"""
from __future__ import annotations

from typing import Dict, Tuple

# Student-friendly stage labels per job type (§7)
JOB_STAGES: Dict[str, Dict[str, str]] = {
    "document_ingestion": {
        "validating": "Validating document",
        "extracting_native_text": "Extracting text",
        "detecting_scanned_pages": "Analyzing page layout",
        "running_ocr": "Running text recognition (OCR)",
        "normalizing_text": "Normalizing content",
        "detecting_sections": "Detecting sections",
        "chunking": "Segmenting document",
        "embedding": "Generating vector embeddings",
        "indexing": "Indexing in vector store",
        "finalizing": "Finalizing document",
    },
    "document_reprocessing": {
        "validating": "Validating reprocess request",
        "extracting_native_text": "Extracting text",
        "detecting_scanned_pages": "Analyzing page layout",
        "running_ocr": "Running text recognition (OCR)",
        "normalizing_text": "Normalizing content",
        "detecting_sections": "Detecting sections",
        "chunking": "Segmenting document",
        "embedding": "Generating vector embeddings",
        "indexing": "Replacing vector index",
        "finalizing": "Finalizing document",
    },
    "quiz_generation": {
        "selecting_context": "Selecting relevant study material",
        "building_prompt": "Preparing generation request",
        "generating_questions": "Generating quiz questions",
        "validating_questions": "Validating quality and sources",
        "saving_quiz": "Saving quiz",
        "finalizing": "Quiz ready",
    },
    "flashcard_generation": {
        "selecting_context": "Selecting relevant study material",
        "building_prompt": "Preparing generation request",
        "generating_cards": "Generating flashcards",
        "validating_cards": "Validating flashcards",
        "saving_study_set": "Saving study set",
        "finalizing": "Study set ready",
    },
    "chat_generation": {
        "retrieving_context": "Retrieving context from documents",
        "building_prompt": "Building prompt",
        "generating_answer": "Generating response",
        "validating_citations": "Validating citations",
        "saving_message": "Saving message",
        "finalizing": "Response ready",
    },
}

# Base percentage per stage (§8)
STAGE_PROGRESS_MAP: Dict[str, Dict[str, int]] = {
    "document_ingestion": {
        "validating": 5,
        "extracting_native_text": 15,
        "detecting_scanned_pages": 30,
        "running_ocr": 45,
        "normalizing_text": 60,
        "detecting_sections": 65,
        "chunking": 70,
        "embedding": 80,
        "indexing": 95,
        "finalizing": 100,
    },
    "document_reprocessing": {
        "validating": 5,
        "extracting_native_text": 15,
        "detecting_scanned_pages": 30,
        "running_ocr": 45,
        "normalizing_text": 60,
        "detecting_sections": 65,
        "chunking": 70,
        "embedding": 80,
        "indexing": 95,
        "finalizing": 100,
    },
    "quiz_generation": {
        "selecting_context": 15,
        "building_prompt": 30,
        "generating_questions": 60,
        "validating_questions": 85,
        "saving_quiz": 95,
        "finalizing": 100,
    },
    "flashcard_generation": {
        "selecting_context": 15,
        "building_prompt": 30,
        "generating_cards": 60,
        "validating_cards": 85,
        "saving_study_set": 95,
        "finalizing": 100,
    },
}


def get_stage_label(job_type: str, stage_key: str) -> str:
    """Return student-friendly display label for a stage."""
    stages = JOB_STAGES.get(job_type, {})
    return stages.get(stage_key, stage_key.replace("_", " ").title())


def calculate_progress(
    job_type: str,
    stage_key: str,
    completed_subunits: int = 0,
    total_subunits: int = 0,
) -> int:
    """
    Calculate progress percentage (0-100).
    For OCR: completed OCR pages / total OCR pages scales between stage bounds.
    """
    stage_map = STAGE_PROGRESS_MAP.get(job_type, {})
    base = stage_map.get(stage_key, 0)
    
    if stage_key == "running_ocr" and total_subunits > 0:
        # OCR runs from 30% to 55%
        ocr_start = 30
        ocr_span = 25
        progress_within_ocr = min(1.0, max(0.0, completed_subunits / total_subunits))
        return int(ocr_start + (progress_within_ocr * ocr_span))
    
    return min(100, max(0, base))
