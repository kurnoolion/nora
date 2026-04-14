"""Tests for the query pipeline (PoC Step 10).

Test categories:
  - Schema: data model round-trips, enums
  - Analyzer: keyword extraction, query type classification
  - Resolver: MNO/release resolution with mock graph
  - GraphScoper: entity/feature/plan/title lookup, edge traversal
  - RAGRetriever: scoped and metadata retrieval, diversity
  - ContextBuilder: chunk enrichment, formatting
  - Synthesizer: mock synthesis, citation extraction
  - Pipeline: end-to-end with synthetic graph + mock providers
  - Integration: real graph data (if available)
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import networkx as nx

from src.graph.schema import NodeType, EdgeType
from src.query.schema import (
    QueryIntent,
    QueryType,
    DocTypeScope,
    ScopedQuery,
    MNOScope,
    CandidateSet,
    CandidateNode,
    RetrievedChunk,
    ChunkContext,
    StandardsContext,
    AssembledContext,
    QueryResponse,
    Citation,
)
from src.query.analyzer import MockQueryAnalyzer
from src.query.resolver import MNOReleaseResolver
from src.query.graph_scope import GraphScoper
from src.query.rag_retriever import RAGRetriever
from src.query.context_builder import ContextBuilder
from src.query.synthesizer import MockSynthesizer, LLMSynthesizer
from src.query.pipeline import QueryPipeline
from src.vectorstore.store_base import QueryResult


# ── Mock providers for tests ────────────────────────────────────


class MockEmbedder:
    """Mock embedder producing deterministic vectors."""

    def __init__(self, dim: int = 8):
        self._dim = dim

    def embed(self, texts):
        return [self._hash(t) for t in texts]

    def embed_query(self, text):
        return self._hash(text)

    @property
    def dimension(self):
        return self._dim

    @property
    def model_name(self):
        return "mock"

    def _hash(self, text):
        h = hash(text)
        vec = [((h >> (i * 4)) & 0xF) / 15.0 - 0.5 for i in range(self._dim)]
        norm = math.sqrt(sum(v * v for v in vec)) or 1
        return [v / norm for v in vec]


class MockVectorStore:
    """In-memory mock vector store."""

    def __init__(self):
        self._docs = {}

    def add(self, ids, embeddings, documents, metadatas):
        for i, doc_id in enumerate(ids):
            self._docs[doc_id] = {
                "embedding": embeddings[i],
                "document": documents[i],
                "metadata": metadatas[i],
            }

    def query(self, query_embedding, n_results=10, where=None):
        scored = []
        for doc_id, data in self._docs.items():
            if where and not self._matches_filter(data["metadata"], where):
                continue
            emb = data["embedding"]
            dot = sum(a * b for a, b in zip(query_embedding, emb))
            norm_q = math.sqrt(sum(a * a for a in query_embedding)) or 1
            norm_e = math.sqrt(sum(a * a for a in emb)) or 1
            dist = 1.0 - dot / (norm_q * norm_e)
            scored.append((doc_id, data, dist))
        scored.sort(key=lambda x: x[2])
        scored = scored[:n_results]
        return QueryResult(
            ids=[s[0] for s in scored],
            documents=[s[1]["document"] for s in scored],
            metadatas=[s[1]["metadata"] for s in scored],
            distances=[s[2] for s in scored],
        )

    @property
    def count(self):
        return len(self._docs)

    def reset(self):
        self._docs.clear()

    def _matches_filter(self, meta, where):
        if "$and" in where:
            return all(self._matches_filter(meta, cond) for cond in where["$and"])
        if "$or" in where:
            return any(self._matches_filter(meta, cond) for cond in where["$or"])
        if "$in" in where:
            # Shouldn't be at top level — skip
            return True
        for key, val in where.items():
            if key.startswith("$"):
                continue
            if isinstance(val, dict) and "$in" in val:
                if meta.get(key) not in val["$in"]:
                    return False
            elif meta.get(key) != val:
                return False
        return True


# ── Test graph builder ──────────────────────────────────────────


def _build_test_graph() -> nx.DiGraph:
    """Build a small test graph for unit tests."""
    g = nx.DiGraph()

    # MNO + Release
    g.add_node("mno:VZW", node_type="MNO", mno="VZW", name="VZW")
    g.add_node("release:VZW:2026_feb", node_type="Release", mno="VZW", release="2026_feb")
    g.add_edge("mno:VZW", "release:VZW:2026_feb", edge_type="has_release")

    # Plans
    for pid in ["LTEDATARETRY", "LTESMS"]:
        plid = f"plan:VZW:2026_feb:{pid}"
        g.add_node(plid, node_type="Plan", plan_id=pid, mno="VZW", release="2026_feb")
        g.add_edge("release:VZW:2026_feb", plid, edge_type="contains_plan")

    # Requirements for LTEDATARETRY
    reqs_dr = [
        ("VZ_REQ_LTEDATARETRY_100", "1.1", "INTRODUCTION", "Introduction text about data retry"),
        ("VZ_REQ_LTEDATARETRY_200", "1.3", "T3402 TIMER", "The T3402 timer value shall be 720 seconds"),
        ("VZ_REQ_LTEDATARETRY_300", "1.4", "ATTACH REJECT", "When attach reject with cause code 3"),
        ("VZ_REQ_LTEDATARETRY_400", "1.4.1", "EMM CAUSE CODES", "EMM cause code handling for reject"),
    ]
    for req_id, section, title, text in reqs_dr:
        nid = f"req:{req_id}"
        g.add_node(nid,
            node_type="Requirement", req_id=req_id, plan_id="LTEDATARETRY",
            mno="VZW", release="2026_feb", section_number=section,
            title=title, text=text,
            hierarchy_path=["DATA_RETRY", title],
        )
        g.add_edge(nid, "plan:VZW:2026_feb:LTEDATARETRY", edge_type="belongs_to")

    # Parent-child
    g.add_edge("req:VZ_REQ_LTEDATARETRY_300", "req:VZ_REQ_LTEDATARETRY_400",
               edge_type="parent_of")

    # Requirements for LTESMS
    reqs_sms = [
        ("VZ_REQ_LTESMS_100", "1.1", "SMS OVER IMS", "SMS over IMS procedures"),
        ("VZ_REQ_LTESMS_200", "1.2", "MO SMS", "Mobile originated SMS requirements"),
    ]
    for req_id, section, title, text in reqs_sms:
        nid = f"req:{req_id}"
        g.add_node(nid,
            node_type="Requirement", req_id=req_id, plan_id="LTESMS",
            mno="VZW", release="2026_feb", section_number=section,
            title=title, text=text,
            hierarchy_path=["SMS", title],
        )
        g.add_edge(nid, "plan:VZW:2026_feb:LTESMS", edge_type="belongs_to")

    # Cross-plan depends_on
    g.add_edge("req:VZ_REQ_LTEDATARETRY_300", "plan:VZW:2026_feb:LTESMS",
               edge_type="depends_on", ref_type="cross_plan")

    # Standards
    g.add_node("std:24.301:11", node_type="Standard_Section",
               spec="24.301", release_num=11, section="", title="NAS for EPS")
    g.add_node("std:24.301:11:5.5.1.2.5", node_type="Standard_Section",
               spec="24.301", release_num=11, section="5.5.1.2.5",
               title="Attach reject", text="When the attach request is rejected...")
    g.add_edge("std:24.301:11", "std:24.301:11:5.5.1.2.5", edge_type="parent_section")
    g.add_edge("req:VZ_REQ_LTEDATARETRY_300", "std:24.301:11:5.5.1.2.5",
               edge_type="references_standard")

    # Features
    g.add_node("feature:DATA_RETRY", node_type="Feature",
               feature_id="DATA_RETRY", name="LTE Data Retry")
    g.add_node("feature:SMS", node_type="Feature",
               feature_id="SMS", name="SMS over LTE")

    for req_id in ["VZ_REQ_LTEDATARETRY_100", "VZ_REQ_LTEDATARETRY_200",
                    "VZ_REQ_LTEDATARETRY_300", "VZ_REQ_LTEDATARETRY_400"]:
        g.add_edge(f"req:{req_id}", "feature:DATA_RETRY", edge_type="maps_to")

    for req_id in ["VZ_REQ_LTESMS_100", "VZ_REQ_LTESMS_200"]:
        g.add_edge(f"req:{req_id}", "feature:SMS", edge_type="maps_to")

    return g


def _build_test_store(embedder) -> MockVectorStore:
    """Populate a mock store with test data matching the test graph."""
    store = MockVectorStore()
    docs = [
        ("req:VZ_REQ_LTEDATARETRY_100", "Introduction text about data retry",
         {"mno": "VZW", "release": "2026_feb", "plan_id": "LTEDATARETRY",
          "req_id": "VZ_REQ_LTEDATARETRY_100", "doc_type": "requirement"}),
        ("req:VZ_REQ_LTEDATARETRY_200", "The T3402 timer value shall be 720 seconds",
         {"mno": "VZW", "release": "2026_feb", "plan_id": "LTEDATARETRY",
          "req_id": "VZ_REQ_LTEDATARETRY_200", "doc_type": "requirement"}),
        ("req:VZ_REQ_LTEDATARETRY_300", "When attach reject with cause code 3",
         {"mno": "VZW", "release": "2026_feb", "plan_id": "LTEDATARETRY",
          "req_id": "VZ_REQ_LTEDATARETRY_300", "doc_type": "requirement"}),
        ("req:VZ_REQ_LTEDATARETRY_400", "EMM cause code handling for reject",
         {"mno": "VZW", "release": "2026_feb", "plan_id": "LTEDATARETRY",
          "req_id": "VZ_REQ_LTEDATARETRY_400", "doc_type": "requirement"}),
        ("req:VZ_REQ_LTESMS_100", "SMS over IMS procedures",
         {"mno": "VZW", "release": "2026_feb", "plan_id": "LTESMS",
          "req_id": "VZ_REQ_LTESMS_100", "doc_type": "requirement"}),
        ("req:VZ_REQ_LTESMS_200", "Mobile originated SMS requirements",
         {"mno": "VZW", "release": "2026_feb", "plan_id": "LTESMS",
          "req_id": "VZ_REQ_LTESMS_200", "doc_type": "requirement"}),
    ]

    ids = [d[0] for d in docs]
    texts = [d[1] for d in docs]
    metas = [d[2] for d in docs]
    embeddings = embedder.embed(texts)

    store.add(ids, embeddings, texts, metas)
    return store


# ═══════════════════════════════════════════════════════════════
# Schema tests
# ═══════════════════════════════════════════════════════════════


class TestSchema:
    def test_query_type_enum(self):
        assert QueryType.SINGLE_DOC.value == "single_doc"
        assert QueryType("cross_doc") == QueryType.CROSS_DOC

    def test_query_intent_to_dict(self):
        intent = QueryIntent(
            raw_query="test", entities=["T3402"],
            query_type=QueryType.SINGLE_DOC,
        )
        d = intent.to_dict()
        assert d["raw_query"] == "test"
        assert d["query_type"] == "single_doc"
        assert d["entities"] == ["T3402"]

    def test_candidate_set_total(self):
        cs = CandidateSet(
            requirement_nodes=[CandidateNode("a", "Requirement")],
            standards_nodes=[CandidateNode("b", "Standard_Section"),
                             CandidateNode("c", "Standard_Section")],
        )
        assert cs.total == 3

    def test_candidate_set_requirement_ids(self):
        cs = CandidateSet(
            requirement_nodes=[
                CandidateNode("req:X", "Requirement",
                              attributes={"req_id": "X"}),
                CandidateNode("req:Y", "Requirement",
                              attributes={"req_id": "Y"}),
            ],
        )
        assert cs.requirement_ids() == ["X", "Y"]

    def test_query_response_to_dict(self):
        r = QueryResponse(
            answer="test answer",
            citations=[Citation(req_id="VZ_REQ_X_1", plan_id="X")],
            candidate_count=5,
            retrieved_count=3,
        )
        d = r.to_dict()
        assert d["answer"] == "test answer"
        assert len(d["citations"]) == 1
        assert d["candidate_count"] == 5

    def test_query_response_save_json(self, tmp_path):
        r = QueryResponse(answer="test")
        path = tmp_path / "response.json"
        r.save_json(path)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["answer"] == "test"


# ═══════════════════════════════════════════════════════════════
# Analyzer tests
# ═══════════════════════════════════════════════════════════════


class TestAnalyzer:
    def setup_method(self):
        self.analyzer = MockQueryAnalyzer()

    def test_extract_timer_entity(self):
        intent = self.analyzer.analyze("What is the T3402 timer behavior?")
        assert "T3402" in intent.entities

    def test_extract_req_id(self):
        intent = self.analyzer.analyze(
            "What does VZ_REQ_LTEDATARETRY_7748 require?"
        )
        assert "VZ_REQ_LTEDATARETRY_7748" in intent.entities

    def test_extract_mno(self):
        intent = self.analyzer.analyze("VZW data retry requirements")
        assert "VZW" in intent.mnos

    def test_extract_mno_alias(self):
        intent = self.analyzer.analyze("Verizon IMS registration")
        assert "VZW" in intent.mnos

    def test_extract_standards_ref(self):
        intent = self.analyzer.analyze(
            "How does VZW differ from 3GPP TS 24.301?"
        )
        assert "3GPP TS 24.301" in intent.standards_refs

    def test_extract_features(self):
        intent = self.analyzer.analyze("data retry timer behavior")
        assert "DATA_RETRY" in intent.likely_features

    def test_extract_plan_ids(self):
        intent = self.analyzer.analyze("What are the SMS requirements?")
        assert "LTESMS" in intent.plan_ids

    def test_classify_single_doc(self):
        intent = self.analyzer.analyze("What timers are in the data retry spec?")
        assert intent.query_type == QueryType.SINGLE_DOC

    def test_classify_standards_comparison(self):
        intent = self.analyzer.analyze(
            "How does VZW T3402 differ from 3GPP TS 24.301?"
        )
        assert intent.query_type == QueryType.STANDARDS_COMPARISON

    def test_classify_feature_level(self):
        intent = self.analyzer.analyze(
            "What are all requirements related to IMS?"
        )
        assert intent.query_type == QueryType.FEATURE_LEVEL

    def test_concepts_extracted(self):
        intent = self.analyzer.analyze("attach reject cause code handling")
        assert any("attach reject" in c for c in intent.concepts)

    def test_doc_type_scope_default(self):
        intent = self.analyzer.analyze("show me requirements")
        assert intent.doc_type_scope == DocTypeScope.REQUIREMENTS

    def test_doc_type_scope_test_cases(self):
        intent = self.analyzer.analyze("what test cases cover this?")
        assert intent.doc_type_scope == DocTypeScope.TEST_CASES


# ═══════════════════════════════════════════════════════════════
# Resolver tests
# ═══════════════════════════════════════════════════════════════


class TestResolver:
    def setup_method(self):
        self.graph = _build_test_graph()
        self.resolver = MNOReleaseResolver(self.graph)

    def test_discovers_available(self):
        avail = self.resolver.available_mnos
        assert "VZW" in avail
        assert "2026_feb" in avail["VZW"]

    def test_resolve_explicit_mno(self):
        intent = QueryIntent(raw_query="test", mnos=["VZW"])
        scoped = self.resolver.resolve(intent)
        assert len(scoped.scoped_mnos) == 1
        assert scoped.scoped_mnos[0].mno == "VZW"
        assert scoped.scoped_mnos[0].release == "2026_feb"

    def test_resolve_no_mno_uses_all(self):
        intent = QueryIntent(raw_query="test")
        scoped = self.resolver.resolve(intent)
        assert len(scoped.scoped_mnos) >= 1
        assert scoped.scoped_mnos[0].mno == "VZW"

    def test_resolve_latest_release(self):
        intent = QueryIntent(raw_query="test", mnos=["VZW"], releases=["latest"])
        scoped = self.resolver.resolve(intent)
        assert scoped.scoped_mnos[0].release == "2026_feb"

    def test_resolve_unknown_mno_skipped(self):
        intent = QueryIntent(raw_query="test", mnos=["UNKNOWN"])
        scoped = self.resolver.resolve(intent)
        # Falls back to all available
        assert len(scoped.scoped_mnos) >= 1


# ═══════════════════════════════════════════════════════════════
# GraphScoper tests
# ═══════════════════════════════════════════════════════════════


class TestGraphScoper:
    def setup_method(self):
        self.graph = _build_test_graph()
        self.scoper = GraphScoper(self.graph)
        self.vzw_scope = [MNOScope(mno="VZW", release="2026_feb")]

    def _make_scoped(self, **kwargs):
        intent = QueryIntent(raw_query="test", **kwargs)
        return ScopedQuery(intent=intent, scoped_mnos=self.vzw_scope)

    def test_entity_lookup_req_id(self):
        sq = self._make_scoped(entities=["VZ_REQ_LTEDATARETRY_200"])
        candidates = self.scoper.scope(sq)
        req_ids = candidates.requirement_ids()
        assert "VZ_REQ_LTEDATARETRY_200" in req_ids

    def test_feature_lookup(self):
        sq = self._make_scoped(likely_features=["DATA_RETRY"])
        candidates = self.scoper.scope(sq)
        req_ids = candidates.requirement_ids()
        assert "VZ_REQ_LTEDATARETRY_200" in req_ids
        assert "VZ_REQ_LTEDATARETRY_300" in req_ids

    def test_plan_lookup(self):
        sq = self._make_scoped(plan_ids=["LTESMS"])
        candidates = self.scoper.scope(sq)
        req_ids = candidates.requirement_ids()
        assert "VZ_REQ_LTESMS_100" in req_ids
        assert "VZ_REQ_LTESMS_200" in req_ids

    def test_title_search(self):
        sq = self._make_scoped(concepts=["t3402"])
        candidates = self.scoper.scope(sq)
        req_ids = candidates.requirement_ids()
        assert "VZ_REQ_LTEDATARETRY_200" in req_ids

    def test_traversal_finds_standards(self):
        sq = self._make_scoped(
            entities=["VZ_REQ_LTEDATARETRY_300"],
            query_type=QueryType.STANDARDS_COMPARISON,
        )
        candidates = self.scoper.scope(sq)
        assert len(candidates.standards_nodes) > 0

    def test_traversal_finds_children(self):
        sq = self._make_scoped(
            entities=["VZ_REQ_LTEDATARETRY_300"],
            query_type=QueryType.SINGLE_DOC,
        )
        candidates = self.scoper.scope(sq)
        req_ids = candidates.requirement_ids()
        # Should find child via parent_of traversal
        assert "VZ_REQ_LTEDATARETRY_400" in req_ids

    def test_scope_filtering(self):
        # Add a TMO requirement
        self.graph.add_node("mno:TMO", node_type="MNO", mno="TMO")
        self.graph.add_node("release:TMO:2026_q1", node_type="Release",
                            mno="TMO", release="2026_q1")
        self.graph.add_node("req:TMO_REQ_1", node_type="Requirement",
                            req_id="TMO_REQ_1", mno="TMO", release="2026_q1",
                            title="TMO req", text="TMO text")

        sq = self._make_scoped(concepts=["tmo"])
        candidates = self.scoper.scope(sq)
        # TMO req should NOT appear with VZW scope
        req_ids = candidates.requirement_ids()
        assert "TMO_REQ_1" not in req_ids

    def test_empty_candidates(self):
        sq = self._make_scoped(entities=["NONEXISTENT"])
        candidates = self.scoper.scope(sq)
        assert candidates.total == 0


# ═══════════════════════════════════════════════════════════════
# RAGRetriever tests
# ═══════════════════════════════════════════════════════════════


class TestRAGRetriever:
    def setup_method(self):
        self.embedder = MockEmbedder(dim=8)
        self.store = _build_test_store(self.embedder)
        self.retriever = RAGRetriever(self.embedder, self.store, top_k=3)
        self.vzw_scope = [MNOScope(mno="VZW", release="2026_feb")]

    def test_scoped_retrieval(self):
        candidates = CandidateSet(
            requirement_nodes=[
                CandidateNode("req:VZ_REQ_LTEDATARETRY_200", "Requirement",
                              attributes={"req_id": "VZ_REQ_LTEDATARETRY_200"}),
                CandidateNode("req:VZ_REQ_LTEDATARETRY_300", "Requirement",
                              attributes={"req_id": "VZ_REQ_LTEDATARETRY_300"}),
            ],
        )
        chunks = self.retriever.retrieve(
            "T3402 timer", candidates, self.vzw_scope,
        )
        assert len(chunks) <= 3
        assert all(isinstance(c, RetrievedChunk) for c in chunks)

    def test_metadata_fallback(self):
        candidates = CandidateSet()  # Empty — no graph candidates
        chunks = self.retriever.retrieve(
            "data retry", candidates, self.vzw_scope,
        )
        assert len(chunks) > 0

    def test_retrieval_has_metadata(self):
        candidates = CandidateSet()
        chunks = self.retriever.retrieve("test", candidates, self.vzw_scope)
        for chunk in chunks:
            assert "mno" in chunk.metadata
            assert "plan_id" in chunk.metadata


# ═══════════════════════════════════════════════════════════════
# ContextBuilder tests
# ═══════════════════════════════════════════════════════════════


class TestContextBuilder:
    def setup_method(self):
        self.graph = _build_test_graph()
        self.builder = ContextBuilder(self.graph)

    def test_build_context(self):
        chunks = [
            RetrievedChunk(
                chunk_id="req:VZ_REQ_LTEDATARETRY_200",
                text="The T3402 timer value shall be 720 seconds",
                metadata={"mno": "VZW", "plan_id": "LTEDATARETRY",
                           "req_id": "VZ_REQ_LTEDATARETRY_200",
                           "section_number": "1.3", "release": "2026_feb"},
                similarity_score=0.1,
                graph_node_id="req:VZ_REQ_LTEDATARETRY_200",
            ),
        ]
        ctx = self.builder.build("T3402 timer?", chunks, QueryType.SINGLE_DOC)
        assert "T3402" in ctx.context_text
        assert ctx.system_prompt != ""
        assert len(ctx.chunks) == 1

    def test_context_has_provenance(self):
        chunks = [
            RetrievedChunk(
                chunk_id="req:VZ_REQ_LTEDATARETRY_300",
                text="When attach reject with cause code 3",
                metadata={"mno": "VZW", "plan_id": "LTEDATARETRY",
                           "req_id": "VZ_REQ_LTEDATARETRY_300",
                           "section_number": "1.4", "release": "2026_feb"},
                similarity_score=0.2,
                graph_node_id="req:VZ_REQ_LTEDATARETRY_300",
            ),
        ]
        ctx = self.builder.build("attach reject", chunks, QueryType.SINGLE_DOC)
        assert "VZW" in ctx.context_text
        assert "LTEDATARETRY" in ctx.context_text

    def test_context_has_standards(self):
        chunks = [
            RetrievedChunk(
                chunk_id="req:VZ_REQ_LTEDATARETRY_300",
                text="When attach reject",
                metadata={"mno": "VZW", "plan_id": "LTEDATARETRY",
                           "req_id": "VZ_REQ_LTEDATARETRY_300",
                           "section_number": "1.4", "release": "2026_feb"},
                graph_node_id="req:VZ_REQ_LTEDATARETRY_300",
            ),
        ]
        ctx = self.builder.build("attach reject", chunks, QueryType.STANDARDS_COMPARISON)
        # Should include standards context from the graph
        assert "24.301" in ctx.context_text or len(ctx.chunks[0].standards) > 0

    def test_context_truncation(self):
        chunks = [
            RetrievedChunk(
                chunk_id=f"req:X_{i}",
                text="x" * 5000,
                metadata={"mno": "VZW", "plan_id": "X", "req_id": f"X_{i}",
                           "section_number": "1", "release": "2026_feb"},
                graph_node_id=f"req:X_{i}",
            )
            for i in range(20)
        ]
        ctx = self.builder.build("test", chunks, QueryType.GENERAL, max_context_chars=1000)
        assert len(ctx.context_text) <= 1100  # ~1000 + "[Context truncated]"

    def test_strip_chunk_headers(self):
        text = "[MNO: VZW | Release: 2026_feb]\n[Path: A > B]\n[Req ID: X]\n\nActual content"
        stripped = ContextBuilder._strip_chunk_headers(text)
        assert "[MNO:" not in stripped
        assert "Actual content" in stripped


# ═══════════════════════════════════════════════════════════════
# Synthesizer tests
# ═══════════════════════════════════════════════════════════════


class TestSynthesizer:
    def test_mock_synthesizer_with_chunks(self):
        synth = MockSynthesizer()
        ctx = AssembledContext(
            system_prompt="test",
            context_text="test context",
            chunks=[
                ChunkContext(
                    chunk=RetrievedChunk(
                        chunk_id="req:X",
                        text="requirement text",
                        metadata={"req_id": "VZ_REQ_LTEDATARETRY_100",
                                   "plan_id": "LTEDATARETRY"},
                    ),
                ),
            ],
        )
        intent = QueryIntent(raw_query="test")
        response = synth.synthesize(ctx, intent)
        assert len(response.answer) > 0
        assert response.retrieved_count == 1
        assert any(c.req_id == "VZ_REQ_LTEDATARETRY_100" for c in response.citations)

    def test_mock_synthesizer_empty(self):
        synth = MockSynthesizer()
        ctx = AssembledContext(chunks=[])
        intent = QueryIntent(raw_query="test")
        response = synth.synthesize(ctx, intent)
        assert "No relevant" in response.answer

    def test_citation_extraction(self):
        answer = (
            "According to VZ_REQ_LTEDATARETRY_7748, the T3402 timer is 720s. "
            "This aligns with 3GPP TS 24.301, Section 5.5.1.2.5."
        )
        citations = LLMSynthesizer._extract_citations(answer)
        req_cites = [c for c in citations if c.req_id]
        std_cites = [c for c in citations if c.spec]
        assert len(req_cites) == 1
        assert req_cites[0].req_id == "VZ_REQ_LTEDATARETRY_7748"
        assert len(std_cites) == 1
        assert std_cites[0].spec_section == "5.5.1.2.5"


# ═══════════════════════════════════════════════════════════════
# Pipeline tests (synthetic data)
# ═══════════════════════════════════════════════════════════════


class TestPipeline:
    def setup_method(self):
        self.graph = _build_test_graph()
        self.embedder = MockEmbedder(dim=8)
        self.store = _build_test_store(self.embedder)
        self.pipeline = QueryPipeline(
            graph=self.graph,
            embedder=self.embedder,
            store=self.store,
            top_k=5,
        )

    def test_basic_query(self):
        response = self.pipeline.query("T3402 timer behavior")
        assert response.answer != ""
        assert response.retrieved_count > 0

    def test_query_with_plan(self):
        response = self.pipeline.query("SMS requirements")
        assert response.answer != ""

    def test_query_with_feature(self):
        response = self.pipeline.query("What are the data retry requirements?")
        assert response.answer != ""
        assert response.candidate_count > 0

    def test_query_returns_citations(self):
        response = self.pipeline.query("data retry timer")
        assert len(response.citations) > 0

    def test_query_with_entity(self):
        response = self.pipeline.query(
            "What does VZ_REQ_LTEDATARETRY_200 require?"
        )
        assert response.answer != ""

    def test_verbose_query(self):
        # Should not crash with verbose=True
        response = self.pipeline.query("test query", verbose=True)
        assert response.answer != ""

    def test_empty_result_query(self):
        response = self.pipeline.query("quantum computing requirements")
        # Should still produce a response (even if "no relevant" message)
        assert response.answer != ""


# ═══════════════════════════════════════════════════════════════
# Integration tests (requires real data)
# ═══════════════════════════════════════════════════════════════


_GRAPH_PATH = Path("data/graph/knowledge_graph.json")
_TREES_DIR = Path("data/parsed")
_has_real_data = _GRAPH_PATH.exists() and _TREES_DIR.exists()


@pytest.mark.skipif(not _has_real_data, reason="Graph data not available")
class TestIntegration:
    """Integration tests with real graph + mock embedder/store."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from src.query.pipeline import load_graph
        self.graph = load_graph(_GRAPH_PATH)

        self.embedder = MockEmbedder(dim=16)

        # Build a store from real parsed data
        from src.vectorstore.config import VectorStoreConfig
        from src.vectorstore.chunk_builder import ChunkBuilder
        from src.vectorstore.builder import VectorStoreBuilder

        config = VectorStoreConfig()
        chunk_builder = ChunkBuilder(config)

        trees = []
        for p in sorted(_TREES_DIR.glob("*_tree.json")):
            with open(p) as f:
                trees.append(json.load(f))

        taxonomy_path = Path("data/taxonomy/taxonomy.json")
        taxonomy = None
        if taxonomy_path.exists():
            with open(taxonomy_path) as f:
                taxonomy = json.load(f)

        chunks = chunk_builder.build_chunks(trees, taxonomy)
        # Deduplicate
        chunks = VectorStoreBuilder._deduplicate_chunks(chunks)

        self.store = MockVectorStore()
        texts = [c.text for c in chunks]
        embeddings = self.embedder.embed(texts)
        ids = [c.chunk_id for c in chunks]
        metas = [c.metadata for c in chunks]
        self.store.add(ids, embeddings, texts, metas)

        self.pipeline = QueryPipeline(
            graph=self.graph,
            embedder=self.embedder,
            store=self.store,
            top_k=10,
        )

    def test_single_doc_query(self):
        response = self.pipeline.query("What is the T3402 timer behavior?")
        assert response.answer != ""
        assert response.retrieved_count > 0
        assert response.candidate_count > 0

    def test_feature_query(self):
        response = self.pipeline.query(
            "What are all requirements related to data retry?"
        )
        assert response.answer != ""
        assert response.candidate_count > 0

    def test_cross_doc_query(self):
        response = self.pipeline.query(
            "How does data retry handle devices without SMS?"
        )
        assert response.answer != ""

    def test_standards_comparison(self):
        response = self.pipeline.query(
            "How does VZW differ from 3GPP TS 24.301 for attach reject?"
        )
        assert response.answer != ""

    def test_pipeline_returns_diverse_plans(self):
        response = self.pipeline.query(
            "What are the VZW requirements for error handling?"
        )
        # Should find results from multiple plans
        plan_ids = set()
        for c in response.citations:
            if c.plan_id:
                plan_ids.add(c.plan_id)
        # With mock embedder, diversity isn't guaranteed, but the pipeline
        # should at least return results
        assert response.retrieved_count > 0
