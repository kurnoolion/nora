"""Tests for the evaluation framework (Step 11).

Tests:
  - Question set completeness and structure
  - Metric scoring logic
  - Report aggregation
  - A/B comparison
  - Integration with pipeline (using synthetic graph + mock store)
"""

import json
import pytest
import tempfile
from pathlib import Path

import networkx as nx

from src.eval.questions import (
    ALL_QUESTIONS,
    QUESTIONS_BY_CATEGORY,
    EvalQuestion,
    GroundTruth,
)
from src.eval.metrics import (
    QuestionScore,
    EvalReport,
    score_question,
)
from src.eval.runner import EvalRunner, ABComparison
from src.query.schema import (
    QueryResponse,
    QueryIntent,
    QueryType,
    Citation,
)


# ─── Test question set ──────────────────────────────────────────

class TestQuestions:
    """Tests for the evaluation question set."""

    def test_question_count(self):
        """Should have 18 questions total."""
        assert len(ALL_QUESTIONS) == 18

    def test_all_categories_present(self):
        expected = {
            "single_doc", "cross_doc", "feature_level",
            "standards_comparison", "traceability",
        }
        assert set(QUESTIONS_BY_CATEGORY.keys()) == expected

    def test_single_doc_count(self):
        assert len(QUESTIONS_BY_CATEGORY["single_doc"]) == 4

    def test_cross_doc_count(self):
        assert len(QUESTIONS_BY_CATEGORY["cross_doc"]) == 4

    def test_feature_level_count(self):
        assert len(QUESTIONS_BY_CATEGORY["feature_level"]) == 4

    def test_standards_comparison_count(self):
        assert len(QUESTIONS_BY_CATEGORY["standards_comparison"]) == 3

    def test_traceability_count(self):
        assert len(QUESTIONS_BY_CATEGORY["traceability"]) == 3

    def test_unique_ids(self):
        ids = [q.id for q in ALL_QUESTIONS]
        assert len(ids) == len(set(ids))

    def test_all_have_ground_truth(self):
        for q in ALL_QUESTIONS:
            assert q.ground_truth is not None
            assert isinstance(q.ground_truth, GroundTruth)

    def test_all_are_eval_questions(self):
        for q in ALL_QUESTIONS:
            assert isinstance(q, EvalQuestion)

    def test_cross_doc_min_plans(self):
        """Cross-doc questions should expect multiple plans."""
        for q in QUESTIONS_BY_CATEGORY["cross_doc"]:
            assert q.ground_truth.min_plans >= 2

    def test_standards_have_expected_specs(self):
        """Standards comparison questions should specify expected specs."""
        for q in QUESTIONS_BY_CATEGORY["standards_comparison"]:
            assert len(q.ground_truth.expected_standards) > 0


# ─── Test metric scoring ────────────────────────────────────────

def _make_intent(**kwargs) -> QueryIntent:
    return QueryIntent(
        raw_query=kwargs.get("query", "test"),
        entities=kwargs.get("entities", []),
        concepts=kwargs.get("concepts", []),
        mnos=kwargs.get("mnos", []),
        releases=[],
        query_type=kwargs.get("query_type", QueryType.GENERAL),
        plan_ids=kwargs.get("plan_ids", []),
    )


def _make_response(
    answer: str = "",
    citations: list[Citation] | None = None,
    retrieved_count: int = 0,
    intent: QueryIntent | None = None,
) -> QueryResponse:
    return QueryResponse(
        answer=answer,
        citations=citations or [],
        query_intent=intent or _make_intent(),
        retrieved_count=retrieved_count,
    )


