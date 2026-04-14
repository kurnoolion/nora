"""Sentence-transformers embedding provider.

Concrete EmbeddingProvider using the sentence-transformers library.
Runs locally — no API key needed.

Models to try (all from sentence-transformers / HuggingFace):
  - all-MiniLM-L6-v2       (384d, 23M params, fast)
  - all-mpnet-base-v2      (768d, 110M params, better quality)
  - BAAI/bge-large-en-v1.5 (1024d, 335M params, top retrieval)
  - BAAI/bge-small-en-v1.5 (384d, 33M params, good speed/quality)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SentenceTransformerEmbedder:
    """Embedding provider using sentence-transformers.

    Satisfies the EmbeddingProvider protocol.

    Args:
        model_name: HuggingFace model name or local path.
        device: 'cpu', 'cuda', or 'mps'.
        batch_size: Batch size for encoding.
        normalize: Whether to L2-normalize embeddings.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        device: str = "cpu",
        batch_size: int = 64,
        normalize: bool = True,
    ) -> None:
        from sentence_transformers import SentenceTransformer

        self._model_name = model_name
        self._batch_size = batch_size
        self._normalize = normalize

        logger.info(f"Loading sentence-transformers model '{model_name}' on {device}")
        self._model = SentenceTransformer(model_name, device=device)
        self._dimension = self._model.get_embedding_dimension()
        logger.info(
            f"Model loaded: {model_name} ({self._dimension}d, "
            f"device={device}, normalize={normalize})"
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts."""
        if not texts:
            return []

        embeddings = self._model.encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=self._normalize,
            show_progress_bar=len(texts) > 100,
        )
        return embeddings.tolist()

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query.

        sentence-transformers does not differentiate query vs document
        encoding for most models, so this just calls embed().
        For models that do (e.g., BGE with 'Represent this sentence:' prefix),
        the prefix is handled by the model's built-in prompt configuration.
        """
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name
