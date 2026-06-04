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

    Wire protocol — supports two response shapes detected at probe time:

      - "rerank_score" — modern reranker-capable Ollama servers
        return `{"embeddings": [[score]]}` (single-float) for a
        concatenated (query, passage) input. Used directly as the
        relevance score. One HTTP call per pair.

      - "embedding_similarity" — when the response is a multi-dim
        vector, the model is packaged as a plain embedder (not a
        cross-encoder). Used by computing cosine similarity between
        the query embedding and each passage embedding. Two calls
        per pair (one query embed, N passage embeds reused-once).
        Lower quality than a true cross-encoder — no joint
        query/passage attention — but still useful as a second-
        stage discriminator since the embedding model is typically
        different from the dense retrieval embedder.

    Endpoint fallback: tries /api/embed first (modern), then
    /api/embeddings (legacy).

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
        # Scoring mode — "rerank_score" (single-float per pair) or
        # "embedding_similarity" (cosine over multi-dim vectors).
        # Detected at probe time alongside the endpoint.
        self._mode: str | None = None
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
        # endpoint + mode. If neither works, log and stay unavailable.
        if self._probe_endpoint_and_mode():
            self._available = True
            logger.info(
                "OllamaReranker ready: model=%s, server=%s, endpoint=%s, mode=%s",
                model_name, self._base_url, self._endpoint, self._mode,
            )

    @property
    def available(self) -> bool:
        return self._available

    def _probe_endpoint_and_mode(self) -> bool:
        """Discover which Ollama endpoint + scoring mode this model
        supports. Try /api/embed first (modern), then /api/embeddings.
        Cache both endpoint and mode on success.

        Mode is auto-detected from the response shape:
          - single-element vector → "rerank_score" (true cross-encoder
            packaging — the float IS the relevance score).
          - multi-dim vector → "embedding_similarity" (model is
            packaged as a plain embedder; rerank() will compute
            cosine over query/passage embeddings).
        """
        for endpoint in ("/api/embed", "/api/embeddings"):
            try:
                vec = self._embed_raw(endpoint, "probe")
            except Exception as e:
                logger.debug(
                    "OllamaReranker: endpoint %s probe failed (%s); "
                    "trying next", endpoint, e,
                )
                continue
            if not vec:
                continue
            self._endpoint = endpoint
            if len(vec) == 1:
                self._mode = "rerank_score"
                logger.info(
                    "OllamaReranker: model %r returns single-float "
                    "scores → using cross-encoder reranker mode",
                    self._model_name,
                )
            else:
                self._mode = "embedding_similarity"
                logger.info(
                    "OllamaReranker: model %r returns %d-dim "
                    "embeddings → using embedding-similarity mode "
                    "(cosine over query/passage). Lower quality than "
                    "a true cross-encoder but still useful as a "
                    "second-stage discriminator.",
                    self._model_name, len(vec),
                )
            return True
        logger.warning(
            "OllamaReranker: no working scoring endpoint at %s for "
            "model %r — falling back to passthrough",
            self._base_url, self._model_name,
        )
        return False

    def _embed_raw(
        self, endpoint: str, text: str,
    ) -> list[float]:
        """One Ollama call; return the raw vector (any dimensionality).
        Throws on HTTP / connection errors. Returns [] on empty body."""
        import json as _json
        import urllib.request

        # /api/embed uses `input`; /api/embeddings uses `prompt`.
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

        # /api/embed: {"embeddings": [[v1, v2, ...]]}
        embs = data.get("embeddings") or []
        if embs and isinstance(embs, list) and isinstance(embs[0], list):
            return [float(x) for x in embs[0]]
        # /api/embeddings: {"embedding": [v1, v2, ...]}
        emb = data.get("embedding")
        if isinstance(emb, list):
            return [float(x) for x in emb]
        return []

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        """Cosine similarity. Returns 0.0 when either vector is empty
        or has zero norm — preserves a deterministic ordering in those
        degenerate cases."""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Score each chunk against the query; return descending order.

        Two scoring paths, selected at probe time:
          - "rerank_score": one Ollama call per (query, passage)
            concatenation. Single-float response IS the score.
          - "embedding_similarity": one call for the query, one per
            passage, then cosine similarity between the vectors.

        Graceful degradation: empty / single-element / unavailable →
        passthrough. Any wire failure during scoring logs a warn and
        returns the input order (same shape as CrossEncoderReranker)."""
        if not chunks:
            return []
        if len(chunks) == 1 or not self._available:
            return list(chunks)
        assert self._endpoint is not None and self._mode is not None

        if self._mode == "embedding_similarity":
            return self._rerank_by_similarity(query, chunks)
        return self._rerank_by_score(query, chunks)

    def _rerank_by_score(
        self, query: str, chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """True cross-encoder mode: one call per (query, passage) pair;
        the returned single-float IS the score."""
        scored: list[tuple[RetrievedChunk, float]] = []
        for c in chunks:
            text = query + self._PAIR_SEP + self._truncate(c.text)
            try:
                vec = self._embed_raw(self._endpoint, text)
            except Exception as e:
                logger.warning(
                    "OllamaReranker: score call failed for chunk %s (%r); "
                    "preserving input order", c.chunk_id, e,
                )
                return list(chunks)
            if len(vec) != 1:
                logger.warning(
                    "OllamaReranker: expected single-float score for "
                    "chunk %s, got %d-d vector — preserving input order",
                    c.chunk_id, len(vec),
                )
                return list(chunks)
            scored.append((c, vec[0]))
        scored.sort(key=lambda p: -p[1])
        return [c for c, _ in scored]

    def _rerank_by_similarity(
        self, query: str, chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Embedding-similarity mode: embed query once, embed each
        passage, sort by cosine similarity. Used when the model is
        packaged as a plain embedder (e.g., bbjson/bge-reranker-base
        port returns 3072-dim vectors, not single-float scores)."""
        try:
            qvec = self._embed_raw(self._endpoint, query)
        except Exception as e:
            logger.warning(
                "OllamaReranker: query embed failed (%r); preserving "
                "input order", e,
            )
            return list(chunks)
        if not qvec:
            logger.warning(
                "OllamaReranker: query embed returned empty vector — "
                "preserving input order"
            )
            return list(chunks)

        scored: list[tuple[RetrievedChunk, float]] = []
        for c in chunks:
            try:
                pvec = self._embed_raw(self._endpoint, self._truncate(c.text))
            except Exception as e:
                logger.warning(
                    "OllamaReranker: passage embed failed for chunk %s "
                    "(%r); preserving input order", c.chunk_id, e,
                )
                return list(chunks)
            scored.append((c, self._cosine(qvec, pvec)))
        scored.sort(key=lambda p: -p[1])
        return [c for c, _ in scored]

    def _truncate(self, text: str) -> str:
        if not text:
            return ""
        if len(text) <= self._max_chunk_chars:
            return text
        return text[: self._max_chunk_chars]


