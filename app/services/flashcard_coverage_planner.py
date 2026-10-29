"""app/services/flashcard_coverage_planner.py — Wave 4 Multi-Document Flashcard Coverage Planner

Plans balanced and focus-mode card allocations across scoped documents, enforces anti-domination
guards (<= 50% max share per doc), and deterministically partitions card types and difficulties.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.quiz_coverage_planner import ScopedDocumentInfo


@dataclass
class FlashcardCoveragePlan:
    allocations_per_document: Dict[str, int]
    type_allocations: Dict[str, int]
    difficulty_allocations: Optional[Dict[str, int]]
    total_cards: int
    focus_mode: str
    document_names: Dict[str, str] = field(default_factory=dict)


class FlashcardCoveragePlanner:
    """
    Deterministic coverage planning layer for multi-document flashcard generation.
    """

    @staticmethod
    def allocate_card_types(
        card_types: List[str],
        total: int,
        focus_mode: str = "balanced",
    ) -> Dict[str, int]:
        """
        Deterministically partition total cards across card types based on focus mode.
        """
        if not card_types or total <= 0:
            return {}

        types = list(dict.fromkeys(card_types))  # deduplicate preserving order
        if len(types) == 1:
            return {types[0]: total}

        if focus_mode == "definitions" and "definition" in types:
            # Prioritize definition type (~70%), remaining distributed
            def_target = max(1, math.ceil(total * 0.70))
            def_target = min(def_target, total - (len(types) - 1))
            allocs: Dict[str, int] = {"definition": def_target}
            remaining = total - def_target
            other_types = [t for t in types if t != "definition"]
            base, rem = divmod(remaining, len(other_types))
            for i, t in enumerate(other_types):
                allocs[t] = base + (1 if i < rem else 0)
            return allocs

        if focus_mode == "key_concepts" and "concept" in types:
            concept_target = max(1, math.ceil(total * 0.60))
            concept_target = min(concept_target, total - (len(types) - 1))
            allocs = {"concept": concept_target}
            remaining = total - concept_target
            other_types = [t for t in types if t != "concept"]
            base, rem = divmod(remaining, len(other_types))
            for i, t in enumerate(other_types):
                allocs[t] = base + (1 if i < rem else 0)
            return allocs

        # Balanced / Exam Review / Default: fair divmod partition
        n = len(types)
        base, remainder = divmod(total, n)
        return {
            ctype: base + (1 if i < remainder else 0)
            for i, ctype in enumerate(types)
        }

    @staticmethod
    def allocate_difficulties(difficulty: str, total: int) -> Optional[Dict[str, int]]:
        """
        Deterministically partition total cards across easy/medium/hard for 'mixed' difficulty.
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
        total_cards: int,
        *,
        focus_mode: str = "balanced",
        card_types: Optional[List[str]] = None,
        difficulty: str = "medium",
        max_share_per_doc: float = 0.50,
    ) -> FlashcardCoveragePlan:
        """
        Main coverage planning method for flashcard decks.

        Rules:
        1. If 1 document, allocate 100% of cards to it.
        2. Filter viable documents (chunk_count > 0). If all are 0, treat all as viable.
        3. Enforce anti-domination cap (<= 50% max share per document when >= 2 viable docs exist).
        4. In focus modes with topic weighting:
           - Focused target documents get ~70% share, remaining split across others.
        5. Weak source protection: Tiny/empty documents do not get forced high quotas.
        6. Sum of per-document allocations strictly equals total_cards.
        """
        types = card_types or ["concept", "definition"]
        type_allocs = cls.allocate_card_types(types, total_cards, focus_mode=focus_mode)
        diff_allocs = cls.allocate_difficulties(difficulty, total_cards)

        if not documents or total_cards <= 0:
            return FlashcardCoveragePlan(
                allocations_per_document={},
                type_allocations=type_allocs,
                difficulty_allocations=diff_allocs,
                total_cards=0,
                focus_mode=focus_mode,
            )

        doc_names = {d.document_id: d.filename for d in documents}

        # Single document fast-path
        if len(documents) == 1:
            return FlashcardCoveragePlan(
                allocations_per_document={documents[0].document_id: total_cards},
                type_allocations=type_allocs,
                difficulty_allocations=diff_allocs,
                total_cards=total_cards,
                focus_mode=focus_mode,
                document_names=doc_names,
            )

        # Multi-document allocation
        viable_docs = [d for d in documents if d.chunk_count > 0]
        if not viable_docs:
            viable_docs = documents

        allocations: Dict[str, int] = {d.document_id: 0 for d in documents}

        focused_docs = [d for d in viable_docs if d.is_focused]
        non_focused_docs = [d for d in viable_docs if not d.is_focused]

        if focused_docs and non_focused_docs:
            # Weighted focus mode
            target_focus_cards = max(1, math.ceil(total_cards * 0.70))
            target_other_cards = total_cards - target_focus_cards

            cls._distribute_balanced(
                focused_docs, target_focus_cards, allocations, max_share_per_doc=1.0
            )
            cls._distribute_balanced(
                non_focused_docs, target_other_cards, allocations, max_share_per_doc=1.0
            )
        else:
            cls._distribute_balanced(
                viable_docs, total_cards, allocations, max_share_per_doc=max_share_per_doc
            )

        # Sanity check sum
        allocated_sum = sum(allocations.values())
        if allocated_sum != total_cards and viable_docs:
            diff = total_cards - allocated_sum
            # Adjust on most-endowed viable document
            sorted_docs = sorted(viable_docs, key=lambda d: (allocations[d.document_id], d.chunk_count), reverse=True)
            for d in sorted_docs:
                if diff == 0:
                    break
                if diff > 0:
                    allocations[d.document_id] += 1
                    diff -= 1
                elif diff < 0 and allocations[d.document_id] > 0:
                    allocations[d.document_id] -= 1
                    diff += 1

        return FlashcardCoveragePlan(
            allocations_per_document=allocations,
            type_allocations=type_allocs,
            difficulty_allocations=diff_allocs,
            total_cards=total_cards,
            focus_mode=focus_mode,
            document_names=doc_names,
        )

    @classmethod
    def _distribute_balanced(
        cls,
        docs: List[ScopedDocumentInfo],
        total: int,
        allocations: Dict[str, int],
        max_share_per_doc: float = 0.50,
    ) -> None:
        if not docs or total <= 0:
            return

        n = len(docs)
        if n == 1:
            allocations[docs[0].document_id] += total
            return

        # Calculate cap per document
        max_cap = max(1, math.ceil(total * max_share_per_doc))

        # Step 1: Base allocation (1 card per doc if total >= n)
        remaining = total
        doc_allocs: Dict[str, int] = {d.document_id: 0 for d in docs}

        if total >= n:
            for d in docs:
                doc_allocs[d.document_id] = 1
            remaining -= n
        else:
            # Fewer cards than docs: give 1 card to highest chunk count docs
            sorted_by_size = sorted(docs, key=lambda d: (d.chunk_count, d.document_id), reverse=True)
            for i in range(total):
                doc_allocs[sorted_by_size[i].document_id] = 1
            remaining = 0

        # Step 2: Distribute remaining cards proportionally to chunk_count respecting cap
        total_chunks = sum(d.chunk_count for d in docs) or n
        if remaining > 0:
            for d in sorted(docs, key=lambda x: (x.chunk_count, x.document_id), reverse=True):
                if remaining <= 0:
                    break
                headroom = max_cap - doc_allocs[d.document_id]
                if headroom <= 0:
                    continue

                proportional = int(math.floor(remaining * (d.chunk_count / total_chunks)))
                grant = min(remaining, headroom, max(1 if remaining > 0 and proportional == 0 else 0, proportional))
                doc_allocs[d.document_id] += grant
                remaining -= grant

        # Step 3: Distribute any leftover remaining cards
        if remaining > 0:
            for d in sorted(docs, key=lambda x: (doc_allocs[x.document_id], x.chunk_count), reverse=False):
                if remaining <= 0:
                    break
                headroom = max_cap - doc_allocs[d.document_id]
                if headroom > 0:
                    grant = min(remaining, headroom)
                    doc_allocs[d.document_id] += grant
                    remaining -= grant

        # If strict cap prevented full allocation, relax cap on largest doc
        if remaining > 0:
            for d in sorted(docs, key=lambda x: x.chunk_count, reverse=True):
                if remaining <= 0:
                    break
                doc_allocs[d.document_id] += 1
                remaining -= 1

        for d in docs:
            allocations[d.document_id] += doc_allocs[d.document_id]
