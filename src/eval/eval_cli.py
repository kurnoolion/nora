"""CLI for the evaluation framework (PoC Step 11).

Usage:
    # Run all evaluation questions (graph-scoped mode)
    python -m src.eval.eval_cli

    # Run A/B comparison (graph-scoped vs pure RAG)
    python -m src.eval.eval_cli --ab

    # Run specific category only
    python -m src.eval.eval_cli --category cross_doc

    # Save report to JSON
    python -m src.eval.eval_cli --output data/eval/report.json

    # Verbose mode
    python -m src.eval.eval_cli --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.eval.questions import ALL_QUESTIONS, QUESTIONS_BY_CATEGORY
from src.eval.runner import EvalRunner, ABComparison
from src.eval.metrics import EvalReport
from src.query.pipeline import load_graph

logger = logging.getLogger(__name__)


def _create_runner(args: argparse.Namespace) -> EvalRunner:
    """Create the evaluation runner."""
    # Load graph
    graph_path = Path(args.graph)
    if not graph_path.exists():
        print(f"Error: Graph not found at {graph_path}")
        print("Run: python -m src.graph.graph_cli")
        sys.exit(1)

    graph = load_graph(graph_path)

    # Load vector store config
    vs_config_path = Path(args.vectorstore_dir) / "config.json"
    if vs_config_path.exists():
        from src.vectorstore.config import VectorStoreConfig
        vs_config = VectorStoreConfig.load_json(vs_config_path)
    else:
        from src.vectorstore.config import VectorStoreConfig
        vs_config = VectorStoreConfig(persist_directory=args.vectorstore_dir)

    # Create embedder
    from src.vectorstore.embedding_st import SentenceTransformerEmbedder
    embedder = SentenceTransformerEmbedder(
        model_name=vs_config.embedding_model,
        device=vs_config.embedding_device,
        batch_size=vs_config.embedding_batch_size,
        normalize=vs_config.normalize_embeddings,
    )

    # Create store
    from src.vectorstore.store_chroma import ChromaDBStore
    store = ChromaDBStore(
        persist_directory=vs_config.persist_directory,
        collection_name=vs_config.collection_name,
        distance_metric=vs_config.distance_metric,
    )

    if store.count == 0:
        print("Error: Vector store is empty.")
        print("Run: python -m src.vectorstore.vectorstore_cli")
        sys.exit(1)

    # Create synthesizer based on --llm flag
    synthesizer = None
    if args.llm == "ollama":
        from src.llm.ollama_provider import OllamaProvider
        from src.query.synthesizer import LLMSynthesizer

        try:
            llm = OllamaProvider(
                model=args.llm_model,
                timeout=args.llm_timeout,
            )
            synthesizer = LLMSynthesizer(llm)
            print(f"Using Ollama LLM: {args.llm_model}")
        except ConnectionError as e:
            print(f"Warning: {e}")
            print("Falling back to mock synthesizer.")

    return EvalRunner(
        graph=graph,
        embedder=embedder,
        store=store,
        synthesizer=synthesizer,
        top_k=args.top_k,
    )


def _display_report(report: EvalReport) -> None:
    """Display an evaluation report."""
    print(f"\n{'=' * 70}")
    print(f"EVALUATION REPORT — Mode: {report.mode}")
    print(f"{'=' * 70}")

    # Overall summary
    print(f"\nOverall ({len(report.scores)} questions):")
    print(f"  Completeness:          {report.avg_completeness:.1%}")
    print(f"  Accuracy:              {report.avg_accuracy:.1%}")
    print(f"  Citation quality:      {report.avg_citation_quality:.1%}")
    print(f"  Standards integration: {report.avg_standards_integration:.1%}")
    print(f"  Hallucination-free:    {report.avg_hallucination_free:.1%}")
    print(f"  Overall score:         {report.avg_overall:.1%}")

    # TDD targets
    print(f"\n{'─' * 70}")
    print("TDD 9.4 Targets:")
    _check_target("Completeness (cross-doc >80%)", report, "cross_doc", "completeness", 0.80)
    _check_target("Accuracy (>90%)", report, None, "accuracy", 0.90)
    _check_target("Citation quality (100%)", report, None, "citation_quality", 1.00)
    _check_target("Standards integration (>80%)", report, "standards_comparison", "standards_integration", 0.80)
    _check_target("No hallucination (100%)", report, None, "hallucination_free", 1.00)

    # Per-category breakdown
    print(f"\n{'─' * 70}")
    print("By category:")
    for cat, avgs in report.category_averages().items():
        n = avgs["count"]
        print(f"\n  {cat} ({n} questions):")
        print(f"    Completeness:    {avgs['completeness']:.1%}")
        print(f"    Accuracy:        {avgs['accuracy']:.1%}")
        print(f"    Citations:       {avgs['citation_quality']:.1%}")
        print(f"    Standards:       {avgs['standards_integration']:.1%}")
        print(f"    Overall:         {avgs['overall']:.1%}")

    # Per-question details
    print(f"\n{'─' * 70}")
    print("Per question:")
    for s in report.scores:
        status = "PASS" if s.overall >= 0.6 else "WARN" if s.overall >= 0.3 else "FAIL"
        print(
            f"  [{status}] {s.question_id:15s} "
            f"overall={s.overall:.0%}  "
            f"compl={s.completeness:.0%}  "
            f"acc={s.accuracy:.0%}  "
            f"cite={s.citation_quality:.0%}  "
            f"std={s.standards_integration:.0%}"
        )
        if s.hallucinated_req_ids:
            print(f"        HALLUCINATED: {s.hallucinated_req_ids}")


def _check_target(
    label: str,
    report: EvalReport,
    category: str | None,
    metric: str,
    target: float,
) -> None:
    """Check if a metric meets its TDD target."""
    if category:
        cats = report.category_averages()
        if category in cats:
            value = cats[category].get(metric, 0.0)
        else:
            print(f"  [ ? ] {label} — no {category} questions")
            return
    else:
        value = getattr(report, f"avg_{metric}", 0.0)

    met = value >= target
    mark = "PASS" if met else "FAIL"
    print(f"  [{mark}] {label}: {value:.1%} (target: {target:.0%})")


def _display_ab_comparison(ab: ABComparison) -> None:
    """Display A/B comparison results."""
    print(f"\n{'=' * 70}")
    print("A/B COMPARISON: Graph-Scoped vs Pure RAG")
    print(f"{'=' * 70}")

    print(f"\nGraph-scoped overall: {ab.graph_report.avg_overall:.1%}")
    print(f"Pure RAG overall:     {ab.rag_report.avg_overall:.1%}")
    delta = ab.graph_report.avg_overall - ab.rag_report.avg_overall
    direction = "better" if delta > 0 else "worse" if delta < 0 else "same"
    print(f"Delta:                {delta:+.1%} (graph is {direction})")

    print(f"\nWins: graph={ab.graph_wins}, rag={ab.rag_wins}, ties={ab.ties}")

    # Per-category
    print(f"\n{'─' * 70}")
    print("By category:")
    for cat, vals in ab._category_comparison().items():
        g = vals["graph_overall"]
        r = vals["rag_overall"]
        d = vals["delta"]
        winner = "graph" if d > 0 else "rag" if d < 0 else "tie"
        print(f"  {cat:25s}  graph={g:.1%}  rag={r:.1%}  delta={d:+.1%}  ({winner})")

    # Per-question
    print(f"\n{'─' * 70}")
    print("Per question:")
    for item in ab.to_dict()["per_question"]:
        print(
            f"  {item['question_id']:15s}  "
            f"graph={item['graph_overall']:.0%}  "
            f"rag={item['rag_overall']:.0%}  "
            f"delta={item['delta']:+.0%}  "
            f"({item['winner']})"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the query pipeline on test questions"
    )

    # Data paths
    parser.add_argument(
        "--graph", default="data/graph/knowledge_graph.json",
        help="Path to knowledge graph JSON",
    )
    parser.add_argument(
        "--vectorstore-dir", default="data/vectorstore",
        help="Path to vector store directory",
    )

    # Evaluation mode
    parser.add_argument(
        "--ab", action="store_true",
        help="Run A/B comparison (graph-scoped vs pure RAG)",
    )
    parser.add_argument(
        "--category", "-c",
        choices=list(QUESTIONS_BY_CATEGORY.keys()),
        help="Run only questions in this category",
    )

    # Pipeline settings
    parser.add_argument(
        "--top-k", type=int, default=10,
        help="Number of chunks to retrieve (default: 10)",
    )

    # LLM settings
    parser.add_argument(
        "--llm", choices=["mock", "ollama"], default="mock",
        help="LLM backend: mock (default) or ollama (local Gemma)",
    )
    parser.add_argument(
        "--llm-model", default="gemma4:e4b",
        help="Ollama model name (default: gemma4:e4b)",
    )
    parser.add_argument(
        "--llm-timeout", type=int, default=300,
        help="LLM request timeout in seconds (default: 300)",
    )

    # Output
    parser.add_argument("--output", "-o", help="Save report to JSON file")
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show detailed pipeline output",
    )

    args = parser.parse_args()

    # Set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Select questions
    if args.category:
        questions = QUESTIONS_BY_CATEGORY.get(args.category, [])
        if not questions:
            print(f"No questions for category: {args.category}")
            sys.exit(1)
    else:
        questions = ALL_QUESTIONS

    # Create runner
    runner = _create_runner(args)

    if args.ab:
        # A/B comparison
        comparison = runner.run_ab_comparison(questions)
        _display_ab_comparison(comparison)

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(comparison.to_dict(), f, indent=2)
            print(f"\nReport saved to {output_path}")
    else:
        # Single-mode evaluation
        report = runner.run_all(questions)
        _display_report(report)

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(report.to_dict(), f, indent=2)
            print(f"\nReport saved to {output_path}")


if __name__ == "__main__":
    main()
