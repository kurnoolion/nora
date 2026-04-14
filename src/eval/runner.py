"""Evaluation runner (TDD 9.4, Step 11).

Runs test questions through the query pipeline and computes metrics.
Supports two modes for A/B comparison:
  - graph_scoped: Normal pipeline (graph scoping → targeted RAG)
  - pure_rag: Bypass graph scoping, use metadata-only RAG retrieval

Usage:
    runner = EvalRunner(graph, embedder, store)
    report = runner.run_all(questions)
    report_ab = runner.run_ab_comparison(questions)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from src.eval.questions import EvalQuestion, ALL_QUESTIONS
from src.eval.metrics import (
    QuestionScore,
    EvalReport,
    score_question,
)
from src.query.pipeline import QueryPipeline
from src.query.schema import (
    QueryResponse,
    CandidateSet,
    ScopedQuery,
)

logger = logging.getLogger(__name__)


@dataclass
class ABComparison:
    """A/B comparison between graph-scoped and pure-RAG."""

    graph_report: EvalReport
    rag_report: EvalReport

    @property
    def graph_wins(self) -> int:
        """Questions where graph-scoped outperforms pure RAG."""
        wins = 0
        for gs, pr in zip(
            self.graph_report.scores, self.rag_report.scores
        ):
            if gs.overall > pr.overall:
                wins += 1
        return wins

    @property
    def rag_wins(self) -> int:
        """Questions where pure RAG outperforms graph-scoped."""
        wins = 0
        for gs, pr in zip(
            self.graph_report.scores, self.rag_report.scores
        ):
            if pr.overall > gs.overall:
                wins += 1
        return wins

    @property
    def ties(self) -> int:
        return len(self.graph_report.scores) - self.graph_wins - self.rag_wins

    def to_dict(self) -> dict:
        return {
            "summary": {
                "graph_wins": self.graph_wins,
                "rag_wins": self.rag_wins,
                "ties": self.ties,
                "graph_avg_overall": round(
                    self.graph_report.avg_overall, 3
                ),
                "rag_avg_overall": round(
                    self.rag_report.avg_overall, 3
                ),
                "delta": round(
                    self.graph_report.avg_overall
                    - self.rag_report.avg_overall,
                    3,
                ),
            },
            "per_question": [
                {
                    "question_id": gs.question_id,
                    "category": gs.category,
                    "graph_overall": round(gs.overall, 3),
                    "rag_overall": round(pr.overall, 3),
                    "delta": round(gs.overall - pr.overall, 3),
                    "winner": (
                        "graph"
                        if gs.overall > pr.overall
                        else "rag"
                        if pr.overall > gs.overall
                        else "tie"
                    ),
                }
                for gs, pr in zip(
                    self.graph_report.scores, self.rag_report.scores
                )
            ],
            "by_category": self._category_comparison(),
            "graph_report": self.graph_report.to_dict(),
            "rag_report": self.rag_report.to_dict(),
        }

    def _category_comparison(self) -> dict:
        g_cats = self.graph_report.category_averages()
        r_cats = self.rag_report.category_averages()
        result = {}
        for cat in g_cats:
            g = g_cats[cat]
            r = r_cats.get(cat, {})
            result[cat] = {
                "graph_overall": round(g.get("overall", 0), 3),
                "rag_overall": round(r.get("overall", 0), 3),
                "delta": round(
                    g.get("overall", 0) - r.get("overall", 0), 3
                ),
            }
        return result


class EvalRunner:
    """Runs evaluation questions through the query pipeline."""

    def __init__(
        self,
        graph,
        embedder,
        store,
        analyzer=None,
        synthesizer=None,
        top_k: int = 10,
        max_depth: int | None = None,
        max_context_chars: int = 30000,
    ) -> None:
        self._graph = graph
        self._embedder = embedder
        self._store = store
        self._analyzer = analyzer
        self._synthesizer = synthesizer
        self._top_k = top_k
        self._max_depth = max_depth
        self._max_context_chars = max_context_chars

    def _make_pipeline(self, bypass_graph: bool = False) -> QueryPipeline:
        """Create a pipeline, optionally bypassing graph scoping."""
        pipeline = QueryPipeline(
            graph=self._graph,
            embedder=self._embedder,
            store=self._store,
            analyzer=self._analyzer,
            synthesizer=self._synthesizer,
            top_k=self._top_k,
            max_depth=self._max_depth,
            max_context_chars=self._max_context_chars,
        )
        if bypass_graph:
            pipeline._bypass_graph = True
        return pipeline

    def run_question(
        self,
        question: EvalQuestion,
        pipeline: QueryPipeline,
    ) -> QuestionScore:
        """Run a single question and score it."""
        logger.info(
            f"Running [{question.id}] ({question.category}): "
            f"{question.question[:60]}..."
        )

        start = time.time()
        response = pipeline.query(question.question)
        elapsed = time.time() - start

        score = score_question(question, response)

        logger.info(
            f"  [{question.id}] overall={score.overall:.3f} "
            f"completeness={score.completeness:.3f} "
            f"accuracy={score.accuracy:.3f} "
            f"citations={score.citation_quality:.3f} "
            f"({elapsed:.2f}s)"
        )

        return score

    def run_all(
        self,
        questions: list[EvalQuestion] | None = None,
        bypass_graph: bool = False,
    ) -> EvalReport:
        """Run all questions and return an evaluation report."""
        if questions is None:
            questions = ALL_QUESTIONS

        mode = "pure_rag" if bypass_graph else "graph_scoped"
        pipeline = self._make_pipeline(bypass_graph=bypass_graph)

        logger.info(
            f"Running evaluation: {len(questions)} questions, mode={mode}"
        )

        report = EvalReport(mode=mode)
        for q in questions:
            score = self.run_question(q, pipeline)
            report.scores.append(score)

        logger.info(
            f"Evaluation complete ({mode}): "
            f"overall={report.avg_overall:.3f}, "
            f"completeness={report.avg_completeness:.3f}, "
            f"accuracy={report.avg_accuracy:.3f}"
        )

        return report

    def run_ab_comparison(
        self,
        questions: list[EvalQuestion] | None = None,
    ) -> ABComparison:
        """Run A/B comparison: graph-scoped vs pure RAG."""
        if questions is None:
            questions = ALL_QUESTIONS

        logger.info(
            f"A/B comparison: {len(questions)} questions"
        )

        # Run with graph scoping
        logger.info("── Mode A: Graph-scoped retrieval ──")
        graph_report = self.run_all(questions, bypass_graph=False)

        # Run without graph scoping (pure RAG)
        logger.info("── Mode B: Pure RAG retrieval ──")
        rag_report = self.run_all(questions, bypass_graph=True)

        comparison = ABComparison(
            graph_report=graph_report,
            rag_report=rag_report,
        )

        logger.info(
            f"A/B result: graph wins={comparison.graph_wins}, "
            f"rag wins={comparison.rag_wins}, ties={comparison.ties}"
        )

        return comparison
