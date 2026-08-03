"""Permanent-refusal detection for LLM responses (shared by the batch
enrich lane and the sira-query service).

Some endpoints deterministically refuse certain inputs: instead of an
answer they return a fixed notice, and retrying the SAME endpoint can
never succeed. Callers detect that shape here and, when a fallback
model is configured, give the request one shot there instead.

A response is a permanent refusal when BOTH hold:
  1. its stripped text starts with (or equals) a configured marker, and
  2. it contains no parseable JSON object/array (a genuine answer that
     merely quotes a marker still carries its JSON payload).

Marker values are deployment-specific and live ONLY in local
(gitignored) env files (`NORA_LLM_REFUSAL_MARKERS`, `||`-separated) —
never in committed code, tests, or docs.

Import note: the batch lane runs this module copied flat into the SIRA
clone's scripts/ dir (see install_configs.sh), so it must stay
dependency-free and importable as a top-level module.
"""

from __future__ import annotations

import json
import re

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
