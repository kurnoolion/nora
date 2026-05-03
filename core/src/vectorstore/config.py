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
    """Which embedding backend to use.
    Options:
      - 'sentence-transformers' (default): local HF-cached model
      - 'ollama': uses the same Ollama runtime as the LLM provider; no
        separate HuggingFace offline cache needed. Configure ollama_url
        via `extra["ollama_url"]` (defaults to http://localhost:11434).
    """

    embedding_model: str = "all-MiniLM-L6-v2"
    """Model name passed to the embedding provider.
    sentence-transformers options:
      - 'all-MiniLM-L6-v2'       (384d, fast, good baseline)
      - 'all-mpnet-base-v2'      (768d, slower, better quality)
      - 'BAAI/bge-large-en-v1.5' (1024d, strong retrieval model)
    ollama options (must be pulled first via `ollama pull <name>`):
      - 'nomic-embed-text'       (768d, ~270MB, balanced)
      - 'mxbai-embed-large'      (1024d, ~670MB, top quality)
      - 'all-minilm'             (384d, ~45MB, fastest)
    """

    embedding_batch_size: int = 64
    """Batch size for embedding computation. Larger = faster but more memory.
    (Used by sentence-transformers; Ollama's /api/embeddings is single-text-per-call.)"""

    embedding_device: str = "cpu"
    """Device for sentence-transformers. Options: 'cpu', 'cuda', 'mps'.
    (Ignored by ollama; the Ollama runtime manages device placement itself.)"""

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

    include_children_titles: bool = False
    """Append [Subsections: t1, t2, ...] line with the immediate
    children's titles when the requirement has children. Lifts thin
    parent/overview chunks for breadth queries — e.g., a "SMS over
    IMS - OVERVIEW" parent whose body is just a heading becomes
    retrievable for "what are all the SMS over IMS requirements?"
    when its children's titles ('MO SMS', 'MT SMS', ...) are
    embedded alongside.

    Gated by `children_titles_body_threshold`: only emitted on
    parents whose own body text is below the threshold (most OA
    parents are heading-only, so the gate's selectivity is corpus-
    dependent — on OA, ~94% of parents pass).

    Capped at `max_children_titles` to bound chunk growth on
    highly-branching parents.

    **Default off.** Empirical tuning on the OA eval (88.9% / 80.1%
    pre-feature) showed the augmentation creates a tradeoff:
    single_doc / lookup queries gained +8pp accuracy (parents made
    more findable for "find this section" queries), but cross_doc /
    breadth queries lost ~10-14pp (augmented parents displace their
    children from top-k, and breadth queries want the children).
    Net was -0.4 to -1.3pp accuracy depending on cap. Feature stays
    available behind the flag for corpora / eval mixes where the
    balance flips the other way (rich-bodied parents, lookup-heavy
    questions, or breadth queries that explicitly want overview
    chunks)."""

    children_titles_body_threshold: int = 300
    """Body-text length (characters) below which a parent's chunk
    gets augmented with `[Subsections: ...]`. 300 chars is roughly
    the OA convention's gap between heading-only / 1-sentence-
    intro overviews (typically <200 chars) and substantive content
    sections (typically >500 chars). Tune per corpus."""

    max_children_titles: int = 3
    """Maximum number of child titles to include when augmentation
    fires. Truncated suffixes get "(+N more)" appended. Default 3
    is empirically tuned: larger caps (tested 12) make parents
    dominate cross-doc breadth queries, displacing the children
    that breadth queries actually want; smaller caps cap the
    displacement effect while still adding meaningful concept
    breadth to overview chunks."""

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
