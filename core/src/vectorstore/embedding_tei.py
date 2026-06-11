"""TEI embedding provider — HuggingFace Text Embeddings Inference.

Uses TEI's native ``/embed`` endpoint: one POST per batch, server-side
forward-pass parallelism. Symmetric with ``TEIReranker`` in
``core.src.query.reranker``.

Wire shape:
    POST {base_url}/embed
    body: {"inputs": [<str>, ...], "truncate": true}
    response: [[<float>, ...], ...]  # one vector per input

Client-side auto-batching at ``max_batch_size`` (default 32, matching
TEI's ``--max-client-batch-size`` default) so the pipeline can hand the
embedder any number of texts without 422ing on oversized requests.
``truncate=true`` on every payload so inputs exceeding the model's max
sequence length get silently truncated server-side instead of 422ing.

Satisfies the EmbeddingProvider protocol — no inheritance.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


_DEFAULT_MAX_INPUT_CHARS = 8000
"""Char-level cap on per-text input — wire-size safety net.

TEI's ``truncate=true`` handles token-level overflow server-side. This
cap is a separate concern: keeps payloads from ballooning when a few
chunks happen to be enormous, which would otherwise transit megabytes
per batch. Tunable via ``TEIEmbedder(max_input_chars=...)``.
"""


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class TEIEmbedder:
    """Embedding provider using TEI's native ``/embed`` endpoint.

    Satisfies the EmbeddingProvider protocol.

    Args:
        model_name: Informational only — TEI is single-model per instance
            (the model is set at TEI startup). Logged at init, included
            in ``model_name`` property.
        base_url: TEI gateway base — the URL prefix whose ``/embed``
            route forwards to the TEI service. The request goes to
            ``{base_url}/embed``.
        api_key: Optional bearer token, sent as ``Authorization: Bearer
            <key>`` if non-empty.
        timeout: Per-request timeout in seconds.
        normalize: L2-normalize each returned vector. TEI does not
            normalize by default for most cross-encoder embedders;
            downstream cosine-similarity stages assume unit vectors.
        max_input_chars: Truncate inputs longer than this *before*
            sending. Wire-size safety net only; server-side
            ``truncate=true`` handles token-level overflow.
        max_batch_size: Maximum number of texts per HTTP POST. Default
            32 to match TEI's ``--max-client-batch-size`` default; raise
            (after raising it on the server too) if your TEI instance
            accepts larger batches.
    """

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str = "",
        *,
        timeout: int = 60,
        normalize: bool = True,
        max_input_chars: int = _DEFAULT_MAX_INPUT_CHARS,
        max_batch_size: int = 32,
    ) -> None:
        if not base_url:
            raise ValueError(
                "TEIEmbedder: base_url is required (no sensible default)."
            )
        self._model_name = model_name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or ""
        self._timeout = timeout
        self._normalize = normalize
        self._max_input_chars = max_input_chars
        self._max_batch_size = max(1, max_batch_size)
        self._dimension: int | None = None
        self._truncated_count = 0

        # Reachability check at init — one small embed call. Verifies
        # the gateway routes correctly, the TEI service is up, and
        # captures the model dimension as a side effect. Mirrors
        # OllamaEmbedder's behavior of failing fast on init.
        try:
            vec = self._post_batch(["__nora_tei_init_probe__"])[0]
        except urllib.error.URLError as exc:
            raise ConnectionError(
                f"TEIEmbedder: cannot reach {self._base_url}/embed "
                f"({exc!r}). Check NORA_EMBEDDING_BASE_URL + gateway routing."
            ) from exc
        self._dimension = len(vec)
        logger.info(
            "TEIEmbedder ready: model=%s, base_url=%s, dim=%d, "
            "max_batch_size=%d, normalize=%s",
            model_name, self._base_url, self._dimension,
            self._max_batch_size, normalize,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via one POST per ``max_batch_size`` chunk.

        Cross-batch consistency is preserved because TEI's embedding model
        is stateless — the vector for a text depends only on the text,
        not on what else is in the batch.

        Raises:
            urllib.error.URLError: Network failure (connection refused,
                timeout, DNS failure). Aborts the whole call — partial
                results are not returned.
            urllib.error.HTTPError: TEI returned a 4xx/5xx. Error body is
                included in the exception for the caller to inspect.
        """
        if not texts:
            return []
        prepared = [self._truncate_for_wire(t, i) for i, t in enumerate(texts)]
        vectors: list[list[float]] = []
        bs = self._max_batch_size
        n_batches = (len(prepared) + bs - 1) // bs
        for batch_i, batch_start in enumerate(range(0, len(prepared), bs)):
            batch_texts = prepared[batch_start : batch_start + bs]
            batch_vecs = self._post_batch(batch_texts)
            if len(batch_vecs) != len(batch_texts):
                raise RuntimeError(
                    f"TEIEmbedder: batch {batch_i + 1}/{n_batches} "
                    f"returned {len(batch_vecs)} vectors for "
                    f"{len(batch_texts)} inputs — protocol mismatch."
                )
            vectors.extend(batch_vecs)
        if self._dimension is None and vectors:
            self._dimension = len(vectors[0])
        if self._normalize:
            vectors = [_l2_normalize(v) for v in vectors]
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query. TEI embedders don't use asymmetric
        query/document prefixes by default; same path as ``embed``."""
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError(
                "TEIEmbedder: dimension unknown — call embed() first."
            )
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    def _truncate_for_wire(self, text: str, idx: int) -> str:
        if not text:
            return ""
        if len(text) <= self._max_input_chars:
            return text
        logger.warning(
            "TEIEmbedder: text %d length %d > max_input_chars %d; "
            "truncating (%d chars dropped). truncate=true also set "
            "server-side as token-level safety.",
            idx, len(text), self._max_input_chars,
            len(text) - self._max_input_chars,
        )
        self._truncated_count += 1
        return text[: self._max_input_chars]

    def _post_batch(self, texts: list[str]) -> list[list[float]]:
        """One POST to {base_url}/embed; returns N vectors for N inputs."""
        payload = {"inputs": texts, "truncate": True}
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(
            f"{self._base_url}/embed",
            data=body, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                err_body = "<unreadable>"
            raise urllib.error.HTTPError(
                exc.url, exc.code,
                f"{exc.msg} — body: {err_body}",
                exc.headers, None,
            ) from exc

        # Native TEI returns a flat array of arrays; some forks wrap it
        # as {"embeddings": [...]}. Accept either.
        if isinstance(data, list):
            vectors = data
        elif isinstance(data, dict) and isinstance(data.get("embeddings"), list):
            vectors = data["embeddings"]
        else:
            raise RuntimeError(
                f"TEIEmbedder: unexpected response shape "
                f"({type(data).__name__}) — expected array-of-arrays or "
                f"{{'embeddings': [...]}}."
            )

        # Validate each entry is a list of numbers.
        out: list[list[float]] = []
        for i, v in enumerate(vectors):
            if not isinstance(v, list) or not all(
                isinstance(x, (int, float)) for x in v
            ):
                raise RuntimeError(
                    f"TEIEmbedder: vector {i} is not a numeric list."
                )
            out.append([float(x) for x in v])
        return out
