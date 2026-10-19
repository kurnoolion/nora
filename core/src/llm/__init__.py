"""LLM providers.

The package re-exports the reasoning-output contract constants so consumers
outside this package depend on the package, not on a specific provider
module (D-006).
"""

from core.src.llm.openai_provider import (
    FINAL_ANSWER_MARKER,
    REASONING_SENTINEL_ENABLED,
)

__all__ = ["FINAL_ANSWER_MARKER", "REASONING_SENTINEL_ENABLED"]
