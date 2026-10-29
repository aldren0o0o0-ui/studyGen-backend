"""
tests/test_rag_benchmark.py — Wave 2 Multi-Document Retrieval Quality Benchmark Suite
Evaluates the 10 golden benchmark cases, measuring candidate counts, dedup rates,
source distributions, context budgeting, and anti-domination safeguards.
"""
from __future__ import annotations

import time
from typing import Any, List

import pytest

from app.database.models.auth import LEGACY_OWNER_ID
from app.services.chat_retrieval import ChatChunk
from app.services.context_packing import (
    compute_text_similarity,
    deduplicate_candidates,
    estimate_tokens,
    is_near_duplicate,
    pack_context,
    select_diversity_aware_chunks,
)
from app.services.retrieval_service import retrieve_context


class InMemoryBenchmarkCollection:
    """Deterministic Mock Chroma Collection for benchmarking retrieval and ranking."""

    def __init__(self):
        self._chunks: List[dict[str, Any]] = []

    def add(self, documents: List[str], ids: List[str], metadatas: List[dict[str, Any]]):
        for doc_id, text, meta in zip(ids, documents, metadatas):
            self._chunks.append({
                "id": doc_id,
                "document": text,
                "metadata": dict(meta),
            })

    def get(self, where: dict[str, Any] = None, include: List[str] = None) -> dict[str, Any]:
        matched_docs = []
        matched_metas = []
        matched_ids = []

        user_id = where["$and"][0]["user_id"]["$eq"]
        doc_id = where["$and"][1]["document_id"]["$eq"]

        for item in self._chunks:
            meta = item["metadata"]
            if meta.get("user_id") == user_id and meta.get("document_id") == doc_id:
                matched_ids.append(item["id"])
                matched_docs.append(item["document"])
                matched_metas.append(meta)

        return {
            "ids": matched_ids,
            "documents": matched_docs,
            "metadatas": matched_metas,
        }

    def query(self, query_texts: List[str], n_results: int, where: dict[str, Any] = None, include: List[str] = None) -> dict[str, Any]:
        query_text = query_texts[0].lower() if query_texts else ""
        user_id = where["$and"][0]["user_id"]["$eq"]
        doc_id = where["$and"][1]["document_id"]["$eq"]

        scored = []
        for item in self._chunks:
            meta = item["metadata"]
            if meta.get("user_id") != user_id or meta.get("document_id") != doc_id:
                continue

            doc_text = item["document"].lower()

            # Determine mock cosine distance based on text similarity and custom mock tags
            sim = compute_text_similarity(query_text, doc_text)
            if "HIGH_RELEVANCE" in item["document"]:
                sim = 0.95
            elif "MED_RELEVANCE" in item["document"]:
                sim = 0.60
            elif "LOW_RELEVANCE" in item["document"] or "IRRELEVANT" in item["document"]:
                sim = 0.05

            distance = max(0.0, min(2.0, (1.0 - sim) * 2.0))
            scored.append((distance, item["document"], meta, item["id"]))

        scored.sort(key=lambda x: x[0])
        top = scored[:n_results]

        return {
            "ids": [[x[3] for x in top]],
            "distances": [[x[0] for x in top]],
            "documents": [[x[1] for x in top]],
            "metadatas": [[x[2] for x in top]],
        }


# ===========================================================================
# 10 GOLDEN BENCHMARK CASES
# ===========================================================================


def test_benchmark_case_1_single_document_baseline():
    """Case 1: Single Document regression baseline."""
    col = InMemoryBenchmarkCollection()
    user_id = "user_bench_1"

    col.add(
        documents=[
            "HIGH_RELEVANCE: Operating system kernels manage memory, processes, and devices.",
            "MED_RELEVANCE: Device drivers provide abstraction interfaces for hardware.",
            "LOW_RELEVANCE: History of punch cards and early computing machines.",
        ],
        ids=["os:0", "os:1", "os:2"],
        metadatas=[
            {"document_id": "os_doc", "user_id": user_id, "filename": "OS.pdf", "chunk_index": 0, "page_number": 1},
            {"document_id": "os_doc", "user_id": user_id, "filename": "OS.pdf", "chunk_index": 1, "page_number": 2},
            {"document_id": "os_doc", "user_id": user_id, "filename": "OS.pdf", "chunk_index": 2, "page_number": 3},
        ],
    )

    chunks, metrics = retrieve_context(
        col,
        query="What does an operating system kernel do?",
        document_ids=["os_doc"],
        user_id=user_id,
    )

    assert len(chunks) >= 1
    assert chunks[0].document_id == "os_doc"
    assert "Operating system kernels manage" in chunks[0].text
    assert metrics["selected_chunk_count"] >= 1
    assert metrics["unique_sources"] == 1


