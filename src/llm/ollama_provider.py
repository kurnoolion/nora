"""Ollama LLM provider for local model inference.

Connects to a local Ollama server via its HTTP API.
Satisfies the LLMProvider Protocol — no inheritance needed.

Usage:
    from src.llm.ollama_provider import OllamaProvider

    provider = OllamaProvider(model="gemma4:e4b")
    answer = provider.complete("What is T3402?", system="You are a telecom expert.")

Requires:
    - Ollama installed and running: https://ollama.com
    - Model pulled: ollama pull gemma4:e4b
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaProvider:
    """LLM provider using a local Ollama server.

    Satisfies the LLMProvider protocol.

    Args:
        model: Ollama model name (e.g., "gemma4:e4b").
        base_url: Ollama server URL (default: http://localhost:11434).
        timeout: Request timeout in seconds (default: 300 for CPU inference).
        think: Enable thinking/reasoning mode if model supports it (default: False).
    """

    def __init__(
        self,
        model: str = "gemma4:e4b",
        base_url: str = _DEFAULT_BASE_URL,
        timeout: int = 300,
        think: bool = False,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._think = think
        self._call_count = 0

        # Verify server is reachable
        try:
            req = urllib.request.Request(f"{self._base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]
                if model not in models:
                    available = ", ".join(models) if models else "none"
                    logger.warning(
                        f"Model '{model}' not found on Ollama server. "
                        f"Available: {available}. "
                        f"Pull it with: ollama pull {model}"
                    )
                else:
                    logger.info(f"OllamaProvider ready: model={model}, server={base_url}")
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Cannot connect to Ollama at {base_url}. "
                f"Is Ollama running? Start with: ollama serve\n"
                f"Error: {e}"
            ) from e

    def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a prompt to the local Ollama model.

        Uses the /api/chat endpoint with messages format.
        """
        self._call_count += 1

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        if self._think:
            payload["think"] = True

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        logger.debug(
            f"Ollama call #{self._call_count}: model={self._model}, "
            f"prompt={len(prompt)} chars, system={len(system)} chars"
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            logger.error(f"Ollama request failed: {e}")
            raise RuntimeError(f"Ollama request failed: {e}") from e

        message = data.get("message", {})
        content = message.get("content", "")

        # Log performance stats
        total_ns = data.get("total_duration", 0)
        eval_count = data.get("eval_count", 0)
        eval_ns = data.get("eval_duration", 0)

        if total_ns > 0:
            total_s = total_ns / 1e9
            tok_per_s = eval_count / (eval_ns / 1e9) if eval_ns > 0 else 0
            logger.info(
                f"Ollama call #{self._call_count}: "
                f"{eval_count} tokens in {total_s:.1f}s "
                f"({tok_per_s:.1f} tok/s)"
            )

        return content

    @property
    def model(self) -> str:
        return self._model

    @property
    def call_count(self) -> int:
        return self._call_count