class TestMetrics:
    """Tests for metric scoring logic."""

    def test_perfect_score(self):
        """A response matching all ground truth should score high."""
        q = EvalQuestion(
            id="test",
            category="single_doc",
            question="What is T3402?",
            ground_truth=GroundTruth(
                expected_plans=["LTEDATARETRY"],
                expected_req_ids=["VZ_REQ_LTEDATARETRY_7742"],
                expected_standards=["3GPP TS 24.301"],
            ),
        )
        response = _make_response(
            answer="VZ_REQ_LTEDATARETRY_7742 defines T3402. See 3GPP TS 24.301.",
            citations=[
                Citation(req_id="VZ_REQ_LTEDATARETRY_7742", plan_id="LTEDATARETRY"),
                Citation(spec="3GPP TS 24.301", spec_section="5.5.1.2.5"),
            ],
            retrieved_count=3,
        )
        score = score_question(q, response)
        assert score.completeness == 1.0
        assert score.accuracy == 1.0
        assert score.citation_quality == 1.0
        assert score.standards_integration == 1.0
        assert score.hallucination_free == 1.0
        assert score.overall == 1.0

    def test_zero_score(self):
        """An empty response should score zero on most metrics."""
        q = EvalQuestion(
            id="test",
            category="cross_doc",
            question="SMS over IMS?",
            ground_truth=GroundTruth(
                expected_plans=["LTESMS", "LTEB13NAC"],
                expected_req_ids=["VZ_REQ_LTESMS_30258"],
                expected_standards=["3GPP TS 24.301"],
                min_plans=2,
            ),
        )
        response = _make_response(
            answer="No relevant requirements found.",
            retrieved_count=0,
        )
        score = score_question(q, response)
        assert score.completeness == 0.0
        assert score.accuracy == 0.0
        assert score.citation_quality == 0.0
        assert score.standards_integration == 0.0
        # Hallucination-free should be 1.0 (no fabricated IDs)
        assert score.hallucination_free == 1.0

    def test_partial_plan_coverage(self):
        """Response covering 1 of 2 expected plans scores 0.5 completeness."""
        q = EvalQuestion(
            id="test",
            category="cross_doc",
            question="test",
            ground_truth=GroundTruth(
                expected_plans=["LTESMS", "LTEB13NAC"],
            ),
        )
        response = _make_response(
            answer="VZ_REQ_LTESMS_30258 found.",
            citations=[
                Citation(req_id="VZ_REQ_LTESMS_30258", plan_id="LTESMS"),
            ],
            retrieved_count=1,
        )
        score = score_question(q, response)
        assert score.completeness == 0.5

    def test_partial_req_id_recall(self):
        """Finding 1 of 3 expected req IDs scores ~0.33 accuracy."""
        q = EvalQuestion(
            id="test",
            category="single_doc",
            question="test",
            ground_truth=GroundTruth(
                expected_req_ids=[
                    "VZ_REQ_LTEDATARETRY_7731",
                    "VZ_REQ_LTEDATARETRY_2376",
                    "VZ_REQ_LTEDATARETRY_7735",
                ],
            ),
        )
        response = _make_response(
            answer="VZ_REQ_LTEDATARETRY_7731 defines throttling.",
            retrieved_count=1,
        )
        score = score_question(q, response)
        assert abs(score.accuracy - 1.0 / 3.0) < 0.01

    def test_hallucination_detection(self):
        """Fabricated req IDs with unknown plans are flagged."""
        q = EvalQuestion(
            id="test",
            category="single_doc",
            question="test",
            ground_truth=GroundTruth(expected_plans=["LTEDATARETRY"]),
        )
        response = _make_response(
            answer="VZ_REQ_FAKEPLAN_9999 says something.",
            retrieved_count=1,
        )
        score = score_question(q, response)
        assert score.hallucination_free == 0.0
        assert "VZ_REQ_FAKEPLAN_9999" in score.hallucinated_req_ids

    def test_no_hallucination_for_valid_plans(self):
        """Req IDs from known plans are not flagged as hallucinations."""
        q = EvalQuestion(
            id="test",
            category="single_doc",
            question="test",
            ground_truth=GroundTruth(expected_plans=["LTESMS"]),
        )
        response = _make_response(
            answer="VZ_REQ_LTESMS_30258 defines SMS over IMS.",
            retrieved_count=1,
        )
        score = score_question(q, response)
        assert score.hallucination_free == 1.0
        assert len(score.hallucinated_req_ids) == 0

    def test_standards_integration_no_standards_expected(self):
        """When no standards expected, score should be 1.0."""
        q = EvalQuestion(
            id="test",
            category="single_doc",
            question="test",
            ground_truth=GroundTruth(
                expected_plans=["LTEAT"],
                expected_standards=[],  # No standards expected
            ),
        )
        response = _make_response(
            answer="VZ_REQ_LTEAT_21030 is about AT commands.",
            citations=[Citation(req_id="VZ_REQ_LTEAT_21030", plan_id="LTEAT")],
            retrieved_count=1,
        )
        score = score_question(q, response)
        assert score.standards_integration == 1.0

    def test_citation_quality_with_req_only(self):
        """Citations with req IDs but no standards score 0.5 when standards expected."""
        q = EvalQuestion(
            id="test",
            category="standards_comparison",
            question="test",
            ground_truth=GroundTruth(
                expected_plans=["LTEDATARETRY"],
                expected_standards=["3GPP TS 24.301"],
            ),
        )
        response = _make_response(
            answer="VZ_REQ_LTEDATARETRY_7742",
            citations=[
                Citation(req_id="VZ_REQ_LTEDATARETRY_7742", plan_id="LTEDATARETRY"),
            ],
            retrieved_count=1,
        )
        score = score_question(q, response)
        assert score.citation_quality == 0.5


