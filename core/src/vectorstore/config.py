"""Vector store configuration.

All tuneable parameters for embeddings and vector store are centralized
here. Configuration can be loaded from / saved to JSON, making it easy
to track which settings produced which results during experimentation.

Usage:
    # Default config
    config = VectorStoreConfig()

    # From JSON file
    config = VectorStoreConfig.load_json(Path("configs/vs_config.json"))

    # Override specific fields
    config = VectorStoreConfig(embedding_model="all-mpnet-base-v2", distance_metric="cosine")

    # Save for reproducibility
    config.save_json(Path("configs/vs_config.json"))
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class VectorStoreConfig:
    """Configuration for the vector store pipeline.

    All parameters that affect embedding quality, retrieval accuracy,
    or performance are captured here so experiments are reproducible.
    """

    # ── Embedding settings ───────────────────────────────────────
    embedding_provider: str = "sentence-transformers"
    """Which embedding backend to use. Options: 'sentence-transformers'."""

    embedding_model: str = "all-MiniLM-L6-v2"
    """Model name passed to the embedding provider.
    sentence-transformers options:
      - 'all-MiniLM-L6-v2'    (384d, fast, good baseline)
      - 'all-mpnet-base-v2'   (768d, slower, better quality)
      - 'BAAI/bge-large-en-v1.5' (1024d, strong retrieval model)
    """

    embedding_batch_size: int = 64
    """Batch size for embedding computation. Larger = faster but more memory."""

    embedding_device: str = "cpu"
    """Device for sentence-transformers. Options: 'cpu', 'cuda', 'mps'."""

    normalize_embeddings: bool = True
    """L2-normalize embeddings before storing. Required for cosine similarity
    with inner-product distance metrics."""

    # ── Vector store settings ────────────────────────────────────
    vector_store_backend: str = "chromadb"
    """Which vector store backend to use. Options: 'chromadb'."""

    collection_name: str = "requirements"
    """Collection/index name in the vector store."""

    distance_metric: str = "cosine"
    """Distance metric for similarity search.
    Options: 'cosine', 'l2', 'ip' (inner product).
    - cosine: normalized dot product, standard for text similarity
    - l2: Euclidean distance
    - ip: inner product (use with normalized embeddings)
    """

    persist_directory: str = "data/vectorstore"
    """Directory for persistent vector store data."""

    # ── Chunk contextualization ──────────────────────────────────
    include_mno_header: bool = True
    """Prepend [MNO: X | Release: Y | Plan: Z | Version: V] header."""

    include_hierarchy_path: bool = True
    """Prepend [Path: A > B > C] hierarchy path."""

    include_req_id: bool = True
    """Prepend [Req ID: VZ_REQ_...] line."""

    include_tables: bool = True
    """Append tables as Markdown within the chunk."""

    include_image_context: bool = True
    """Append image captions / surrounding text."""

    # ── Retrieval defaults ───────────────────────────────────────
    default_n_results: int = 10
    """Default number of results for queries."""

    # ── Extra provider-specific settings ─────────────────────────
    extra: dict[str, Any] = field(default_factory=dict)
    """Catch-all for provider-specific settings not covered above.
    Example: {"api_key": "...", "base_url": "..."} for API-based embedders.
    """

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path) -> VectorStoreConfig:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
