"""LLM provider abstraction layer.

Defines the LLMProvider protocol that all LLM integrations implement.
This is the ONLY interface the rest of the codebase uses — no module
outside src/llm/ should import any LLM SDK directly.

## How to add a new LLM provider

1. Create a new file in src/llm/ (e.g., `internal_provider.py`)
2. Implement a class that satisfies the LLMProvider protocol:

    class InternalLLMProvider:
        def __init__(self, base_url: str, api_key: str, model: str = "default"):
            # your init
            ...

        def complete(
            self,
            prompt: str,
            system: str = "",
            temperature: float = 0.0,
            max_tokens: int = 4096,
        ) -> str:
            # Call your API, return the text response
            ...

3. No base class inheritance needed — just match the method signature.
   Python's structural typing (Protocol) handles the rest.

4. To use it, pass your provider instance to any component that
   takes an LLMProvider parameter:

    from src.llm.internal_provider import InternalLLMProvider
    provider = InternalLLMProvider(base_url="...", api_key="...")
    extractor = FeatureExtractor(provider)

## Design notes

- complete() returns plain text, not structured objects. The caller
  is responsible for parsing JSON from the response. This keeps the
  protocol minimal and avoids forcing structured output on providers
  that don't support it.
- temperature=0.0 by default for deterministic extraction tasks.
- max_tokens caps the response length. Providers should respect this
  or use their closest equivalent.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM providers.

    Any class with a matching `complete` method satisfies this protocol.
    No inheritance required.
    """

    def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a prompt to the LLM and return the text response.

        Args:
            prompt: The user message / main prompt.
            system: System prompt (provider-specific handling).
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum tokens in the response.

        Returns:
            The LLM's text response.
        """
        ...
