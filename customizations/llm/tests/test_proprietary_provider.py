"""Smoke tests for the ProprietaryLLMProvider stub."""

from __future__ import annotations

import pytest

from customizations.llm.proprietary_provider import ProprietaryLLMProvider
from core.src.llm.base import LLMProvider


def test_satisfies_llm_provider_protocol():
    """The stub must structurally satisfy LLMProvider for swap-by-instance (NFR-6)."""
    provider = ProprietaryLLMProvider(
        model="proprietary-v1",
        endpoint="https://llm.example/api",
    )
    assert isinstance(provider, LLMProvider)


def test_complete_raises_until_implemented():
    """Default complete() raises NotImplementedError, not silently returning empty."""
    provider = ProprietaryLLMProvider()
    with pytest.raises(NotImplementedError):
        provider.complete("test prompt")


def test_call_count_initialized():
    """call_count is initialized to 0 (matches OllamaProvider / MockLLMProvider shape)."""
    provider = ProprietaryLLMProvider()
    assert provider.call_count == 0


def test_constructor_kwargs_accepted():
    """The constructor accepts arbitrary kwargs so deployments can carry custom config."""
    provider = ProprietaryLLMProvider(
        model="m",
        endpoint="e",
        api_key="redacted",
        timeout_s=120,
    )
    assert provider.model == "m"
    assert provider.endpoint == "e"