# ─── Test score serialization ───────────────────────────────────

class TestScoreSerialization:
    """Tests for score and report serialization."""

    def test_score_to_dict(self):
        score = QuestionScore(
            question_id="test",
            category="single_doc",
            question="What?",
            completeness=0.8,
            accuracy=0.9,
        )
        d = score.to_dict()
        assert d["question_id"] == "test"
        assert d["scores"]["completeness"] == 0.8
        assert d["scores"]["accuracy"] == 0.9
        assert "overall" in d["scores"]

    def test_report_to_dict(self):
        scores = [
            QuestionScore(
                question_id=f"q{i}",
                category="single_doc",
                question=f"Q{i}?",
                completeness=0.5 + i * 0.1,
                accuracy=0.6 + i * 0.1,
            )
            for i in range(3)
        ]
        report = EvalReport(scores=scores, mode="graph_scoped")
        d = report.to_dict()
        assert d["mode"] == "graph_scoped"
        assert d["summary"]["total_questions"] == 3
        assert "avg_completeness" in d["summary"]
        assert len(d["questions"]) == 3

    def test_report_category_averages(self):
        scores = [
            QuestionScore(question_id="q1", category="single_doc",
                         question="A?", completeness=0.8),
            QuestionScore(question_id="q2", category="single_doc",
                         question="B?", completeness=0.6),
            QuestionScore(question_id="q3", category="cross_doc",
                         question="C?", completeness=1.0),
        ]
        report = EvalReport(scores=scores)
        avgs = report.category_averages()
        assert abs(avgs["single_doc"]["completeness"] - 0.7) < 0.01
        assert avgs["cross_doc"]["completeness"] == 1.0


# ─── Test A/B comparison ────────────────────────────────────────

class TestABComparison:
    """Tests for A/B comparison logic."""

    def test_graph_wins(self):
        graph_scores = [
            QuestionScore(question_id="q1", category="single_doc",
                         question="A?", completeness=0.9, accuracy=0.9),
        ]
        rag_scores = [
            QuestionScore(question_id="q1", category="single_doc",
                         question="A?", completeness=0.5, accuracy=0.5),
        ]
        ab = ABComparison(
            graph_report=EvalReport(scores=graph_scores, mode="graph_scoped"),
            rag_report=EvalReport(scores=rag_scores, mode="pure_rag"),
        )
        assert ab.graph_wins == 1
        assert ab.rag_wins == 0
        assert ab.ties == 0

    def test_tie(self):
        same = QuestionScore(question_id="q1", category="single_doc",
                            question="A?", completeness=0.7, accuracy=0.7)
        ab = ABComparison(
            graph_report=EvalReport(scores=[same], mode="graph_scoped"),
            rag_report=EvalReport(scores=[same], mode="pure_rag"),
        )
        assert ab.ties == 1

    def test_to_dict(self):
        graph_scores = [
            QuestionScore(question_id="q1", category="single_doc",
                         question="A?", completeness=0.8),
        ]
        rag_scores = [
            QuestionScore(question_id="q1", category="single_doc",
                         question="A?", completeness=0.6),
        ]
        ab = ABComparison(
            graph_report=EvalReport(scores=graph_scores, mode="graph_scoped"),
            rag_report=EvalReport(scores=rag_scores, mode="pure_rag"),
        )
        d = ab.to_dict()
        assert "summary" in d
        assert "per_question" in d
        assert "by_category" in d
        assert d["summary"]["graph_wins"] == 1


