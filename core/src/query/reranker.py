"""Cross-encoder reranker — final-pass relevance scoring on the
fused top-K chunks from the BM25 + dense retrieval pipeline.

The retrieval stack today is:
    1. Graph scope → candidate req_ids
    2. Dense vector search (top-K candidates by cosine)
    3. BM25 sparse search over the same candidates
    4. RRF fusion → top-N fused chunks
    5. (optional) cross-encoder rerank → top-M ≤ N
    6. Context assembly + LLM synthesis

The bi-encoder dense retriever and BM25 are FAST but produce only
a coarse ranking — they don't see the (query, chunk) pair jointly.
A cross-encoder model takes (query, chunk) as a single input and
outputs a fine-grained relevance score; running it on a small set
(top-N from RRF fusion) lets us re-order before the LLM sees the
context.

Two implementations behind a `Reranker` Protocol:
    - `MockReranker`: passthrough — returns chunks in their input
      order, used by tests and offline / deterministic paths.
    - `CrossEncoderReranker(model_name)`: wraps
      `sentence_transformers.CrossEncoder`. Falls back to a
      passthrough on construction failure (model not pulled,
      sentence-transformers offline, etc.) — never raises into
      the retrieval path.

Design notes:
    - Reranker runs AFTER RRF fusion, before diversity enforcement.
      RRF gives us a candidate ordering across both retrievers; the
      reranker just permutes the top-K.
    - Each rerank call is O(N) cross-encoder evaluations; we cap N
      at the retriever's fanout. With `cross-encoder/ms-marco-
      MiniLM-L6-v2` (default), expect ~10ms/pair on CPU →
      ~250-500ms total per query. Negligible vs LLM synthesis.
    - The cross-encoder model is local-only (ships via
      sentence-transformers' HF cache, same offline path as the
      bi-encoder). No new infrastructure.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from core.src.query.schema import RetrievedChunk

logger = logging.getLogger(__name__)


# Default cross-encoder. Small (~80MB), fast on CPU, generic English
# trained on MS MARCO. Telecom corpus is technical English so it
# transfers reasonably; corpora with heavy non-English content should
# pick a multilingual reranker like `BAAI/bge-reranker-v2-m3` instead.
_DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"


@runtime_checkable
class Reranker(Protocol):
    """Protocol for rerankers.

    `rerank(query, chunks) -> list[RetrievedChunk]` returns the same
    chunks in (possibly) a different order. Implementations may also
    drop chunks they consider irrelevant, but the v1 contract is
    "return all input chunks reordered" so callers know the size is
    preserved.
    """

    def rerank(
        self, query: str, chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        ...


class MockReranker:
    """Deterministic no-op reranker — returns input chunks as-is.

    Used by unit tests and as the default when reranking is disabled.
    Pinned `rerank` is a passthrough so existing pipelines see
    unchanged retrieval output when a reranker slot is supplied
    without a real cross-encoder.
    """

    def rerank(
        self, query: str, chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        return list(chunks)


class CrossEncoderReranker:
    """Wraps `sentence_transformers.CrossEncoder` to score (query,
    chunk) pairs jointly and reorder by descending relevance.

    Constructor failures (model not cached + offline; sentence-
    transformers import error; misconfigured environment) fall back
    to a Mock-style passthrough — the pipeline degrades to the
    pre-rerank ordering rather than crashing. This matches the
    pattern in `BM25Index.from_store` and `OllamaEmbedder` (warn,
    don't fail).
    """

    def __init__(
        self,
        model_name: str = _DEFAULT_RERANKER_MODEL,
        device: str = "cpu",
        batch_size: int = 32,
        max_chunk_chars: int = 4000,
    ) -> None:
        """Args:
            model_name: HuggingFace cross-encoder model id. Default is
                `cross-encoder/ms-marco-MiniLM-L6-v2` — small, fast,
                generic English. For multilingual corpora consider
                `BAAI/bge-reranker-v2-m3`.
            device: torch device. CPU is the safe default; pass
                "cuda" / "mps" if available.
            batch_size: cross-encoder forward-pass batch size. Tune
                up on GPU; default 32 is conservative for CPU.
            max_chunk_chars: chunk text truncation before scoring.
                Cross-encoders are token-limited; truncating here
                prevents long-tail chunks from blowing past the model
                window. The early prefix preserves the path / req-id
                / opening sentences which carry the most signal.
        """
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._max_chunk_chars = max_chunk_chars
        self._model = None
        self._available = False

        try:
            # Local import — we don't pay the import cost for the
            # passthrough path or for callers that opt out.
            from sentence_transformers import CrossEncoder

            # Same offline-cache strategy as the bi-encoder
            # (embedding_st.py): if the model is already in the HF
            # cache, this works fully offline.
            from core.src.vectorstore.hf_offline import enable_offline_if_cached
            enable_offline_if_cached(model_name)

            self._model = CrossEncoder(model_name, device=device)
            self._available = True
            logger.info(
                f"CrossEncoderReranker ready: model={model_name}, "
                f"device={device}, batch_size={batch_size}"
            )
        except Exception as e:
            # Graceful degradation: missing model / offline / import
            # error → log + run as passthrough. The pipeline keeps
            # working with pre-rerank ordering.
            logger.warning(
                f"CrossEncoderReranker unavailable ({e!r}); "
                f"reranking disabled, retrieval order preserved"
            )

    @property
    def available(self) -> bool:
        return self._available

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Score every (query, chunk_text) pair and return chunks
        sorted by descending score. Empty input → empty output;
        single-element input → that element (no scoring needed).
        Falls back to passthrough when the model is unavailable.
        """
        if not chunks:
            return []
        if len(chunks) == 1 or not self._available:
            return list(chunks)

        pairs = [(query, self._truncate(c.text)) for c in chunks]
        try:
            scores = self._model.predict(
                pairs,
                batch_size=self._batch_size,
                show_progress_bar=False,
            )
        except Exception as e:
            logger.warning(
                f"Cross-encoder predict failed ({e!r}); "
                f"returning input order"
            )
            return list(chunks)

        # Pair each chunk with its score; sort descending. Stable sort
        # so equal-score pairs preserve their input (RRF-fused) order.
        scored = list(zip(chunks, scores))
        scored.sort(key=lambda p: -float(p[1]))
        return [c for c, _ in scored]

    def _truncate(self, text: str) -> str:
        """Truncate to `max_chunk_chars` so long-tail chunks don't
        exceed the cross-encoder's token window. Chunk prefix
        carries path + req-id + opening sentences — the most
        discriminating signal."""
        if not text:
            return ""
        if len(text) <= self._max_chunk_chars:
            return text
        return text[: self._max_chunk_chars]