def test_benchmark_case_2_two_complementary_documents():
    """Case 2: Two Equally Relevant / Complementary Documents."""
    col = InMemoryBenchmarkCollection()
    user_id = "user_bench_2"

    col.add(
        documents=["HIGH_RELEVANCE: Microservices architecture decouples domain services via APIs."],
        ids=["arch:0"],
        metadatas=[{"document_id": "arch_doc", "user_id": user_id, "filename": "Architecture.pdf", "chunk_index": 0}],
    )
    col.add(
        documents=["HIGH_RELEVANCE: Docker containers package microservices with their runtime dependencies."],
        ids=["docker:0"],
        metadatas=[{"document_id": "docker_doc", "user_id": user_id, "filename": "Docker.pdf", "chunk_index": 0}],
    )

    chunks, metrics = retrieve_context(
        col,
        query="How do Docker containers support microservices architecture?",
        document_ids=["arch_doc", "docker_doc"],
        user_id=user_id,
    )

    sources = {c.document_id for c in chunks}
    assert "arch_doc" in sources
    assert "docker_doc" in sources
    assert metrics["unique_sources"] == 2


def test_benchmark_case_3_one_relevant_nine_irrelevant():
    """Case 3: One Highly Relevant + 9 Irrelevant Documents (anti-forcing guard)."""
    col = InMemoryBenchmarkCollection()
    user_id = "user_bench_3"

    # 1 highly relevant document with multiple valuable chunks
    col.add(
        documents=[
            "HIGH_RELEVANCE: ACID Atomicity ensures all transaction operations succeed or roll back.",
            "HIGH_RELEVANCE: ACID Consistency guarantees database invariants remain valid.",
            "HIGH_RELEVANCE: ACID Isolation prevents concurrent transaction interference.",
        ],
        ids=["acid:0", "acid:1", "acid:2"],
        metadatas=[
            {"document_id": "acid_doc", "user_id": user_id, "filename": "Transactions.pdf", "chunk_index": 0},
            {"document_id": "acid_doc", "user_id": user_id, "filename": "Transactions.pdf", "chunk_index": 1},
            {"document_id": "acid_doc", "user_id": user_id, "filename": "Transactions.pdf", "chunk_index": 2},
        ],
    )

    # 9 irrelevant documents
    irrelevant_ids = []
    for i in range(1, 10):
        doc_id = f"irrelevant_{i}"
        irrelevant_ids.append(doc_id)
        col.add(
            documents=[f"IRRELEVANT: Styling buttons with CSS flexbox and margin padding {i}."],
            ids=[f"{doc_id}:0"],
            metadatas=[{"document_id": doc_id, "user_id": user_id, "filename": f"CSS_{i}.pdf", "chunk_index": 0}],
        )

    all_scoped_ids = ["acid_doc"] + irrelevant_ids

    chunks, metrics = retrieve_context(
        col,
        query="Explain ACID properties of transactions in detail",
        document_ids=all_scoped_ids,
        user_id=user_id,
    )

    # Relevant document must dominate context; irrelevant documents should NOT be forced into top slots
    acid_chunks = [c for c in chunks if c.document_id == "acid_doc"]
    assert len(acid_chunks) >= 2
    assert chunks[0].document_id == "acid_doc"


def test_benchmark_case_4_large_doc_domination_guard():
    """Case 4: Large Document with semi-relevant chunks vs Small Docs with high-relevance chunks."""
    col = InMemoryBenchmarkCollection()
    user_id = "user_bench_4"

    # Large doc with 8 medium relevance chunks
    large_docs = [f"MED_RELEVANCE: General database overview paragraph {i} covering storage engines." for i in range(8)]
    col.add(
        documents=large_docs,
        ids=[f"large:{i}" for i in range(8)],
        metadatas=[{"document_id": "large_doc", "user_id": user_id, "filename": "LargeTextbook.pdf", "chunk_index": i} for i in range(8)],
    )

    # Small doc A with high relevance chunk
    col.add(
        documents=["HIGH_RELEVANCE: Two-Phase Locking (2PL) guarantees serializability by acquiring locks before release."],
        ids=["small_a:0"],
        metadatas=[{"document_id": "small_a", "user_id": user_id, "filename": "2PL.pdf", "chunk_index": 0}],
    )

    # Small doc B with high relevance chunk
    col.add(
        documents=["HIGH_RELEVANCE: Strict 2PL holds all exclusive locks until commit time to prevent cascading aborts."],
        ids=["small_b:0"],
        metadatas=[{"document_id": "small_b", "user_id": user_id, "filename": "Strict2PL.pdf", "chunk_index": 0}],
    )

    chunks, metrics = retrieve_context(
        col,
        query="What is the difference between 2PL and Strict 2PL?",
        document_ids=["large_doc", "small_a", "small_b"],
        user_id=user_id,
    )

    sources = {c.document_id for c in chunks}
    # Large document MUST NOT crowd out small_a and small_b
    assert "small_a" in sources
    assert "small_b" in sources
    # Top 2 chunks must be the high-relevance ones from the small docs
    assert {chunks[0].document_id, chunks[1].document_id} == {"small_a", "small_b"}


