"""Ollama embedding provider for local model inference.

Uses Ollama's `/api/embeddings` endpoint. Same offline-friendly distribution
path as the existing Ollama LLM provider — once Ollama is set up, embedding
models are pulled with `ollama pull <model>` (no separate HuggingFace cache).

Recommended embedding models:
  - nomic-embed-text     (768d, ~270MB, balanced)
  - mxbai-embed-large    (1024d, ~670MB, top quality)
  - all-minilm           (384d, ~45MB, fastest)

Pull with: `ollama pull nomic-embed-text`

Satisfies the EmbeddingProvider protocol — no inheritance.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _build_opener(base_url: str) -> urllib.request.OpenerDirector:
    """Build a urllib opener that bypasses HTTP_PROXY for loopback URLs.

    Without this, HTTP_PROXY / http_proxy env vars (common on corporate
    work laptops) cause urllib to route http://localhost:11434 through the
    proxy → connection times out. curl ignores *_proxy for localhost by
    default; urllib does not.
    """
    host = (urlsplit(base_url).hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class OllamaEmbedder:
    """Embedding provider using Ollama's /api/embeddings endpoint.

    Satisfies the EmbeddingProvider protocol.

    Args:
        model_name: Ollama embedding model (e.g., "nomic-embed-text").
        base_url: Ollama server URL.
        timeout: Per-request timeout in seconds.
        normalize: L2-normalize embeddings before returning.
    """

    def __init__(
        self,
        model_name: str = "nomic-embed-text",
        base_url: str = _DEFAULT_BASE_URL,
        timeout: int = 60,
        normalize: bool = True,
    ) -> None:
        self._model_name = model_name
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._normalize = normalize
        self._opener = _build_opener(self._base_url)
        self._dimension: int | None = None

        # Verify reachability + check that the model is pulled. Match
        # OllamaProvider's __init__ behavior: warn (don't fail) if the model
        # isn't listed yet, since `ollama pull` may be in progress.
        try:
            req = urllib.request.Request(f"{self._base_url}/api/tags")
            with self._opener.open(req, timeout=5) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Cannot connect to Ollama at {self._base_url}. "
                f"Is Ollama running? (ollama serve)\n"
                f"Error: {e}"
            ) from e

        models = [m.get("name", "") for m in data.get("models", [])]
        # Allow exact match or "name:tag" prefix
        present = model_name in models or any(
            m.startswith(f"{model_name}:") for m in models
        )
        if not present:
            available = ", ".join(models) if models else "none"
            logger.warning(
                f"Embedding model '{model_name}' not found on Ollama server. "
                f"Available: {available}. Pull with: ollama pull {model_name}"
            )
        else:
            logger.info(
                f"OllamaEmbedder ready: model={model_name}, server={self._base_url}, "
                f"normalize={normalize}"
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts.

        Ollama's /api/embeddings is single-text per call; we loop. For large
        batches this is slower than sentence-transformers' native batching,
        but it keeps the wire protocol simple and matches what `ollama pull`
        ships out of the box.
        """
        if not texts:
            return []
        vectors: list[list[float]] = []
        for i, text in enumerate(texts):
            payload = json.dumps(
                {"model": self._model_name, "prompt": text}
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{self._base_url}/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with self._opener.open(req, timeout=self._timeout) as resp:
                    data = json.loads(resp.read())
            except urllib.error.URLError as e:
                raise RuntimeError(
                    f"Ollama embedding request failed (text {i}): {e}"
                ) from e
            vec = data.get("embedding", [])
            if not vec:
                raise RuntimeError(
                    f"Ollama returned empty embedding for input #{i}; "
                    f"check that '{self._model_name}' is an embedding model "
                    f"(LLM models like gemma3:12b don't expose /api/embeddings output)"
                )
            if self._normalize:
                vec = _l2_normalize(vec)
            vectors.append(vec)
            if self._dimension is None:
                self._dimension = len(vec)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query.

        Ollama's /api/embeddings does not differentiate query vs document
        encoding, so this just delegates to embed().
        """
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            # Probe with a short non-empty string to discover dimensionality.
            # Empty strings are rejected by some embedding models (Qwen3 family
            # returns {"embedding":[]} for ""); use a single ASCII character.
            self.embed(["a"])
        return self._dimension or 0

    @property
    def model_name(self) -> str:
        return self._model_name
