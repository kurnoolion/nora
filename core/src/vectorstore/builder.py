"""Vector store builder (TDD 5.9).

Orchestrates the vector store construction pipeline:
  1. Load parsed trees + taxonomy
  2. Build contextualized chunks (ChunkBuilder)
  3. Embed chunks (EmbeddingProvider)
  4. Store in vector store (VectorStoreProvider)

Provider implementations are injected — the builder works with any
embedding model and vector store backend that satisfy the protocols.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from src.vectorstore.chunk_builder import ChunkBuilder, Chunk
from src.vectorstore.config import VectorStoreConfig
from src.vectorstore.embedding_base import EmbeddingProvider
from src.vectorstore.store_base import VectorStoreProvider

logger = logging.getLogger(__name__)


@dataclass
class BuildStats:
    """Statistics from a vector store build."""
    total_chunks: int = 0
    chunks_by_plan: dict[str, int] = field(default_factory=dict)
    embedding_model: str = ""
    embedding_dimension: int = 0
    vector_store_backend: str = ""
    distance_metric: str = ""
    collection_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)


class VectorStoreBuilder:
    """Orchestrates vector store construction from ingestion outputs.

    Usage:
        config = VectorStoreConfig(embedding_model="all-mpnet-base-v2")
        embedder = SentenceTransformerEmbedder(model_name=config.embedding_model)
        store = ChromaDBStore(persist_directory=config.persist_directory)
        builder = VectorStoreBuilder(embedder, store, config)
        stats = builder.build(trees_dir, taxonomy_path)
    """

    def __init__(
        self,
        embedder: EmbeddingProvider,
        store: VectorStoreProvider,
        config: VectorStoreConfig,
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.config = config
        self.chunk_builder = ChunkBuilder(config)

    def build(
        self,
        trees_dir: Path,
        taxonomy_path: Path | None = None,
        rebuild: bool = False,
    ) -> BuildStats:
        """Build the vector store.

        Args:
            trees_dir: Directory with *_tree.json files.
            taxonomy_path: Path to taxonomy.json (optional).
            rebuild: If True, clear existing data before building.

        Returns:
            BuildStats with summary statistics.
        """
        if rebuild:
            logger.info("Rebuilding — clearing existing vector store data")
            self.store.reset()

        # Load data
        trees = self._load_trees(trees_dir)
        taxonomy = self._load_taxonomy(taxonomy_path)

        # Build chunks and deduplicate by ID (keep longer text on collision)
        raw_chunks = self.chunk_builder.build_chunks(trees, taxonomy)
        chunks = self._deduplicate_chunks(raw_chunks)

        if not chunks:
            logger.warning("No chunks to embed")
            return BuildStats()

        # Filter out chunks already in the store (if not rebuilding)
        if not rebuild and self.store.count > 0:
            existing_count = self.store.count
            logger.info(
                f"Store already has {existing_count} documents. "
                f"Use --rebuild to start fresh."
            )
            # For simplicity in PoC, just rebuild when the count differs
            if existing_count == len(chunks):
                logger.info("Chunk count matches — skipping rebuild")
                return self._compute_stats(chunks)

            logger.info("Chunk count mismatch — rebuilding")
            self.store.reset()

        # Embed
        texts = [c.text for c in chunks]
        logger.info(
            f"Embedding {len(texts)} chunks with {self.embedder.model_name} "
            f"({self.embedder.dimension}d)"
        )
        embeddings = self._embed_batched(texts)

        # Store
        ids = [c.chunk_id for c in chunks]
        metadatas = [c.metadata for c in chunks]
        self.store.add(ids, embeddings, texts, metadatas)

        logger.info(
            f"Vector store built: {self.store.count} documents in "
            f"'{self.config.collection_name}'"
        )

        stats = self._compute_stats(chunks)
        return stats

    def _embed_batched(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches to manage memory."""
        batch_size = self.config.embedding_batch_size
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = self.embedder.embed(batch)
            all_embeddings.extend(batch_embeddings)

            if len(texts) > batch_size:
                logger.info(
                    f"Embedded batch {i // batch_size + 1}/"
                    f"{(len(texts) + batch_size - 1) // batch_size}"
                )

        return all_embeddings

    def _compute_stats(self, chunks: list[Chunk]) -> BuildStats:
        """Compute build statistics."""
        chunks_by_plan: dict[str, int] = {}
        for chunk in chunks:
            pid = chunk.metadata.get("plan_id", "unknown")
            chunks_by_plan[pid] = chunks_by_plan.get(pid, 0) + 1

        return BuildStats(
            total_chunks=len(chunks),
            chunks_by_plan=chunks_by_plan,
            embedding_model=self.embedder.model_name,
            embedding_dimension=self.embedder.dimension,
            vector_store_backend=self.config.vector_store_backend,
            distance_metric=self.config.distance_metric,
            collection_name=self.config.collection_name,
        )

    @staticmethod
    def _deduplicate_chunks(chunks: list[Chunk]) -> list[Chunk]:
        """Deduplicate chunks by ID, keeping the one with more text content.

        Duplicate IDs can occur when the parser assigns the same req_id
        to both a parent section and its child (known parser artifact).
        """
        seen: dict[str, Chunk] = {}
        dupes = 0
        for chunk in chunks:
            if chunk.chunk_id in seen:
                dupes += 1
                # Keep the chunk with more text content
                if len(chunk.text) > len(seen[chunk.chunk_id].text):
                    seen[chunk.chunk_id] = chunk
            else:
                seen[chunk.chunk_id] = chunk

        if dupes:
            logger.warning(
                f"Deduplicated {dupes} chunks with duplicate IDs "
                f"({len(chunks)} -> {len(seen)})"
            )

        return list(seen.values())

    @staticmethod
    def _load_trees(trees_dir: Path) -> list[dict]:
        trees = []
        for path in sorted(trees_dir.glob("*_tree.json")):
            with open(path, "r", encoding="utf-8") as f:
                trees.append(json.load(f))
        logger.info(f"Loaded {len(trees)} parsed trees from {trees_dir}")
        return trees

    @staticmethod
    def _load_taxonomy(taxonomy_path: Path | None) -> dict | None:
        if taxonomy_path is None or not taxonomy_path.exists():
            logger.info("No taxonomy provided — feature_ids will be empty")
            return None
        with open(taxonomy_path, "r", encoding="utf-8") as f:
            tax = json.load(f)
        logger.info(f"Loaded taxonomy with {len(tax.get('features', []))} features")
        return tax
