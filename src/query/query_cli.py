"""CLI for the query pipeline (PoC Step 10).

Usage:
    # Single query
    python -m src.query.query_cli --query "What is the T3402 timer behavior?"

    # Verbose mode (shows all pipeline stages)
    python -m src.query.query_cli --query "T3402 timer" --verbose

    # Interactive mode
    python -m src.query.query_cli --interactive

    # With custom settings
    python -m src.query.query_cli --query "..." --top-k 15 --max-depth 3

    # Save response to file
    python -m src.query.query_cli --query "..." --output response.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.query.pipeline import QueryPipeline, load_graph
from src.query.schema import QueryResponse

logger = logging.getLogger(__name__)


def _create_pipeline(args: argparse.Namespace) -> QueryPipeline:
    """Create the query pipeline with all components."""
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

    # Create analyzer (mock by default, LLM if provider available)
    analyzer = None
    synthesizer = None

    if args.use_llm:
        # User wants real LLM — they need to provide one
        print("Note: --use-llm requires a configured LLM provider.")
        print("Using mock analyzer and synthesizer.")

    # Build pipeline
    pipeline = QueryPipeline(
        graph=graph,
        embedder=embedder,
        store=store,
        analyzer=analyzer,
        synthesizer=synthesizer,
        top_k=args.top_k,
        max_depth=args.max_depth,
        max_context_chars=args.max_context,
    )

    return pipeline


def _display_response(response: QueryResponse, verbose: bool = False) -> None:
    """Display a query response."""
    print(f"\n{'=' * 70}")
    print("ANSWER")
    print(f"{'=' * 70}")
    print(response.answer)

    if response.citations:
        print(f"\n{'─' * 70}")
        print(f"Citations ({len(response.citations)}):")
        for c in response.citations:
            if c.req_id:
                print(f"  - {c.req_id} (Plan: {c.plan_id})")
            if c.spec:
                section = f", Section {c.spec_section}" if c.spec_section else ""
                print(f"  - {c.spec}{section}")

    if verbose:
        print(f"\n{'─' * 70}")
        print("Pipeline stats:")
        print(f"  Candidates from graph: {response.candidate_count}")
        print(f"  Chunks retrieved:      {response.retrieved_count}")
        print(f"  Context tokens (est):  {response.context_tokens_approx}")
        if response.query_intent:
            intent = response.query_intent
            print(f"  Query type:            {intent.query_type.value}")
            print(f"  Features:              {intent.likely_features}")
            print(f"  Plans:                 {intent.plan_ids}")
            print(f"  Entities:              {intent.entities}")


def cmd_query(args: argparse.Namespace) -> None:
    """Run a single query."""
    pipeline = _create_pipeline(args)
    response = pipeline.query(args.query, verbose=args.verbose)
    _display_response(response, verbose=args.verbose)

    if args.output:
        response.save_json(args.output)
        print(f"\nResponse saved to {args.output}")


def cmd_interactive(args: argparse.Namespace) -> None:
    """Run interactive query mode."""
    pipeline = _create_pipeline(args)

    print("\nTelecom Requirements Q&A System")
    print("Type your question, or 'quit' to exit.\n")

    while True:
        try:
            query = input("Q: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            print("Goodbye.")
            break

        response = pipeline.query(query, verbose=args.verbose)
        _display_response(response, verbose=args.verbose)
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query pipeline for telecom requirements"
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

    # Query
    parser.add_argument("--query", "-q", help="Single query to run")
    parser.add_argument(
        "--interactive", "-i", action="store_true",
        help="Run in interactive mode",
    )

    # Pipeline settings
    parser.add_argument(
        "--top-k", type=int, default=10,
        help="Number of chunks to retrieve (default: 10)",
    )
    parser.add_argument(
        "--max-depth", type=int, default=None,
        help="Override graph traversal depth",
    )
    parser.add_argument(
        "--max-context", type=int, default=30000,
        help="Maximum context chars for LLM (default: 30000)",
    )
    parser.add_argument(
        "--use-llm", action="store_true",
        help="Use real LLM for analysis/synthesis (requires provider)",
    )

    # Output
    parser.add_argument("--output", "-o", help="Save response to JSON file")
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show pipeline stage details",
    )

    args = parser.parse_args()

    # Set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.interactive:
        cmd_interactive(args)
    elif args.query:
        cmd_query(args)
    else:
        parser.print_help()
        print("\nUse --query 'your question' or --interactive")


if __name__ == "__main__":
    main()
