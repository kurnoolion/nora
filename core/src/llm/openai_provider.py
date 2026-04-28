"""OpenAI-compatible LLM provider for cloud APIs.

Works with any OpenAI Chat Completions endpoint: OpenRouter, Together AI,
DeepInfra, Groq, Fireworks, vLLM/SGLang/text-generation-inference, and
OpenAI itself. Configured via constructor args or environment variables.

Satisfies the LLMProvider Protocol — no inheritance, structural typing only.

Environment variables (used when the matching constructor arg is None):
    NORA_LLM_BASE_URL — e.g. https://openrouter.ai/api/v1
    NORA_LLM_API_KEY  — bearer token
    NORA_LLM_MODEL    — provider-qualified model name, e.g. "qwen/qwen3-235b-a22b"

Usage:
    provider = OpenAICompatibleProvider(
        model="qwen/qwen3-235b-a22b",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
    answer = provider.complete("What is T3402?", system="You are a telecom expert.")

Stdlib urllib only — no `httpx` / `openai` SDK dependency. Matches the
OllamaProvider pattern so the module installs cleanly on offline /
locked-down hosts that nonetheless have outbound HTTPS.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300

# Env-var names mirror the NORA_STANDARDS_SOURCE pattern.
ENV_BASE_URL = "NORA_LLM_BASE_URL"
ENV_API_KEY = "NORA_LLM_API_KEY"
ENV_MODEL = "NORA_LLM_MODEL"


class OpenAICompatibleProvider:
    """LLM provider for OpenAI-compatible chat completion APIs.

    Args:
        model: Provider-qualified model name (e.g. "qwen/qwen3-235b-a22b").
            Falls back to NORA_LLM_MODEL env var if None.
        base_url: API root URL ending in `/v1` (e.g. https://openrouter.ai/api/v1).
            Falls back to NORA_LLM_BASE_URL env var if None.
        api_key: Bearer token. Falls back to NORA_LLM_API_KEY env var if None.
        timeout: Per-request timeout in seconds (default: 300; cloud LLMs need it).
        extra_headers: Optional headers merged into every request (e.g. OpenRouter's
            HTTP-Referer / X-Title for analytics).
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        # Constructor args win over env vars; env vars are fallback only.
        resolved_model = model or os.environ.get(ENV_MODEL)
        resolved_base_url = base_url or os.environ.get(ENV_BASE_URL)
        resolved_api_key = api_key or os.environ.get(ENV_API_KEY)

        if not resolved_model:
            raise ValueError(
                f"OpenAICompatibleProvider needs `model` "
                f"(constructor arg or {ENV_MODEL} env var)."
            )
        if not resolved_base_url:
            raise ValueError(
                f"OpenAICompatibleProvider needs `base_url` "
                f"(constructor arg or {ENV_BASE_URL} env var)."
            )
        if not resolved_api_key:
            raise ValueError(
                f"OpenAICompatibleProvider needs `api_key` "
                f"(constructor arg or {ENV_API_KEY} env var)."
            )

        self._model = resolved_model
        self._base_url = resolved_base_url.rstrip("/")
        self._api_key = resolved_api_key
        self._timeout = timeout
        self._extra_headers = dict(extra_headers or {})
        self._call_count = 0
        self._last_call_stats: dict = {}

        logger.info(
            f"OpenAICompatibleProvider ready: model={self._model}, "
            f"base_url={self._base_url}"
        )

    def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a prompt to {base_url}/chat/completions. Returns the assistant content."""
        self._call_count += 1

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **self._extra_headers,
        }
        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )

        logger.debug(
            f"LLM call #{self._call_count}: model={self._model}, "
            f"prompt={len(prompt)} chars, system={len(system)} chars"
        )

        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            # Surface the API's error body — providers usually return JSON
            # with a useful "error.message" field.
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = "<no body>"
            logger.error(f"LLM HTTP {e.code} {e.reason}: {err_body[:400]}")
            raise RuntimeError(
                f"LLM HTTP {e.code} {e.reason}: {err_body[:400]}"
            ) from e
        except urllib.error.URLError as e:
            logger.error(f"LLM network error: {e}")
            raise RuntimeError(f"LLM network error: {e}") from e

        elapsed_s = time.time() - t0

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(
                f"LLM returned no choices: {json.dumps(data)[:400]}"
            )
        message = choices[0].get("message") or {}
        content = message.get("content", "") or ""

        usage = data.get("usage") or {}
        eval_count = int(usage.get("completion_tokens", 0) or 0)
        prompt_eval_count = int(usage.get("prompt_tokens", 0) or 0)
        tok_per_s = (eval_count / elapsed_s) if elapsed_s > 0 else 0.0

        self._last_call_stats = {
            "total_duration_s": elapsed_s,
            "eval_count": eval_count,
            "prompt_eval_count": prompt_eval_count,
            "tokens_per_second": round(tok_per_s, 1),
            "model": self._model,
        }

        if eval_count or elapsed_s > 0.5:
            logger.info(
                f"LLM call #{self._call_count}: "
                f"{eval_count} tokens in {elapsed_s:.1f}s "
                f"({tok_per_s:.1f} tok/s)"
            )

        return content

    @property
    def model(self) -> str:
        return self._model

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def last_call_stats(self) -> dict:
        return dict(self._last_call_stats)
