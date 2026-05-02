"""BM25 sparse retrieval index for the query pipeline.

Companion to the dense vector store. Indexes the same chunk corpus
the vector store holds, scored by Okapi BM25 over a telecom-aware
tokenization. Hybrid retrieval (RAGRetriever) fuses BM25 ranks with
dense ranks via Reciprocal Rank Fusion (RRF) so that:

  - specific terms ("T3402", "TS 24.301", "VZ_REQ_LTEDATARETRY_2377")
    that pure-dense embeddings underweight surface near the top, AND
  - concept similarity from the dense side still drives the rest of
    the ranking.

Built in-memory at QueryPipeline init time from a one-shot
`store.get_all()` snapshot — fits comfortably for the v1 corpus
(~800 chunks). Persistence can be added later if needed.

Non-goals: stemming, stopword removal, query expansion. Telecom
acronyms don't stem cleanly and BM25's default IDF weighting
already penalizes common words.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# Token pattern: alphanumeric runs that may contain `_`, `.`, `-`
# inside (so `VZ_REQ_LTEAT_45`, `24.301`, `Rel-9` survive as single
# tokens). Lowercased before matching so case never matters.
_TOKEN_RE = re.compile(r"[a-z0-9_.\-]+")
_MIN_TOKEN_LEN = 2


def tokenize(text: str) -> list[str]:
    """Telecom-aware tokenizer used for BM25 indexing and query parsing.

    Lowercases, splits on whitespace and most punctuation but preserves
    `_`, `.`, `-` inside tokens. Drops single-character tokens.

    Examples:
      "VZ_REQ_LTEAT_45 references TS 24.301 Rel-9"
        → ["vz_req_lteat_45", "references", "ts", "24.301", "rel-9"]
      "What is requirement VZ_REQ_LTEDATARETRY_7754?"
        → ["what", "is", "requirement", "vz_req_lteat_45"]  # `?` strips
      "T3402 timer behavior"
        → ["t3402", "timer", "behavior"]
    """
    if not text:
        return []
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= _MIN_TOKEN_LEN]


class BM25Index:
    """Wraps `rank_bm25.BM25Okapi` with the chunk corpus + telecom
    tokenizer. Owns its own copy of (id, text, metadata) tuples so
    `search` can return chunks directly without round-tripping to the
    vector store.
    """

    def __init__(
        self,
        chunk_ids: list[str],
        chunk_texts: list[str],
        chunk_metadatas: list[dict[str, Any]],
    ) -> None:
        from rank_bm25 import BM25Okapi  # local import — optional dep

        if not chunk_ids:
            raise ValueError("BM25Index requires at least one chunk")
        if not (len(chunk_ids) == len(chunk_texts) == len(chunk_metadatas)):
            raise ValueError(
                "ids, texts, metadatas must have matching lengths"
            )

        self._ids = list(chunk_ids)
        self._texts = list(chunk_texts)
        self._metadatas = list(chunk_metadatas)
        # Tokenize once; rank_bm25 retains the tokenized corpus
        tokenized = [tokenize(t) for t in chunk_texts]
        self._bm25 = BM25Okapi(tokenized)
        logger.info(
            f"BM25Index built: {len(self._ids)} chunks, "
            f"avg tokens/chunk={sum(len(t) for t in tokenized) / len(tokenized):.1f}"
        )

    @classmethod
    def from_store(cls, store) -> BM25Index | None:
        """Build a BM25Index from a `VectorStoreProvider.get_all()`
        snapshot. Returns None if the store is empty or `get_all` is
        unavailable — the caller should treat None as "BM25 not
        available; fall back to pure dense retrieval".
        """
        if not hasattr(store, "get_all"):
            logger.warning(
                "Store lacks get_all() — BM25 disabled; pure dense fallback"
            )
            return None
        try:
            snapshot = store.get_all()
        except Exception as e:
            logger.warning(f"store.get_all() failed: {e!r} — BM25 disabled")
            return None
        if not snapshot.ids:
            logger.warning("Store is empty — BM25 disabled")
            return None
        return cls(snapshot.ids, snapshot.documents, snapshot.metadatas)

    def search(
        self,
        query: str,
        top_k: int = 25,
        filter_ids: set[str] | None = None,
        filter_metadata: tuple[str, set[str]] | None = None,
    ) -> list[tuple[str, float]]:
        """Return top-k `(chunk_id, bm25_score)` tuples in descending
        score order.

        Two filter modes (mutually compatible — both apply when
        supplied):

        - `filter_ids` — set of chunk_ids to keep. Useful when the
          caller already knows the chunk-id space (e.g., a previous
          dense-retrieval result narrowed the population).
        - `filter_metadata = (key, values)` — keep chunks whose
          `metadata[key]` is in the value set. Mirrors the dense
          path's `where: <key> $in [...]` filter, so a graph-scoped
          set of `req_id` values can gate BOTH retrievers identically
          (chunk_id and metadata.req_id are not always the same
          string — chunk_id is often `req:<req_id>`).

        BM25 scores are not normalized; downstream callers should not
        compare them against dense distances. Use the rank-based
        `rrf_fuse` helper to combine.
        """
        if not query:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        meta_key, meta_values = (None, None)
        if filter_metadata is not None:
            meta_key, meta_values = filter_metadata
        scores = self._bm25.get_scores(tokens)
        pairs: list[tuple[str, float]] = []
        for i, cid in enumerate(self._ids):
            if filter_ids is not None and cid not in filter_ids:
                continue
            if meta_key is not None:
                if self._metadatas[i].get(meta_key) not in meta_values:
                    continue
            pairs.append((cid, float(scores[i])))
        pairs.sort(key=lambda p: -p[1])
        return pairs[:top_k]

    def chunk_text(self, chunk_id: str) -> str:
        """Retrieve a chunk's text by id. Used by hybrid retrieval to
        materialize BM25-ranked-but-not-dense-retrieved chunks
        without a second store round-trip.
        """
        try:
            i = self._ids.index(chunk_id)
        except ValueError:
            return ""
        return self._texts[i]

    def chunk_metadata(self, chunk_id: str) -> dict[str, Any]:
        try:
            i = self._ids.index(chunk_id)
        except ValueError:
            return {}
        return dict(self._metadatas[i])

    @property
    def size(self) -> int:
        return len(self._ids)


def rrf_fuse(
    *ranked_lists: list[str],
    k: int = 60,
    weights: list[float] | None = None,
    top_k: int | None = None,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion across multiple ranked lists of chunk ids.

    Each input is a list of chunk ids in DESCENDING relevance order
    (rank 1 = most relevant). The fused score for chunk `d` is:

        score(d) = sum over inputs i of  weight_i / (k + rank_i(d))

    Chunks absent from a given input contribute 0 from that input.
    Default `k=60` matches the constant used in the original RRF
    paper (Cormack 2009) — robust across heterogeneous score
    distributions, no normalization needed.

    `weights` (default: all 1.0 — uniform RRF) lets callers bias
    the fusion. Required when one retriever is reliably more
    relevant than another for a given workload — empirically, dense
    embeddings out-rank BM25 on overview/list queries (where the
    expected chunks are thin parent reqs that BM25 underweights),
    so the hybrid retriever uses (dense=1.0, bm25=0.5) by default.

    Returns `(chunk_id, fused_score)` tuples in descending fused-score
    order, optionally capped at `top_k`.
    """
    if not ranked_lists:
        return []
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError(
            f"weights length ({len(weights)}) != ranked_lists length "
            f"({len(ranked_lists)})"
        )
    fused: dict[str, float] = {}
    for ranked, w in zip(ranked_lists, weights):
        for rank, cid in enumerate(ranked, start=1):
            fused[cid] = fused.get(cid, 0.0) + w / (k + rank)
    out = sorted(fused.items(), key=lambda p: -p[1])
    if top_k is not None:
        out = out[:top_k]
    return out
