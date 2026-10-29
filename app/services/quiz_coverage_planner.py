"""
app/services/quiz_coverage_planner.py — Wave 3 Multi-Document Quiz Coverage Planner
Plans balanced or focused question distribution across scoped documents, enforces anti-domination
guards, and deterministically partitions question types and mixed difficulties.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ScopedDocumentInfo:
    document_id: str
    filename: str
    chunk_count: int = 1
    is_focused: bool = False


@dataclass
class QuizCoveragePlan:
    allocations_per_document: Dict[str, int]
    type_allocations: Dict[str, int]
    difficulty_allocations: Optional[Dict[str, int]]
    total_questions: int
    coverage_mode: str
    document_names: Dict[str, str] = field(default_factory=dict)


class QuizCoveragePlanner:
    """
    Deterministic coverage planning layer for multi-document assessment generation.
    """

    @staticmethod
    def allocate_question_types(question_types: List[str], total: int) -> Dict[str, int]:
        """
        Deterministically partition total questions across question types.
        The difference between any two type counts is at most 1.
        """
        if not question_types or total <= 0:
            return {}
        n = len(question_types)
        base, remainder = divmod(total, n)
        return {
            qtype: base + (1 if i < remainder else 0)
            for i, qtype in enumerate(question_types)
        }

    @staticmethod
    def allocate_difficulties(difficulty: str, total: int) -> Optional[Dict[str, int]]:
        """
        Deterministically partition total questions across easy/medium/hard for 'mixed' difficulty.
        """
        if difficulty != "mixed" or total <= 0:
            return None
        levels = ["easy", "medium", "hard"]
        base, remainder = divmod(total, 3)
        return {
            lvl: base + (1 if i < remainder else 0)
            for i, lvl in enumerate(levels)
        }

    @classmethod
    def plan_coverage(
        cls,
        documents: List[ScopedDocumentInfo],
        total_questions: int,
        *,
        coverage_mode: str = "balanced",
        question_types: Optional[List[str]] = None,
        difficulty: str = "medium",
        max_share_per_doc: float = 0.50,
    ) -> QuizCoveragePlan:
        """
        Main coverage planning method.
        
        Rules:
        1. If 1 document, allocate 100% of questions to it.
        2. In 'balanced' mode:
           - Give each viable document a base opportunity.
           - Allocate remaining questions proportionally by usable content.
           - Apply soft anti-domination cap (default 50% max share per document if multiple viable docs exist).
        3. In 'focused' mode:
           - Focus target documents receive higher weighting (~70%), remaining shared among others.
        4. Sum of per-document allocations strictly equals total_questions.
        """
        types = question_types or ["multiple_choice"]
        type_allocs = cls.allocate_question_types(types, total_questions)
        diff_allocs = cls.allocate_difficulties(difficulty, total_questions)

        if not documents or total_questions <= 0:
            return QuizCoveragePlan(
                allocations_per_document={},
                type_allocations=type_allocs,
                difficulty_allocations=diff_allocs,
                total_questions=0,
                coverage_mode=coverage_mode,
            )

        doc_names = {d.document_id: d.filename for d in documents}

        # Single document fast-path
        if len(documents) == 1:
            return QuizCoveragePlan(
                allocations_per_document={documents[0].document_id: total_questions},
                type_allocations=type_allocs,
                difficulty_allocations=diff_allocs,
                total_questions=total_questions,
                coverage_mode=coverage_mode,
                document_names=doc_names,
            )

        # Multi-document allocation
        allocations: Dict[str, int] = {d.document_id: 0 for d in documents}

        if coverage_mode == "focused":
            focused_docs = [d for d in documents if d.is_focused]
            non_focused_docs = [d for d in documents if not d.is_focused]

            if focused_docs:
                focus_target_q = max(1, int(round(total_questions * 0.70)))
                other_target_q = total_questions - focus_target_q

                # Allocate focus target
                base_f, rem_f = divmod(focus_target_q, len(focused_docs))
                for i, d in enumerate(focused_docs):
                    allocations[d.document_id] = base_f + (1 if i < rem_f else 0)

                # Allocate other target
                if non_focused_docs and other_target_q > 0:
                    base_o, rem_o = divmod(other_target_q, len(non_focused_docs))
                    for i, d in enumerate(non_focused_docs):
                        allocations[d.document_id] = base_o + (1 if i < rem_o else 0)
                else:
                    # Give remainder to first focused doc
                    allocations[focused_docs[0].document_id] += other_target_q
            else:
                # Fallback to balanced if no specific doc was flagged as focused
                coverage_mode = "balanced"

        if coverage_mode == "balanced":
            n_docs = len(documents)
            # Soft domination cap per document (e.g. 50% of total questions)
            max_per_doc = max(2, int(round(total_questions * max_share_per_doc)))

            # Step 1: Base allocation (1 question to each doc if total_questions >= n_docs)
            if total_questions >= n_docs:
                for d in documents:
                    allocations[d.document_id] = 1
                remaining_q = total_questions - n_docs
            else:
                # More documents than questions: select top documents by richness
                sorted_docs = sorted(documents, key=lambda x: -x.chunk_count)
                for i in range(total_questions):
                    allocations[sorted_docs[i].document_id] = 1
                remaining_q = 0

            # Step 2: Distribute remaining questions proportionally with anti-domination cap
            if remaining_q > 0:
                # Calculate weights based on chunk count (with a log/sqrt dampen to prevent extreme domination)
                weights = {d.document_id: max(1.0, float(d.chunk_count) ** 0.6) for d in documents}
                total_weight = sum(weights.values())

                doc_order = sorted(documents, key=lambda x: (-x.chunk_count, x.document_id))
                for d in doc_order:
                    if remaining_q <= 0:
                        break
                    prop = (weights[d.document_id] / total_weight) * (total_questions - n_docs)
                    add_q = int(round(prop))
                    # Enforce anti-domination cap
                    current = allocations[d.document_id]
                    allowed_add = min(remaining_q, max(0, max_per_doc - current), add_q)
                    allocations[d.document_id] += allowed_add
                    remaining_q -= allowed_add

                # Fill any remaining questions in round-robin across documents below cap
                idx = 0
                while remaining_q > 0:
                    d = doc_order[idx % len(doc_order)]
                    if allocations[d.document_id] < max_per_doc or len(doc_order) == 1:
                        allocations[d.document_id] += 1
                        remaining_q -= 1
                    idx += 1
                    if idx > len(doc_order) * 2:  # safety break to prevent loop
                        allocations[doc_order[0].document_id] += remaining_q
                        break

        # Sanity check: Ensure total sum matches total_questions
        allocated_total = sum(allocations.values())
        if allocated_total != total_questions and allocations:
            diff = total_questions - allocated_total
            first_key = next(iter(allocations))
            allocations[first_key] += diff

        return QuizCoveragePlan(
            allocations_per_document=allocations,
            type_allocations=type_allocs,
            difficulty_allocations=diff_allocs,
            total_questions=total_questions,
            coverage_mode=coverage_mode,
            document_names=doc_names,
        )