# ─────────────────────────────────────────────────────────────────────
# OpenAI-compatible rerankers — chat-completions and dedicated /v1/rerank
# ─────────────────────────────────────────────────────────────────────


class OpenAIRerankChat:
    """Reranker that scores (query, document) pairs via an OpenAI-
    compatible ``/v1/chat/completions`` endpoint with a per-pair scoring
    prompt. Works against any LLM-serving stack (vLLM, SGLang, the
    proprietary shim, etc.) — no dedicated reranker model required.

    Per-call wire shape:
        POST {base_url}/v1/chat/completions
        body: {"model": ..., "messages": [{"role":"user","content": <prompt>}],
               "temperature": 0.0, "max_tokens": <small>}

    The model returns a free-form text completion; we extract the first
    integer in 0..100 as the relevance score. Robust to a bit of
    surrounding text — many models emit ``Score: 42`` or similar.

    Slower than a true cross-encoder: one HTTP call per chunk (no
    batching unless the chat endpoint natively batches). On a small
    top-K (10–25 chunks), latency dominates per-call rather than
    throughput.

    Graceful degradation: per-call failures score 0 (chunk drops to
    the tail rather than aborting the rerank). Construction-time
    failure (empty base_url, transport init error) sets
    ``available=False`` and ``rerank()`` becomes a passthrough.

    Satisfies the ``Reranker`` Protocol.
    """

    # Default scoring prompt. Intentionally short so even small/cheap
    # instruct models can follow it. The system asks for a bare integer
    # so parsing is robust.
    _SCORING_PROMPT_TEMPLATE = (
        "You are a relevance judge. On a 0-100 integer scale, score how "
        "relevant the document is to the query. 0 = unrelated, 50 = "
        "partially related, 100 = directly answers the query. Output "
        "ONLY the integer score with no explanation.\n\n"
        "Query: {query}\n\n"
        "Document: {document}\n\n"
        "Score:"
    )

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str = "",
        *,
        timeout_s: float = 60.0,
        max_chunk_chars: int = 4000,
        batch_size: int = 1,
    ) -> None:
        self._model_name = model_name
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self._timeout_s = timeout_s
        self._max_chunk_chars = max_chunk_chars
        # batch_size <= 1 means per-call (one HTTP request per chunk).
        # batch_size > 1 packs N (query, document) pairs into one call
        # with a JSON-array response. See _format_batch_prompt and
        # D-089 (mirrors SIRA's per-query service batch pattern).
        self._batch_size = max(1, batch_size)
        self.available = True
        if not self._base_url:
            logger.warning(
                "OpenAIRerankChat: empty base_url — falling back to "
                "MockReranker passthrough.",
            )
            self.available = False
            return
        logger.info(
            "OpenAIRerankChat ready: model=%s, base_url=%s, timeout=%ds, "
            "batch_size=%d (%s mode)",
            model_name, self._base_url, int(timeout_s),
            self._batch_size,
            "batched" if self._batch_size > 1 else "per-call",
        )

    def _score_one(self, query: str, doc_text: str) -> int:
        """Score one (query, doc) pair. Returns 0 on any failure so
        a single bad call doesn't sink the whole rerank."""
        import json as _json
        import re
        import urllib.error
        import urllib.request

        prompt = self._SCORING_PROMPT_TEMPLATE.format(
            query=query,
            document=self._truncate(doc_text),
        )
        payload = {
            "model": self._model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 16,
        }
        body = _json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=body, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, _json.JSONDecodeError):
            return 0
        try:
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
        except (AttributeError, IndexError, TypeError):
            return 0
        m = re.search(r"-?\d+", content or "")
        if not m:
            return 0
        try:
            score = int(m.group(0))
        except ValueError:
            return 0
        return max(0, min(100, score))

    _BATCH_PROMPT_HEADER = (
        "You are scoring documents for relevance to a query. For each "
        "document, output an integer 0-100 score.\n\n"
        "Scoring rubric:\n"
        "- 0: completely unrelated topic\n"
        "- 1-20: tangentially related but no answer\n"
        "- 21-40: discusses related concepts, no direct answer\n"
        "- 41-70: partial answer or strongly related\n"
        "- 71-100: directly answers the query\n\n"
        "Output ONLY a JSON array of objects in document order:\n"
        '[{"id": 0, "score": N}, {"id": 1, "score": N}, ...]\n'
        "No commentary, no thinking, no markdown — just the JSON.\n\n"
    )

    def _format_batch_prompt(
        self, query: str, docs: list[tuple[int, str]],
    ) -> str:
        """Build a batch-scoring prompt for ``docs=[(local_id, text), ...]``.
        Mirrors the shape SIRA's per-query service uses (D-089) so the
        same prompt-rubric calibration applies."""
        docs_block = "\n\n".join(
            f"[{lid}] {self._truncate(text)}" for lid, text in docs
        )
        return (
            self._BATCH_PROMPT_HEADER
            + f"Query: {query}\n\n"
            + f"Documents:\n{docs_block}\n\n"
            + "Scores:"
        )

    @staticmethod
    def _parse_batch_response(
        raw: str, expected_ids: list[int],
    ) -> dict[int, int]:
        """Extract ``{id: score}`` from a batch rerank response. Missing /
        unparseable ids default to 0 (matches per-call failure semantics).
        Tolerant of CoT preambles before the JSON, trailing commentary
        after, and per-object parse failures within the array."""
        import json as _json
        import re
        out: dict[int, int] = {i: 0 for i in expected_ids}
        # First-pass: find the JSON array bracket range and parse.
        lb = raw.find("[")
        rb = raw.rfind("]")
        if lb != -1 and rb > lb:
            try:
                arr = _json.loads(raw[lb : rb + 1])
                if isinstance(arr, list):
                    for item in arr:
                        if not isinstance(item, dict):
                            continue
                        try:
                            i = int(item.get("id"))
                            s = int(item.get("score", 0))
                        except (TypeError, ValueError):
                            continue
                        if i in out:
                            out[i] = max(0, min(100, s))
                    return out
            except _json.JSONDecodeError:
                pass
        # Fallback: regex-scan for individual {"id":N,"score":M} objects.
        for m in re.finditer(
            r'\{\s*"id"\s*:\s*(-?\d+)\s*,\s*"score"\s*:\s*(-?\d+)\s*\}',
            raw,
        ):
            i, s = int(m.group(1)), int(m.group(2))
            if i in out:
                out[i] = max(0, min(100, s))
        return out

    def _score_batch(
        self, query: str, batch: list[tuple[int, str]],
    ) -> dict[int, int]:
        """Score one batch in a single HTTP call. Returns {local_id: score};
        missing ids default to 0. Whole-batch failure (transport or
        protocol-level) returns 0 for every id in the batch."""
        import json as _json
        import urllib.error
        import urllib.request

        prompt = self._format_batch_prompt(query, batch)
        # Generous max_tokens — batch responses can be larger.
        # 32 tokens per item + 64 base accommodates 25-chunk batches.
        max_tokens = 64 + 32 * len(batch)
        payload = {
            "model": self._model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }
        body = _json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=body, headers=headers, method="POST",
        )
        expected = [lid for lid, _ in batch]
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, _json.JSONDecodeError):
            return {i: 0 for i in expected}
        try:
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
        except (AttributeError, IndexError, TypeError):
            return {i: 0 for i in expected}
        return self._parse_batch_response(content or "", expected)

    def rerank(
        self, query: str, chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        if not self.available or not chunks:
            return list(chunks)

        # Per-call mode: one HTTP request per chunk (simple, robust).
        if self._batch_size <= 1:
            scored: list[tuple[int, int, RetrievedChunk]] = []
            for i, c in enumerate(chunks):
                score = self._score_one(query, c.text or "")
                scored.append((-score, i, c))
            scored.sort()
            return [c for _, _, c in scored]

        # Batched mode: pack `batch_size` chunks per HTTP request.
        # Local ids are batch-local (0..len(batch)-1), so the LLM gets
        # short integer ids regardless of original chunk index.
        scored2: list[tuple[int, int, RetrievedChunk]] = []
        for batch_start in range(0, len(chunks), self._batch_size):
            batch_chunks = chunks[batch_start : batch_start + self._batch_size]
            batch = [
                (local_id, c.text or "")
                for local_id, c in enumerate(batch_chunks)
            ]
            scores = self._score_batch(query, batch)
            for local_id, c in enumerate(batch_chunks):
                score = scores.get(local_id, 0)
                # Global tiebreak preserves input order across batches
                # AND within a batch when the model returns equal scores.
                global_index = batch_start + local_id
                scored2.append((-score, global_index, c))
        scored2.sort()
        return [c for _, _, c in scored2]

    def _truncate(self, text: str) -> str:
        if not text:
            return ""
        if len(text) <= self._max_chunk_chars:
            return text
        return text[: self._max_chunk_chars]


class OpenAIRerankDedicated:
    """Reranker that calls an OpenAI-compatible ``/v1/rerank`` endpoint
    in a single batched HTTP call. The server must expose this route
    with a cross-encoder reranker model loaded (vLLM does, when started
    with a reranker model).

    Wire shape (vLLM / cohere-style convention):
        POST {base_url}/v1/rerank
        body: {"model": <name>, "query": <str>, "documents": [<str>, ...]}
        response: {"results": [
            {"index": int, "relevance_score": float}, ...
        ]}

    Single round trip regardless of top-K size, so latency scales with
    server batching rather than per-pair RTT — typically 10–100× faster
    than the chat-completions variant for the same top-K.

    Graceful degradation: a non-200 response, transport error, or
    malformed body returns the input chunks unchanged. Construction-
    time failure (empty base_url) sets ``available=False`` and
    ``rerank()`` becomes a passthrough.

    Satisfies the ``Reranker`` Protocol.
    """

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str = "",
        *,
        timeout_s: float = 60.0,
        max_chunk_chars: int = 4000,
    ) -> None:
        self._model_name = model_name
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self._timeout_s = timeout_s
        self._max_chunk_chars = max_chunk_chars
        self.available = True
        if not self._base_url:
            logger.warning(
                "OpenAIRerankDedicated: empty base_url — falling back "
                "to MockReranker passthrough.",
            )
            self.available = False
            return
        logger.info(
            "OpenAIRerankDedicated ready: model=%s, base_url=%s, timeout=%ds",
            model_name, self._base_url, int(timeout_s),
        )

    def rerank(
        self, query: str, chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        import json as _json
        import urllib.error
        import urllib.request

        if not self.available or not chunks:
            return list(chunks)

        docs = [self._truncate(c.text or "") for c in chunks]
        payload = {
            "model": self._model_name,
            "query": query,
            "documents": docs,
        }
        body = _json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(
            f"{self._base_url}/v1/rerank",
            data=body, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, _json.JSONDecodeError) as exc:
            logger.warning(
                "OpenAIRerankDedicated: call failed (%r) — returning "
                "input order unchanged for this query.",
                exc,
            )
            return list(chunks)

        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return list(chunks)

        # Parse + dedup ranked indices. Defensive: keep only valid
        # in-range integers; any chunks the server didn't rank get
        # appended in input order so the size invariant holds.
        seen: set[int] = set()
        ranked: list[RetrievedChunk] = []
        for r in results:
            if not isinstance(r, dict):
                continue
            idx = r.get("index")
            if not isinstance(idx, int) or idx in seen:
                continue
            if 0 <= idx < len(chunks):
                ranked.append(chunks[idx])
                seen.add(idx)
        for i, c in enumerate(chunks):
            if i not in seen:
                ranked.append(c)
        return ranked

    def _truncate(self, text: str) -> str:
        if not text:
            return ""
        if len(text) <= self._max_chunk_chars:
            return text
        return text[: self._max_chunk_chars]