def test_benchmark_case_5_duplicate_source_suppression():
    """Case 5: Duplicate Sources (identical or near-identical text across copies)."""
    col = InMemoryBenchmarkCollection()
    user_id = "user_bench_5"

    identical_text = "HIGH_RELEVANCE: Polymorphism in OOP allows objects of different classes to respond to common method calls."

    col.add(
        documents=[identical_text],
        ids=["lecture:0"],
        metadatas=[{"document_id": "lecture_doc", "user_id": user_id, "filename": "Lecture.pdf", "chunk_index": 0}],
    )
    col.add(
        documents=[identical_text],
        ids=["copy:0"],
        metadatas=[{"document_id": "copy_doc", "user_id": user_id, "filename": "LectureCopy.pdf", "chunk_index": 0}],
    )

    chunks, metrics = retrieve_context(
        col,
        query="What is polymorphism?",
        document_ids=["lecture_doc", "copy_doc"],
        user_id=user_id,
    )

    # Only 1 instance should survive context packing
    assert len(chunks) == 1
    assert metrics["suppressed_count"] >= 1


def test_benchmark_case_6_overlapping_adjacent_chunks():
    """Case 6: Overlapping Adjacent Chunks from the same document."""
    col = InMemoryBenchmarkCollection()
    user_id = "user_bench_6"

    col.add(
        documents=[
            "HIGH_RELEVANCE: Recursion is a method where the solution depends on solutions to smaller instances of the same problem. A base case stops recursion.",
            "HIGH_RELEVANCE: A base case stops recursion. Without a base case, recursion produces a stack overflow error in the call stack.",
        ],
        ids=["algo:0", "algo:1"],
        metadatas=[
            {"document_id": "algo_doc", "user_id": user_id, "filename": "Algorithms.pdf", "chunk_index": 0},
            {"document_id": "algo_doc", "user_id": user_id, "filename": "Algorithms.pdf", "chunk_index": 1},
        ],
    )

    chunks, metrics = retrieve_context(
        col,
        query="What is recursion and why is a base case necessary?",
        document_ids=["algo_doc"],
        user_id=user_id,
    )

    assert len(chunks) >= 1
    assert chunks[0].document_id == "algo_doc"


def test_benchmark_case_7_cross_document_synthesis():
    """Case 7: Cross-Document Synthesis (definition in A, example in B, limitation in C)."""
    col = InMemoryBenchmarkCollection()
    user_id = "user_bench_7"

    col.add(
        documents=["HIGH_RELEVANCE: QuickSort is a divide-and-conquer sorting algorithm based on partitioning around a pivot."],
        ids=["def:0"],
        metadatas=[{"document_id": "doc_def", "user_id": user_id, "filename": "QuickSort_Def.pdf", "chunk_index": 0}],
    )
    col.add(
        documents=["HIGH_RELEVANCE: For example, choosing the median-of-three as pivot achieves O(n log n) average time complexity."],
        ids=["ex:0"],
        metadatas=[{"document_id": "doc_ex", "user_id": user_id, "filename": "QuickSort_Example.pdf", "chunk_index": 0}],
    )
    col.add(
        documents=["HIGH_RELEVANCE: The worst-case time complexity degrades to O(n^2) when an unbalanced pivot is chosen repeatedly."],
        ids=["lim:0"],
        metadatas=[{"document_id": "doc_lim", "user_id": user_id, "filename": "QuickSort_Limits.pdf", "chunk_index": 0}],
    )

    chunks, metrics = retrieve_context(
        col,
        query="Explain QuickSort with definition, implementation example, and worst-case limitations",
        document_ids=["doc_def", "doc_ex", "doc_lim"],
        user_id=user_id,
    )

    sources = {c.document_id for c in chunks}
    assert sources == {"doc_def", "doc_ex", "doc_lim"}
    assert metrics["unique_sources"] == 3


