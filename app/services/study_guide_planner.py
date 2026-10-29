"""
app/services/study_guide_planner.py — Wave 5 Deterministic Study Guide Planner

Responsibilities:
  - Resolves guide modes: 'comprehensive', 'condensed', 'exam_review'
  - Selects and orders viable sections
  - Defines section-specific retrieval intents (queries)
  - Assigns target item counts, context budgets, and required/optional flags
  - Applies anti-domination multi-document source share allocation
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from app.schemas.ai_scope import AIScopeDocumentSummary


SECTION_TITLES: dict[str, str] = {
    "overview": "Overview & Core Themes",
    "key_concepts": "Key Concepts & Principles",
    "definitions": "Important Definitions & Terminology",
    "processes": "Core Processes & Workflows",
    "comparisons": "Comparisons & Relationships",
    "examples": "Illustrative Examples",
    "misconceptions": "Common Mistakes & Misconceptions",
    "review_points": "High-Value Review Points",
    "practice_questions": "Practice Questions",
}

# Targeted semantic search queries for each section type
SECTION_RETRIEVAL_QUERIES: dict[str, str] = {
    "overview": "overview introduction summary main themes scope fundamental purpose",
    "key_concepts": "key concepts core principles fundamental theory main ideas architecture foundations",
    "definitions": "definitions terminology vocabulary key terms meaning formal definition",
    "processes": "steps workflows procedures algorithms sequence lifecycle execution stages methods",
    "comparisons": "differences similarities comparisons tradeoffs versus distinctions relationships advantages disadvantages",
    "examples": "examples case studies applications implementations scenarios code walk-through",
    "misconceptions": "common mistakes errors misconceptions pitfalls traps exceptions confusing distinctions edge cases",
    "review_points": "high value review points important takeaways critical rules exam summary key facts",
    "practice_questions": "review questions conceptual problems exercise questions self assessment test",
}

# Mode default section blueprints (in logical study order)
MODE_DEFAULT_SECTIONS: dict[str, list[str]] = {
    "comprehensive": [
        "overview",
        "key_concepts",
        "definitions",
        "processes",
        "comparisons",
        "examples",
        "misconceptions",
        "review_points",
        "practice_questions",
    ],
    "condensed": [
        "overview",
        "key_concepts",
        "definitions",
        "review_points",
    ],
    "exam_review": [
        "definitions",
        "processes",
        "comparisons",
        "misconceptions",
        "review_points",
        "practice_questions",
    ],
}

CORE_REQUIRED_SECTIONS: set[str] = {
    "overview",
    "key_concepts",
    "definitions",
    "review_points",
}


@dataclass
class SectionPlan:
    section_type: str
    title: str
    retrieval_query: str
    target_item_count: int
    max_context_tokens: int
    is_required: bool
    description: str = ""


@dataclass
class StudyGuidePlan:
    guide_mode: str
    title: str
    sections: List[SectionPlan]
    document_ids: List[str]
    document_titles: Dict[str, str]
    total_estimated_context_tokens: int
    is_multi_document: bool
    topic_query: Optional[str] = None


class StudyGuidePlanner:
    """
    Deterministic planning layer for study guides.
    Decoupled from LLM execution.
    """

    @classmethod
    def plan_guide(
        cls,
        documents: List[AIScopeDocumentSummary],
        *,
        guide_mode: str = "comprehensive",
        requested_sections: Optional[List[str]] = None,
        topic_query: Optional[str] = None,
        custom_title: Optional[str] = None,
    ) -> StudyGuidePlan:
        """
        Creates a deterministic StudyGuidePlan based on scope and mode.
        """
        mode = guide_mode.lower().strip() if guide_mode else "comprehensive"
        if mode not in MODE_DEFAULT_SECTIONS:
            mode = "comprehensive"

        doc_ids = [d.document_id for d in documents]
        doc_map = {d.document_id: d.filename for d in documents}
        is_multi = len(doc_ids) > 1

        # 1. Determine active sections
        if requested_sections:
            valid_requested = [s.lower().strip() for s in requested_sections if s.lower().strip() in SECTION_TITLES]
            # Preserve logical order from comprehensive template
            ordered_sections = [s for s in MODE_DEFAULT_SECTIONS["comprehensive"] if s in valid_requested]
            if not ordered_sections:
                ordered_sections = list(MODE_DEFAULT_SECTIONS[mode])
        else:
            ordered_sections = list(MODE_DEFAULT_SECTIONS[mode])

        # 2. Build Section Plans
        section_plans: List[SectionPlan] = []
        total_tokens = 0

        for section_type in ordered_sections:
            base_query = SECTION_RETRIEVAL_QUERIES.get(section_type, section_type)
            if topic_query and topic_query.strip():
                retrieval_query = f"{topic_query.strip()} {base_query}"
            else:
                retrieval_query = base_query

            # Item counts and token budgets by mode and section type
            if mode == "condensed":
                target_items = 4 if section_type in ("key_concepts", "definitions", "review_points") else 3
                context_tokens = 2000
            elif mode == "exam_review":
                target_items = 6 if section_type in ("definitions", "review_points", "practice_questions") else 4
                context_tokens = 3000
            else:  # comprehensive
                target_items = 7 if section_type in ("key_concepts", "definitions") else 5
                context_tokens = 3000

            is_required = (section_type in CORE_REQUIRED_SECTIONS) if mode != "exam_review" else (section_type in ("definitions", "review_points"))

            plan = SectionPlan(
                section_type=section_type,
                title=SECTION_TITLES.get(section_type, section_type.replace("_", " ").title()),
                retrieval_query=retrieval_query,
                target_item_count=target_items,
                max_context_tokens=context_tokens,
                is_required=is_required,
                description=f"Synthesize {section_type.replace('_', ' ')} from authorized scope.",
            )
            section_plans.append(plan)
            total_tokens += context_tokens

        # 3. Determine Guide Title
        if custom_title and custom_title.strip():
            title = custom_title.strip()
        elif topic_query and topic_query.strip():
            topic_str = topic_query.strip().title()
            if mode == "exam_review":
                title = f"{topic_str} — Exam Reviewer"
            elif mode == "condensed":
                title = f"{topic_str} — Quick Study Guide"
            else:
                title = f"{topic_str} — Comprehensive Study Guide"
        elif len(documents) == 1:
            base_name = documents[0].filename.rsplit(".", 1)[0]
            if mode == "exam_review":
                title = f"{base_name} — Exam Reviewer"
            elif mode == "condensed":
                title = f"{base_name} — Quick Study Guide"
            else:
                title = f"{base_name} — Study Guide"
        else:
            if mode == "exam_review":
                title = f"Course Exam Reviewer ({len(documents)} Documents)"
            elif mode == "condensed":
                title = f"Condensed Reviewer ({len(documents)} Documents)"
            else:
                title = f"Comprehensive Study Guide ({len(documents)} Documents)"

        return StudyGuidePlan(
            guide_mode=mode,
            title=title,
            sections=section_plans,
            document_ids=doc_ids,
            document_titles=doc_map,
            total_estimated_context_tokens=total_tokens,
            is_multi_document=is_multi,
            topic_query=topic_query,
        )