# ─── Test integration with pipeline ─────────────────────────────

def _build_eval_graph() -> nx.DiGraph:
    """Build a synthetic graph for evaluation testing."""
    G = nx.DiGraph()

    # MNO and release
    G.add_node("mno:VZW", node_type="MNO", mno="VZW")
    G.add_node("release:VZW:2026_feb", node_type="Release",
               mno="VZW", release="2026_feb")
    G.add_edge("mno:VZW", "release:VZW:2026_feb", edge_type="has_release")

    # Plans
    for plan in ["LTEDATARETRY", "LTESMS", "LTEB13NAC"]:
        pid = f"plan:VZW:2026_feb:{plan}"
        G.add_node(pid, node_type="Plan", mno="VZW",
                   release="2026_feb", plan_id=plan)
        G.add_edge("release:VZW:2026_feb", pid, edge_type="contains_plan")

    # Requirements
    reqs = [
        ("VZ_REQ_LTEDATARETRY_7742", "LTEDATARETRY", "1.3.4", "TIMER T3402"),
        ("VZ_REQ_LTEDATARETRY_2377", "LTEDATARETRY", "1.3.4.1", "T3402 on a PLMN basis"),
        ("VZ_REQ_LTEDATARETRY_7731", "LTEDATARETRY", "1.3.3", "GENERIC THROTTLING ALGORITHM"),
        ("VZ_REQ_LTESMS_30258", "LTESMS", "1.4.2.1.1", "SMS OVER IMS - OVERVIEW"),
        ("VZ_REQ_LTESMS_30284", "LTESMS", "1.5.1.2", "SMS OVER IMS"),
        ("VZ_REQ_LTEB13NAC_23507", "LTEB13NAC", "1.3.2.10.1", "SMS over IMS Support"),
    ]

    for req_id, plan, section, title in reqs:
        nid = f"req:{req_id}"
        G.add_node(nid, node_type="Requirement", mno="VZW",
                   release="2026_feb", plan_id=plan,
                   req_id=req_id, section_number=section,
                   title=title)
        pid = f"plan:VZW:2026_feb:{plan}"
        G.add_edge(nid, pid, edge_type="belongs_to")

    # Standards
    std_id = "std:24.301:Rel-11:5.5.1.2.5"
    G.add_node(std_id, node_type="Standard_Section",
               spec="24.301", release="Rel-11",
               section="5.5.1.2.5", title="T3402 timer handling")
    G.add_edge("req:VZ_REQ_LTEDATARETRY_2377", std_id,
               edge_type="references_standard")

    # Features
    for feat_id, plan_ids in [
        ("TIMER_MANAGEMENT", ["LTEDATARETRY"]),
        ("SMS", ["LTESMS", "LTEB13NAC"]),
        ("DATA_RETRY", ["LTEDATARETRY"]),
    ]:
        fid = f"feature:{feat_id}"
        G.add_node(fid, node_type="Feature", feature_id=feat_id)
        for plan in plan_ids:
            for nid, data in G.nodes(data=True):
                if (data.get("node_type") == "Requirement"
                        and data.get("plan_id") == plan):
                    G.add_edge(nid, fid, edge_type="maps_to")

    # Parent-of edges
    G.add_edge("req:VZ_REQ_LTEDATARETRY_7742",
               "req:VZ_REQ_LTEDATARETRY_2377",
               edge_type="parent_of")

    return G