def test_benchmark_case_8_fifty_document_folder_scaling():
    """Case 8: 50 Document Folder scaling and latency boundedness."""
    col = InMemoryBenchmarkCollection()
    user_id = "user_bench_8"

    doc_ids = []
    for i in range(50):
        doc_id = f"doc_{i:02d}"
        doc_ids.append(doc_id)
        if i == 7:
            col.add(
                documents=["HIGH_RELEVANCE: The target topic is located in document 7 with complete details."],
                ids=[f"{doc_id}:0"],
                metadatas=[{"document_id": doc_id, "user_id": user_id, "filename": f"Doc_{i}.pdf", "chunk_index": 0}],
            )
        else:
            col.add(
                documents=[f"LOW_RELEVANCE: Unrelated background context notes index {i}."],
                ids=[f"{doc_id}:0"],
                metadatas=[{"document_id": doc_id, "user_id": user_id, "filename": f"Doc_{i}.pdf", "chunk_index": 0}],
            )

    start_time = time.perf_counter()
    chunks, metrics = retrieve_context(
        col,
        query="What is the target topic?",
        document_ids=doc_ids,
        user_id=user_id,
    )
    duration_ms = (time.perf_counter() - start_time) * 1000.0

    assert len(chunks) >= 1
    assert chunks[0].document_id == "doc_07"
    # Latency across 50 documents must be tightly bounded
    assert duration_ms < 500.0
    assert metrics["candidate_count"] <= 48


def test_benchmark_case_9_selected_documents_scope():
    """Case 9: SELECTED_DOCUMENTS scope behaves consistently."""
    col = InMemoryBenchmarkCollection()
    user_id = "user_bench_9"

    col.add(
        documents=["HIGH_RELEVANCE: Quantum computing utilizes superposition and entanglement."],
        ids=["qc:0"],
        metadatas=[{"document_id": "qc_doc", "user_id": user_id, "filename": "Quantum.pdf", "chunk_index": 0}],
    )
    col.add(
        documents=["HIGH_RELEVANCE: Qubits represent linear combinations of |0> and |1> states."],
        ids=["qubit:0"],
        metadatas=[{"document_id": "qubit_doc", "user_id": user_id, "filename": "Qubits.pdf", "chunk_index": 0}],
    )

    chunks, metrics = retrieve_context(
        col,
        query="How do qubits and superposition enable quantum computing?",
        document_ids=["qc_doc", "qubit_doc"],
        user_id=user_id,
    )

    sources = {c.document_id for c in chunks}
    assert sources == {"qc_doc", "qubit_doc"}
    assert metrics["unique_sources"] == 2


def test_benchmark_case_10_recursive_folder_provenance():
    """Case 10: Recursive folder scope documents retain accurate provenance."""
    col = InMemoryBenchmarkCollection()
    user_id = "user_bench_10"

    col.add(
        documents=["HIGH_RELEVANCE: Direct folder document covering network topologies."],
        ids=["direct:0"],
        metadatas=[{"document_id": "direct_doc", "user_id": user_id, "filename": "Direct.pdf", "chunk_index": 0, "page_number": 1}],
    )
    col.add(
        documents=["HIGH_RELEVANCE: Subfolder A document covering TCP/IP protocol stack."],
        ids=["sub_a:0"],
        metadatas=[{"document_id": "sub_a_doc", "user_id": user_id, "filename": "ChildA.pdf", "chunk_index": 0, "page_number": 5}],
    )
    col.add(
        documents=["HIGH_RELEVANCE: Subfolder B document covering DNS resolution mechanisms."],
        ids=["sub_b:0"],
        metadatas=[{"document_id": "sub_b_doc", "user_id": user_id, "filename": "ChildB.pdf", "chunk_index": 0, "page_number": 12}],
    )

    chunks, metrics = retrieve_context(
        col,
        query="Explain networks including topologies, TCP/IP, and DNS",
        document_ids=["direct_doc", "sub_a_doc", "sub_b_doc"],
        user_id=user_id,
    )

    assert len(chunks) == 3
    sources = {c.document_id for c in chunks}
    assert sources == {"direct_doc", "sub_a_doc", "sub_b_doc"}

    # Provenance check: page numbers and filenames must be strictly preserved
    by_id = {c.document_id: c for c in chunks}
    assert by_id["direct_doc"].page_number == 1
    assert by_id["sub_a_doc"].page_number == 5
    assert by_id["sub_b_doc"].page_number == 12
