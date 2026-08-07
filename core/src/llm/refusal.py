"""Permanent-refusal detection + fallback provider for core LLM lanes.

Some endpoints deterministically refuse certain inputs: instead of an
answer they return a fixed notice, and retrying the SAME endpoint can
never succeed. The synthesis lane surfaces that
notice as the user-visible answer — this module detects the shape and
gives the call one shot on a fallback model instead.

A response is a permanent refusal when BOTH hold:
  1. its stripped text starts with (or equals) a configured marker, and
  2. it contains no parseable JSON object/array (a genuine answer that
     merely quotes a marker still carries its JSON payload).

Marker values are deployment-specific and live ONLY in local
(gitignored) env files (``NORA_LLM_REFUSAL_MARKERS``, ``||``-separated)
— never in committed code, tests, or docs.

Twin note: the detection functions are a deliberate copy of
``sandbox/llm_refusal.py`` (same rules, same env var). Core must not
import sandbox (D-111 boundary) and the sandbox copy must stay
flat-copyable into the SIRA clone, so the two files are kept in sync by
hand — change one, change both.

Config (env vars, wiring-level like the sandbox lanes' fallbacks):
  NORA_LLM_REFUSAL_MARKERS      ||-separated refusal-notice prefixes
  NORA_LLM_FALLBACK_BASE_URL    OpenAI-compatible base URL — same
                                convention as NORA_LLM_BASE_URL
                                (includes /v1)
  NORA_LLM_FALLBACK_MODEL       model name on the fallback endpoint
  NORA_LLM_FALLBACK_API_KEY     optional bearer token
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

MARKER_SEP = "||"

_THINK_SPAN_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE_RE = re.compile(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", re.S)


def parse_markers(env_val: str | None) -> tuple[str, ...]:
    """`NORA_LLM_REFUSAL_MARKERS` → marker tuple. Markers are separated
    by `||` (refusal notices can contain commas); blanks dropped."""
    if not env_val:
        return ()
    return tuple(m.strip() for m in env_val.split(MARKER_SEP) if m.strip())


def _contains_json_payload(raw: str) -> bool:
    """True when the text carries a parseable JSON object or array —
    fenced blocks first, then the outermost brace/bracket span."""
    raw = _THINK_SPAN_RE.sub("", raw)
    candidates = [m.group(1) for m in _FENCE_RE.finditer(raw)]
    for open_c, close_c in (("{", "}"), ("[", "]")):
        first, last = raw.find(open_c), raw.rfind(close_c)
        if 0 <= first < last:
            candidates.append(raw[first:last + 1])
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except ValueError:
            continue
        if isinstance(parsed, (dict, list)):
            return True
    return False


def is_permanent_refusal(raw: str | None, markers: tuple[str, ...]) -> bool:
    """Marker-prefixed AND payload-free → permanent refusal. Empty
    responses are NOT refusals (transient endpoint trouble retries)."""
    if not markers or not raw:
        return False
    text = raw.strip()
    if not any(text.startswith(m) for m in markers):
        return False
    return not _contains_json_payload(raw)


class RefusalFallbackProvider:
    """LLMProvider decorator: primary first; a permanently-refused call
    is retried ONCE on the fallback provider. Satisfies the LLMProvider
    Protocol structurally, so it drops in anywhere a provider is used
    (synthesis, curation chat, judge).

    `used` counts fallback-answered calls this process — visibility
    without logging content (NFR-8).
    """

    def __init__(self, primary, fallback, markers: tuple[str, ...]):
        self._primary = primary
        self._fallback = fallback
        self._markers = markers
        self.used = 0
        # Model that produced the LAST answer (primary's until a call
        # falls back) — callers stamping provenance (e.g. the synthesis
        # epilogue) read this instead of assuming one model.
        self.last_model = getattr(primary, "model", "")

    @property
    def model(self):
        """Provider-surface parity: the primary model's name."""
        return getattr(self._primary, "model", "")

    def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        answer = self._primary.complete(
            prompt, system=system, temperature=temperature,
            max_tokens=max_tokens,
        )
        if not is_permanent_refusal(answer, self._markers):
            self.last_model = getattr(self._primary, "model", "")
            return answer
        self.used += 1
        logger.info(
            "LLM permanently refused — routing call to fallback "
            "(%d fallback call(s) this process)", self.used,
        )
        answer = self._fallback.complete(
            prompt, system=system, temperature=temperature,
            max_tokens=max_tokens,
        )
        self.last_model = getattr(self._fallback, "model", "")
        return answer


def maybe_wrap_with_refusal_fallback(llm, timeout: int = 600):
    """Wrap `llm` with the refusal fallback when fully configured.

    Returns `llm` unchanged when: it is already wrapped (idempotent —
    callers may wrap a provider that a builder already wrapped), markers
    or fallback endpoint/model are unset (feature off), or `llm` is the
    mock provider (nothing real to fall back from). Partial config logs
    a warning so a half-wired env file is visible instead of silently
    off.
    """
    if isinstance(llm, RefusalFallbackProvider):
        return llm
    markers = parse_markers(os.getenv("NORA_LLM_REFUSAL_MARKERS"))
    base_url = os.getenv("NORA_LLM_FALLBACK_BASE_URL", "").strip()
    model = os.getenv("NORA_LLM_FALLBACK_MODEL", "").strip()
    if not (markers or base_url):
        return llm
    if not (markers and base_url and model):
        logger.warning(
            "Refusal fallback partially configured — need "
            "NORA_LLM_REFUSAL_MARKERS + NORA_LLM_FALLBACK_BASE_URL + "
            "NORA_LLM_FALLBACK_MODEL; running WITHOUT fallback",
        )
        return llm
    if getattr(llm, "_is_mock", False):
        return llm
    from core.src.llm.openai_provider import OpenAICompatibleProvider

    fallback = OpenAICompatibleProvider(
        model=model,
        base_url=base_url,
        api_key=os.getenv("NORA_LLM_FALLBACK_API_KEY", "") or None,
        timeout=timeout,
    )
    logger.info("Refusal fallback active (fallback model: %s)", model)
    return RefusalFallbackProvider(llm, fallback, markers)
