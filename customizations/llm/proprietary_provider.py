"""Proprietary LLM provider stub.

This file is the human-completion seam for production deployment with a
proprietary on-premise LLM (per D-024 and PROJECT.md production constraints).
It satisfies the `LLMProvider` Protocol shape so the rest of the pipeline can
target it via swap-by-instance (NFR-6, D-006) once a deployment-specific
implementation is filled in.

The default `complete()` raises `NotImplementedError` so that misconfigured
production deployments fail loudly rather than silently falling back to a mock.
"""

from __future__ import annotations


class ProprietaryLLMProvider:
    """Placeholder `LLMProvider` for proprietary on-premise deployments.

    Replace `complete()` with a concrete call to the deployment's LLM endpoint.
    The signature must continue to match the `LLMProvider` Protocol defined in
    `core/src/llm/base.py`.
    """

    def __init__(self, model: str = "", endpoint: str = "", **kwargs):
        self.model = model
        self.endpoint = endpoint
        self.call_count = 0

    def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a prompt and return the text response.

        Implementations should call the proprietary LLM endpoint and return
        the completion text. The default raises so that unconfigured production
        deployments fail fast (NFR-1: no silent fallback to external services).
        """
        raise NotImplementedError(
            "ProprietaryLLMProvider.complete must be implemented per deployment. "
            "See customizations/llm/README.md for guidance."
        )
