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


# ── Ollama-backed reranker ──────────────────────────────────────────


class OllamaReranker:
    """Cross-encoder reranker that scores (query, document) pairs via
    a local Ollama server. Same role as `CrossEncoderReranker` but
    uses Ollama's HTTP API instead of sentence_transformers + HF cache.

    Recommended Ollama models (must be pulled with `ollama pull <name>`):
      - `bbjson/bge-reranker-base:latest` — ~280M, BGE-reranker-base
        port. Strong on out-of-domain queries.

    Wire protocol: POST /api/embed with `input` = "query [SEP] passage"
    (newer Ollama) — modern reranker-capable Ollama servers return a
    single-float "embedding" that IS the relevance score. Falls back
    to /api/embeddings (older Ollama) if /api/embed 404s.

    Graceful degradation matches CrossEncoderReranker: connection
    failures or unexpected response shapes set `available=False` and
    the rerank() call passes through the input chunks unchanged. The
    pipeline keeps working with pre-rerank ordering.

    Satisfies the Reranker Protocol.
    """

    # Default separator between query and passage in the API input.
    # bge-reranker-base on HF uses `[SEP]` token; for an Ollama API
    # call we pass plain text — the server tokenizes. Empirically
    # newline-separated or "query: ... passage: ..." both work.
    _PAIR_SEP = "\n"

    def __init__(
        self,
        model_name: str = "bbjson/bge-reranker-base:latest",
        base_url: str = "http://localhost:11434",
        timeout: int = 60,
        max_chunk_chars: int = 4000,
    ) -> None:
        import urllib.error
        import urllib.request

        self._model_name = model_name
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_chunk_chars = max_chunk_chars
        # Reuse OllamaEmbedder's loopback-proxy-bypass logic so corporate
        # HTTP_PROXY env vars don't intercept localhost:11434.
        from core.src.vectorstore.embedding_ollama import _build_opener
        self._opener = _build_opener(self._base_url)
        # Which endpoint to use — detected on first call, cached.
        self._endpoint: str | None = None
        self._available = False

        # Reachability + model-presence check, matching OllamaEmbedder.
        try:
            req = urllib.request.Request(f"{self._base_url}/api/tags")
            with self._opener.open(req, timeout=5) as resp:
                import json as _json
                data = _json.loads(resp.read())
        except urllib.error.URLError as e:
            logger.warning(
                "OllamaReranker: cannot reach Ollama at %s (%s) — "
                "reranking disabled, retrieval order preserved",
                self._base_url, e,
            )
            return

        models = [m.get("name", "") for m in data.get("models", [])]
        present = (
            model_name in models
            or any(m.startswith(f"{model_name}:") for m in models)
        )
        if not present:
            available_list = ", ".join(models) if models else "none"
            logger.warning(
                "OllamaReranker: model %r not pulled. Available: %s. "
                "Pull with: ollama pull %s — reranking disabled until then",
                model_name, available_list, model_name,
            )
            return

        # Probe the scoring endpoint with a tiny pair; cache the working
        # endpoint name. If neither works, log and stay unavailable.
        if self._probe_endpoint():
            self._available = True
            logger.info(
                "OllamaReranker ready: model=%s, server=%s, endpoint=%s",
                model_name, self._base_url, self._endpoint,
            )

    @property
    def available(self) -> bool:
        return self._available

    def _probe_endpoint(self) -> bool:
        """Discover which Ollama endpoint serves the reranker. Try
        /api/embed first (modern), then /api/embeddings (legacy).
        Cache the working endpoint."""
        for endpoint in ("/api/embed", "/api/embeddings"):
            try:
                score = self._score_pair_via(
                    endpoint, query="test", passage="test"
                )
                if score is not None:
                    self._endpoint = endpoint
                    return True
            except Exception as e:
                logger.debug(
                    "OllamaReranker: endpoint %s probe failed (%s); "
                    "trying next", endpoint, e,
                )
        logger.warning(
            "OllamaReranker: no working scoring endpoint at %s; "
            "model %r may not be a reranker — falling back to passthrough",
            self._base_url, self._model_name,
        )
        return False

    def _score_pair_via(
        self, endpoint: str, query: str, passage: str,
    ) -> float | None:
        """One scoring call. Returns the relevance score (float) or
        None if the response shape doesn't look like a reranker output
        (e.g., model returns a multi-dim vector — not a reranker)."""
        import json as _json
        import urllib.request

        text = query + self._PAIR_SEP + passage
        # /api/embed uses `input`; /api/embeddings uses `prompt`. Both
        # accept the same body otherwise.
        if endpoint == "/api/embed":
            body = {"model": self._model_name, "input": text}
        else:
            body = {"model": self._model_name, "prompt": text}
        payload = _json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}{endpoint}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self._opener.open(req, timeout=self._timeout) as resp:
            data = _json.loads(resp.read())

        # Response shape varies. Look for known keys.
        # Newer /api/embed: {"embeddings": [[score]]} (reranker mode)
        #                   or {"embeddings": [[v1, v2, ...]]} (embedding mode).
        # Older /api/embeddings: {"embedding": [score]} or [v1, v2, ...].
        embs = data.get("embeddings") or []
        if embs and isinstance(embs, list) and embs[0]:
            row = embs[0]
            if isinstance(row, list):
                # A single-element vector IS the reranker score. If the
                # row has >1 element, the model is acting as a plain
                # embedder — not what we want.
                if len(row) == 1:
                    return float(row[0])
                logger.debug(
                    "OllamaReranker: %s returned multi-dim vector (%d-d); "
                    "model is acting as embedder, not reranker",
                    endpoint, len(row),
                )
                return None

        emb = data.get("embedding")
        if isinstance(emb, list):
            if len(emb) == 1:
                return float(emb[0])
            logger.debug(
                "OllamaReranker: %s returned multi-dim vector (%d-d); "
                "model is acting as embedder, not reranker",
                endpoint, len(emb),
            )
            return None

        return None

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Score each (query, chunk_text) pair via the Ollama scoring
        endpoint; return chunks sorted by descending score. Graceful
        degradation: empty / single-element / unavailable → passthrough.
        Wire-level failures during scoring log a warn and preserve input
        ordering (same shape as CrossEncoderReranker)."""
        if not chunks:
            return []
        if len(chunks) == 1 or not self._available:
            return list(chunks)
        assert self._endpoint is not None  # set by _probe_endpoint when available

        scored: list[tuple[RetrievedChunk, float]] = []
        for c in chunks:
            try:
                s = self._score_pair_via(
                    self._endpoint,
                    query=query,
                    passage=self._truncate(c.text),
                )
            except Exception as e:
                logger.warning(
                    "OllamaReranker: scoring failed for chunk %s (%r); "
                    "skipping (preserving input order)",
                    c.chunk_id, e,
                )
                return list(chunks)
            if s is None:
                logger.warning(
                    "OllamaReranker: scoring returned None for chunk %s; "
                    "preserving input order",
                    c.chunk_id,
                )
                return list(chunks)
            scored.append((c, s))

        # Stable sort by descending score
        scored.sort(key=lambda p: -p[1])
        return [c for c, _ in scored]

    def _truncate(self, text: str) -> str:
        if not text:
            return ""
        if len(text) <= self._max_chunk_chars:
            return text
        return text[: self._max_chunk_chars]
