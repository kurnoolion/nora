"""CLI for vector store construction (PoC Step 9).

Usage:
    # Build with defaults (all-MiniLM-L6-v2, ChromaDB, cosine)
    python -m src.vectorstore.vectorstore_cli

    # Build with a config file
    python -m src.vectorstore.vectorstore_cli --config configs/vs_experiment1.json

    # Override specific settings via CLI flags
    python -m src.vectorstore.vectorstore_cli --model all-mpnet-base-v2 --metric l2

    # Force rebuild (clear existing data)
    python -m src.vectorstore.vectorstore_cli --rebuild

    # Inspect existing store
    python -m src.vectorstore.vectorstore_cli --info

    # Save current config for reproducibility
    python -m src.vectorstore.vectorstore_cli --save-config configs/vs_baseline.json

    # Test query against the store
    python -m src.vectorstore.vectorstore_cli --query "T3402 timer behavior"
    python -m src.vectorstore.vectorstore_cli --query "attach reject cause codes" --n-results 5 --filter-plan LTEDATARETRY
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.vectorstore.config import VectorStoreConfig
from src.vectorstore.builder import VectorStoreBuilder

logger = logging.getLogger(__name__)


def _create_embedder(config: VectorStoreConfig):
    """Create an embedding provider from config."""
    if config.embedding_provider == "sentence-transformers":
        from src.vectorstore.embedding_st import SentenceTransformerEmbedder

        return SentenceTransformerEmbedder(
            model_name=config.embedding_model,
            device=config.embedding_device,
            batch_size=config.embedding_batch_size,
            normalize=config.normalize_embeddings,
        )
    else:
        raise ValueError(
            f"Unknown embedding provider '{config.embedding_provider}'. "
            f"Options: 'sentence-transformers'"
        )


def _create_store(config: VectorStoreConfig):
    """Create a vector store backend from config."""
    if config.vector_store_backend == "chromadb":
        from src.vectorstore.store_chroma import ChromaDBStore

        return ChromaDBStore(
            persist_directory=config.persist_directory,
            collection_name=config.collection_name,
            distance_metric=config.distance_metric,
        )
    else:
        raise ValueError(
            f"Unknown vector store backend '{config.vector_store_backend}'. "
            f"Options: 'chromadb'"
        )


def _build_config(args: argparse.Namespace) -> VectorStoreConfig:
    """Build config from file + CLI overrides."""
    if args.config:
        config = VectorStoreConfig.load_json(Path(args.config))
        logger.info(f"Loaded config from {args.config}")
    else:
        config = VectorStoreConfig()

    # CLI overrides
    if args.model:
        config.embedding_model = args.model
    if args.provider:
        config.embedding_provider = args.provider
    if args.backend:
        config.vector_store_backend = args.backend
    if args.metric:
        config.distance_metric = args.metric
    if args.collection:
        config.collection_name = args.collection
    if args.batch_size:
        config.embedding_batch_size = args.batch_size
    if args.device:
        config.embedding_device = args.device
    if args.persist_dir:
        config.persist_directory = args.persist_dir
    if args.no_normalize:
        config.normalize_embeddings = False

    return config


def cmd_build(args: argparse.Namespace) -> None:
    """Build the vector store."""
    config = _build_config(args)

    # Log effective config
    logger.info("Effective configuration:")
    for k, v in config.to_dict().items():
        if k != "extra":
            logger.info(f"  {k}: {v}")

    # Save config if requested
    if args.save_config:
        config.save_json(Path(args.save_config))
        logger.info(f"Config saved to {args.save_config}")

    trees_dir = Path(args.trees_dir)
    taxonomy_path = Path(args.taxonomy) if args.taxonomy else None

    embedder = _create_embedder(config)
    store = _create_store(config)
    builder = VectorStoreBuilder(embedder, store, config)

    stats = builder.build(trees_dir, taxonomy_path, rebuild=args.rebuild)

    # Print summary
    print(f"\n{'=' * 60}")
    print("Vector Store Build Complete")
    print(f"{'=' * 60}")
    print(f"Total chunks:     {stats.total_chunks}")
    print(f"Embedding model:  {stats.embedding_model} ({stats.embedding_dimension}d)")
    print(f"Vector store:     {stats.vector_store_backend}")
    print(f"Distance metric:  {stats.distance_metric}")
    print(f"Collection:       {stats.collection_name}")
    print(f"\nChunks per plan:")
    for pid, count in sorted(stats.chunks_by_plan.items()):
        print(f"  {pid:<20s} {count:>4d}")

    # Save stats
    stats_path = Path(config.persist_directory) / "build_stats.json"
    stats.save_json(stats_path)
    print(f"\nStats saved to {stats_path}")

    # Save config alongside the store
    config_path = Path(config.persist_directory) / "config.json"
    config.save_json(config_path)
    print(f"Config saved to {config_path}")


def cmd_info(args: argparse.Namespace) -> None:
    """Show info about an existing vector store."""
    config = _build_config(args)

    # Load saved config if it exists
    saved_config_path = Path(config.persist_directory) / "config.json"
    if saved_config_path.exists():
        saved = VectorStoreConfig.load_json(saved_config_path)
        print(f"Saved configuration ({saved_config_path}):")
        for k, v in saved.to_dict().items():
            if k != "extra":
                print(f"  {k}: {v}")
        # Use the saved config for the store
        config = saved

    store = _create_store(config)

    print(f"\nCollection: {config.collection_name}")
    print(f"Documents:  {store.count}")

    # Load build stats if available
    stats_path = Path(config.persist_directory) / "build_stats.json"
    if stats_path.exists():
        with open(stats_path, "r") as f:
            stats = json.load(f)
        print(f"\nBuild stats:")
        for k, v in stats.items():
            if k != "chunks_by_plan":
                print(f"  {k}: {v}")
        if "chunks_by_plan" in stats:
            print(f"  Chunks per plan:")
            for pid, count in sorted(stats["chunks_by_plan"].items()):
                print(f"    {pid:<20s} {count:>4d}")


def cmd_query(args: argparse.Namespace) -> None:
    """Run a test query against the vector store."""
    config = _build_config(args)

    # Load saved config for consistent embedder
    saved_config_path = Path(config.persist_directory) / "config.json"
    if saved_config_path.exists():
        config = VectorStoreConfig.load_json(saved_config_path)

    embedder = _create_embedder(config)
    store = _create_store(config)

    if store.count == 0:
        print("Vector store is empty. Run build first.")
        return

    # Build metadata filter
    where = None
    if args.filter_plan:
        where = {"plan_id": args.filter_plan}
    elif args.filter_mno:
        where = {"mno": args.filter_mno}

    n_results = args.n_results or config.default_n_results

    # Embed query
    query_embedding = embedder.embed_query(args.query)

    # Search
    results = store.query(query_embedding, n_results=n_results, where=where)

    # Display
    print(f"\nQuery: \"{args.query}\"")
    if where:
        print(f"Filter: {where}")
    print(f"Results: {len(results.ids)}")
    print(f"{'=' * 70}")

    for i, (rid, doc, meta, dist) in enumerate(
        zip(results.ids, results.documents, results.metadatas, results.distances)
    ):
        print(f"\n--- Result {i + 1} (distance: {dist:.4f}) ---")
        print(f"ID:      {rid}")
        print(f"Plan:    {meta.get('plan_id', '?')}")
        print(f"Section: {meta.get('section_number', '?')}")
        print(f"Req ID:  {meta.get('req_id', '?')}")
        # Show first 300 chars of document text
        preview = doc[:300].replace("\n", " ")
        if len(doc) > 300:
            preview += "..."
        print(f"Text:    {preview}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NORA — Vector store construction for network operator requirements"
    )

    # Data paths
    parser.add_argument(
        "--trees-dir", default="data/parsed",
        help="Directory with parsed *_tree.json files (default: data/parsed)",
    )
    parser.add_argument(
        "--taxonomy", default="data/taxonomy/taxonomy.json",
        help="Path to taxonomy.json (default: data/taxonomy/taxonomy.json)",
    )

    # Config
    parser.add_argument(
        "--config", help="Path to JSON config file",
    )
    parser.add_argument(
        "--save-config", help="Save effective config to this path",
    )

    # Embedding overrides
    parser.add_argument("--model", help="Embedding model name")
    parser.add_argument("--provider", help="Embedding provider (sentence-transformers)")
    parser.add_argument("--device", help="Device for embedding (cpu, cuda, mps)")
    parser.add_argument("--batch-size", type=int, help="Embedding batch size")
    parser.add_argument("--no-normalize", action="store_true", help="Disable L2 normalization")

    # Store overrides
    parser.add_argument("--backend", help="Vector store backend (chromadb)")
    parser.add_argument("--metric", help="Distance metric (cosine, l2, ip)")
    parser.add_argument("--collection", help="Collection name")
    parser.add_argument("--persist-dir", help="Persistence directory")

    # Actions
    parser.add_argument("--rebuild", action="store_true", help="Clear and rebuild")
    parser.add_argument("--info", action="store_true", help="Show store info")
    parser.add_argument("--query", help="Run a test query")
    parser.add_argument("--n-results", type=int, help="Number of query results")
    parser.add_argument("--filter-plan", help="Filter query by plan_id")
    parser.add_argument("--filter-mno", help="Filter query by MNO")

    args = parser.parse_args()

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.info:
        cmd_info(args)
    elif args.query:
        cmd_query(args)
    else:
        cmd_build(args)


if __name__ == "__main__":
    main()
