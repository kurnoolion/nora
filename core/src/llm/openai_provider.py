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
import re
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300

# Reasoning ("thinking") models sometimes inline their chain-of-thought in the
# message content as <think>…</think> (or <thinking>/<reasoning>) rather than
# splitting it into a separate `reasoning_content` field. We only ever want the
# final answer, so strip those blocks. Endpoints that already separate reasoning
# never hit this; plain answers contain no such tags and pass through unchanged.
_THINK_TAGS = ("think", "thinking", "reason", "reasoning")
_THINK_BLOCK_RE = re.compile(
    r"<(" + "|".join(_THINK_TAGS) + r")\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)
_THINK_CLOSE_RE = re.compile(
    r"</(?:" + "|".join(_THINK_TAGS) + r")\s*>",
    re.IGNORECASE,
)

# Some reasoning models emit UNtagged chain-of-thought — plain prose before the
# answer, with no <think> delimiter to match. For those, a prompt can instruct
# the model to print this marker on its own line just before the final answer;
# _strip_reasoning then drops everything up to (and including) the last marker.
#
# This is a per-model opt-in: most models (Qwen3, Gemma, …) skip their thinking
# natively, so the sentinel is OFF by default and only the prompt+strip pair is
# activated — per stack — by setting NORA_LLM_REASONING_SENTINEL=1 for the model
# that needs it. Prompts import FINAL_ANSWER_MARKER and REASONING_SENTINEL_ENABLED
# so the instruction and the strip stay in lockstep.
FINAL_ANSWER_MARKER = "===FINAL_ANSWER==="
ENV_REASONING_SENTINEL = "NORA_LLM_REASONING_SENTINEL"
REASONING_SENTINEL_ENABLED = (
    os.getenv(ENV_REASONING_SENTINEL, "").strip().lower() in ("1", "true", "yes", "on")
)


def _strip_reasoning(text: str) -> str:
    """Remove reasoning from model output. Handles (1) a prompt-driven
    FINAL_ANSWER_MARKER sentinel — only when REASONING_SENTINEL_ENABLED — and
    (2) inline <think>…</think> tag blocks (always, harmless when absent).
    Idempotent and safe on normal answers (no marker/tags → returned unchanged
    apart from surrounding whitespace)."""
    if not text:
        return text
    # Sentinel: the only reliable signal for untagged reasoning, but opt-in per
    # model — most models don't need it and shouldn't have output reshaped.
    if REASONING_SENTINEL_ENABLED:
        marker_at = text.rfind(FINAL_ANSWER_MARKER)
        if marker_at != -1:
            text = text[marker_at + len(FINAL_ANSWER_MARKER):]
    cleaned = _THINK_BLOCK_RE.sub("", text)
    # Some servers drop the opening tag and return "<reasoning…></think>answer"
    # or "reasoning…</think>answer" — a dangling close with no matching open.
    # Everything up to and including the last such close tag is reasoning.
    matches = list(_THINK_CLOSE_RE.finditer(cleaned))
    if matches:
        cleaned = cleaned[matches[-1].end():]
    return cleaned.strip()

# Env-var names mirror the NORA_STANDARDS_SOURCE pattern.
ENV_BASE_URL = "NORA_LLM_BASE_URL"
ENV_API_KEY = "NORA_LLM_API_KEY"
ENV_MODEL = "NORA_LLM_MODEL"
# Opt-in diagnostic: log each response's raw content + reasoning fields at INFO.
ENV_DEBUG_RAW = "NORA_LLM_DEBUG_RAW"


class OpenAICompatibleProvider:
    """LLM provider for OpenAI-compatible chat completion APIs.

    Args:
        model: Provider-qualified model name (e.g. "qwen/qwen3-235b-a22b").
            Falls back to NORA_LLM_MODEL env var if None.
        base_url: API root URL ending in `/v1` (e.g. https://openrouter.ai/api/v1).
            Falls back to NORA_LLM_BASE_URL env var if None.
        api_key: Bearer token. Falls back to NORA_LLM_API_KEY env var if None.
            Optional — when empty, no Authorization header is sent (for
            self-hosted endpoints that accept unauthenticated requests).
        timeout: Per-request timeout in seconds (default: 300; cloud LLMs need it).
        extra_headers: Optional headers merged into every request (e.g. OpenRouter's
            HTTP-Referer / X-Title for analytics).
        reasoning: Reasoning effort for models that support it — one of
            "none" / "low" / "medium" / "high". Sent as the OpenAI-standard
            `reasoning_effort` field; vLLM maps it to the model's
            `enable_thinking` chat-template kwarg ("none" disables thinking).
            None omits the field entirely, which is the pre-existing behaviour.
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
        extra_headers: dict[str, str] | None = None,
        reasoning: str | None = None,
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
        # api_key is optional: self-hosted OpenAI-compat servers (vLLM, sglang,
        # Ollama, llama.cpp) commonly accept unauthenticated requests. When
        # empty we simply omit the Authorization header (see `complete`).

        self._model = resolved_model
        self._base_url = resolved_base_url.rstrip("/")
        self._api_key = resolved_api_key or ""
        self._timeout = timeout
        self._extra_headers = dict(extra_headers or {})
        self._reasoning = (reasoning or "").strip() or None
        self._call_count = 0
        self._last_call_stats: dict = {}

        logger.info(
            f"OpenAICompatibleProvider ready: model={self._model}, "
            f"base_url={self._base_url}, reasoning={self._reasoning or '<default>'}"
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
        # Reasoning effort, when the caller asked for one. vLLM accepts the
        # OpenAI-standard field and injects the model's `enable_thinking`
        # chat-template kwarg itself ("none" -> false, low/medium/high -> true),
        # so no per-model mapping table is needed here. Older servers that
        # reject `reasoning_effort` take the equivalent:
        #     payload["chat_template_kwargs"] = {"enable_thinking": False}
        if self._reasoning:
            payload["reasoning_effort"] = self._reasoning

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            **self._extra_headers,
        }
        # Only authenticate when a key is configured; keyless endpoints reject
        # (or ignore) a bogus `Bearer ` header.
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
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
        raw_content = message.get("content", "") or ""
        # Opt-in raw dump (NORA_LLM_DEBUG_RAW=1) to inspect a model's reasoning
        # delimiters when chain-of-thought leaks past _strip_reasoning. Off by
        # default — raw model output may contain corpus content (no proprietary
        # content in logs otherwise). `%r` preserves exact/unicode markers.
        if os.environ.get(ENV_DEBUG_RAW):
            reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
            logger.info(
                "LLM raw content (pre-strip, %d chars): %r | reasoning_content(%d): %r",
                len(raw_content), raw_content[:800],
                len(reasoning), reasoning[:200],
            )
        content = _strip_reasoning(raw_content)

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
    def reasoning(self) -> str:
        """Reasoning effort sent with each call; empty when unset."""
        return self._reasoning or ""

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def last_call_stats(self) -> dict:
        return dict(self._last_call_stats)