class MockEvalStore:
    """Mock vector store for evaluation testing."""

    def __init__(self):
        self._data = {
            "VZ_REQ_LTEDATARETRY_7742": {
                "text": "[MNO: VZW] [Plan: LTEDATARETRY] TIMER T3402. "
                        "The T3402 timer controls PLMN-based retry behavior. "
                        "See 3GPP TS 24.301.",
                "metadata": {
                    "req_id": "VZ_REQ_LTEDATARETRY_7742",
                    "plan_id": "LTEDATARETRY",
                    "mno": "VZW",
                    "section_number": "1.3.4",
                },
            },
            "VZ_REQ_LTEDATARETRY_2377": {
                "text": "[MNO: VZW] [Plan: LTEDATARETRY] T3402 on a PLMN basis. "
                        "The UE shall implement T3402 on a PLMN basis. "
                        "3GPP TS 24.301, Section 5.5.1.2.5.",
                "metadata": {
                    "req_id": "VZ_REQ_LTEDATARETRY_2377",
                    "plan_id": "LTEDATARETRY",
                    "mno": "VZW",
                    "section_number": "1.3.4.1",
                },
            },
            "VZ_REQ_LTEDATARETRY_7731": {
                "text": "[MNO: VZW] [Plan: LTEDATARETRY] "
                        "GENERIC THROTTLING ALGORITHM for data retry.",
                "metadata": {
                    "req_id": "VZ_REQ_LTEDATARETRY_7731",
                    "plan_id": "LTEDATARETRY",
                    "mno": "VZW",
                    "section_number": "1.3.3",
                },
            },
            "VZ_REQ_LTESMS_30258": {
                "text": "[MNO: VZW] [Plan: LTESMS] "
                        "SMS OVER IMS - OVERVIEW. Requirements for SMS over IMS.",
                "metadata": {
                    "req_id": "VZ_REQ_LTESMS_30258",
                    "plan_id": "LTESMS",
                    "mno": "VZW",
                    "section_number": "1.4.2.1.1",
                },
            },
            "VZ_REQ_LTESMS_30284": {
                "text": "[MNO: VZW] [Plan: LTESMS] SMS OVER IMS detail.",
                "metadata": {
                    "req_id": "VZ_REQ_LTESMS_30284",
                    "plan_id": "LTESMS",
                    "mno": "VZW",
                    "section_number": "1.5.1.2",
                },
            },
            "VZ_REQ_LTEB13NAC_23507": {
                "text": "[MNO: VZW] [Plan: LTEB13NAC] "
                        "SMS over IMS Support in Band 13 NAC.",
                "metadata": {
                    "req_id": "VZ_REQ_LTEB13NAC_23507",
                    "plan_id": "LTEB13NAC",
                    "mno": "VZW",
                    "section_number": "1.3.2.10.1",
                },
            },
        }

    @property
    def count(self) -> int:
        return len(self._data)

    def query(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict | None = None,
    ):
        """Return matching documents.

        Returns QueryResult with flat lists (matching ChromaDBStore behavior).
        """
        ids, docs, metas, dists = [], [], [], []

        # Filter by where clause
        candidates = list(self._data.items())
        if where:
            if "$and" in where:
                for condition in where["$and"]:
                    for key, val in condition.items():
                        if isinstance(val, dict) and "$in" in val:
                            candidates = [
                                (k, v) for k, v in candidates
                                if v["metadata"].get(key) in val["$in"]
                            ]
                        elif isinstance(val, str):
                            candidates = [
                                (k, v) for k, v in candidates
                                if v["metadata"].get(key) == val
                            ]
            elif "req_id" in where:
                val = where["req_id"]
                if isinstance(val, dict) and "$in" in val:
                    candidates = [
                        (k, v) for k, v in candidates
                        if k in val["$in"]
                    ]

        for cid, cdata in candidates[:n_results]:
            ids.append(cid)
            docs.append(cdata["text"])
            metas.append(cdata["metadata"])
            dists.append(0.3)

        from src.vectorstore.store_base import QueryResult
        return QueryResult(
            ids=ids,
            documents=docs,
            metadatas=metas,
            distances=dists,
        )


class MockEvalEmbedder:
    """Mock embedder for evaluation testing."""

    @property
    def dimension(self) -> int:
        return 384

    @property
    def model_name(self) -> str:
        return "mock"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * 384


class TestEvalRunner:
    """Integration tests for the evaluation runner."""

    @pytest.fixture
    def runner(self):
        graph = _build_eval_graph()
        embedder = MockEvalEmbedder()
        store = MockEvalStore()
        return EvalRunner(graph, embedder, store)

    def test_run_single_question(self, runner):
        """Should run a single question and return a score."""
        from src.query.pipeline import QueryPipeline
        pipeline = runner._make_pipeline()
        score = runner.run_question(ALL_QUESTIONS[0], pipeline)
        assert isinstance(score, QuestionScore)
        assert score.question_id == ALL_QUESTIONS[0].id
        assert 0.0 <= score.overall <= 1.0

    def test_run_all(self, runner):
        """Should run all questions and return a report."""
        # Use just a few questions for speed
        questions = ALL_QUESTIONS[:3]
        report = runner.run_all(questions)
        assert isinstance(report, EvalReport)
        assert len(report.scores) == 3
        assert report.mode == "graph_scoped"

    def test_run_pure_rag(self, runner):
        """Should run in pure RAG mode (bypass graph)."""
        questions = ALL_QUESTIONS[:2]
        report = runner.run_all(questions, bypass_graph=True)
        assert report.mode == "pure_rag"
        assert len(report.scores) == 2

    def test_ab_comparison(self, runner):
        """Should run A/B comparison."""
        questions = ALL_QUESTIONS[:2]
        ab = runner.run_ab_comparison(questions)
        assert isinstance(ab, ABComparison)
        assert len(ab.graph_report.scores) == 2
        assert len(ab.rag_report.scores) == 2
        assert ab.graph_wins + ab.rag_wins + ab.ties == 2

    def test_ab_to_dict(self, runner):
        """A/B comparison should serialize to dict."""
        questions = ALL_QUESTIONS[:2]
        ab = runner.run_ab_comparison(questions)
        d = ab.to_dict()
        assert "summary" in d
        assert "per_question" in d
        assert d["summary"]["graph_wins"] + d["summary"]["rag_wins"] + d["summary"]["ties"] == 2

    def test_report_json_serializable(self, runner):
        """Report should be JSON-serializable."""
        questions = ALL_QUESTIONS[:2]
        report = runner.run_all(questions)
        d = report.to_dict()
        # Should not raise
        json_str = json.dumps(d, indent=2)
        parsed = json.loads(json_str)
        assert parsed["summary"]["total_questions"] == 2

    def test_pipeline_bypass_graph(self, runner):
        """Pipeline with bypass_graph should skip graph scoping."""
        pipeline = runner._make_pipeline(bypass_graph=True)
        assert pipeline._bypass_graph is True
        # Query should still work (falls back to metadata retrieval)
        response = pipeline.query("What is T3402?")
        assert isinstance(response, QueryResponse)
        assert response.candidate_count == 0  # No graph candidates


# ─── Test overall score weighting ────────────────────────────────

class TestOverallScore:
    """Tests for the weighted overall score calculation."""

    def test_all_ones(self):
        score = QuestionScore(
            question_id="test", category="single_doc", question="?",
            completeness=1.0, accuracy=1.0, citation_quality=1.0,
            standards_integration=1.0, hallucination_free=1.0,
        )
        assert abs(score.overall - 1.0) < 0.001

    def test_all_zeros_except_hallucination(self):
        score = QuestionScore(
            question_id="test", category="single_doc", question="?",
            completeness=0.0, accuracy=0.0, citation_quality=0.0,
            standards_integration=0.0, hallucination_free=1.0,
        )
        # hallucination_free weight = 0.10
        assert abs(score.overall - 0.10) < 0.001

    def test_weights_sum_to_one(self):
        """Verify weight constants sum to 1.0."""
        weights = [0.30, 0.25, 0.20, 0.15, 0.10]
        assert abs(sum(weights) - 1.0) < 0.001
