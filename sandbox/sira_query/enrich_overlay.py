"""Enrichment-corrections overlay — load, filter, apply (sira-enrichment-review).

Domain-expert corrections to SIRA's per-requirement enrichments live OUTSIDE
builds and serve labels, per MNO, as word-level records:

    <corrections-root>/sira-enrich/<MNO>.json          # {req_id: entry}
    <corrections-root>/sira-enrich/labels.json         # {"disabled": [...]}
    <corrections-root>/sira-enrich/reason-categories.json

Entry shape (see strand design doc):

    { "remove": [ {word, label, reason{category,note}, by, at,
                   origin{release}} ],
      "add":    [ ...same record shape... ],
      "suppress_all": {value, label, reason, by, at, origin{release}} }

Semantics implemented here (D-DRAFT-1/2/3 of strand sira-enrichment-review):
- A record participates only when its label is not disabled ("" = always).
- Cross-release guard: a record applies to the viewed/loaded release only
  when the requirement is still "the same" as at origin — token-set Jaccard
  over VANILLA index tokens >= threshold. Verdicts are supplied by the
  caller via a callback (the service owns tokenization + other releases'
  corpora); this module owns only the fold.
- Effective = suppress_all ? (applied adds) : (llm − applied removes) +
  applied adds. REMOVE WINS: an applied add never survives an applied
  remove of the same word.
- Records that do NOT apply because the guard failed (or origin is
  unknowable) are returned as HELD — the UI surfaces them for
  re-affirm/discard. Disabled-label records are neither applied nor held.

This module is dependency-free (stdlib only) so the web side can reuse it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

JACCARD_THRESHOLD_DEFAULT = 0.85

# verdict_fn(origin_release, req_id) -> "ok" | "changed" | "unknown"
VerdictFn = Callable[[str, str], str]


def sira_enrich_dir(corrections_root: Path | str) -> Path:
    return Path(corrections_root) / "sira-enrich"


def load_overlay(corrections_root: Path | str, mno: str) -> dict[str, Any]:
    """Load `<root>/sira-enrich/<MNO>.json`; missing/malformed -> {} (loud log
    is the caller's concern — this module stays quiet + total)."""
    p = sira_enrich_dir(corrections_root) / f"{mno}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_disabled_labels(corrections_root: Path | str) -> set[str]:
    p = sira_enrich_dir(corrections_root) / "labels.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data.get("disabled", []))
    except (OSError, json.JSONDecodeError):
        return set()


def jaccard(a: set[str], b: set[str]) -> float:
    """Token-set Jaccard; both empty -> 1.0 (nothing changed about nothing)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class OverlayResult:
    """The fold's output for one requirement."""
    effective: list[str]
    held: list[dict[str, Any]] = field(default_factory=list)  # records + why
    suppressed: bool = False
    applied_removes: list[str] = field(default_factory=list)
    applied_adds: list[str] = field(default_factory=list)


def _record_state(rec: dict[str, Any], disabled: set[str],
                  verdict_fn: VerdictFn, req_id: str) -> str:
    """'applied' | 'held' | 'disabled' for one record."""
    if (rec.get("label") or "") in disabled and (rec.get("label") or "") != "":
        return "disabled"
    origin = (rec.get("origin") or {}).get("release", "")
    if not origin:
        return "applied"  # no origin recorded: legacy/manual entry — apply
    verdict = verdict_fn(origin, req_id)
    return "applied" if verdict == "ok" else "held"


def apply_overlay_to_req(
    llm_words: list[str],
    entry: dict[str, Any] | None,
    disabled: set[str],
    verdict_fn: VerdictFn,
    req_id: str,
) -> OverlayResult:
    """Fold one requirement's records into its effective enrichment set."""
    if not entry:
        return OverlayResult(effective=list(llm_words))

    held: list[dict[str, Any]] = []
    removes: list[str] = []
    adds: list[str] = []

    for direction in ("remove", "add"):
        for rec in entry.get(direction) or []:
            state = _record_state(rec, disabled, verdict_fn, req_id)
            if state == "applied":
                (removes if direction == "remove" else adds).append(rec["word"])
            elif state == "held":
                held.append({**rec, "direction": direction})

    suppressed = False
    sup = entry.get("suppress_all")
    if isinstance(sup, dict) and sup.get("value"):
        state = _record_state(sup, disabled, verdict_fn, req_id)
        if state == "applied":
            suppressed = True
        elif state == "held":
            held.append({**sup, "direction": "suppress_all"})

    remove_set = set(removes)
    if suppressed:
        base: list[str] = []
    else:
        base = [w for w in llm_words if w not in remove_set]
    # remove wins over add; preserve order, no duplicates
    seen = set(base)
    for w in adds:
        if w not in remove_set and w not in seen:
            base.append(w)
            seen.add(w)

    return OverlayResult(
        effective=base,
        held=held,
        suppressed=suppressed,
        applied_removes=sorted(remove_set & set(llm_words)) if not suppressed else [],
        applied_adds=[w for w in adds if w not in remove_set],
    )


def make_verdict_fn(
    token_sets: Callable[[str, str], "set[str] | None"],
    current_release: str,
    threshold: float = JACCARD_THRESHOLD_DEFAULT,
) -> VerdictFn:
    """Build the cross-release guard from a token-set source.

    `token_sets(release, req_id)` returns the VANILLA index token set for the
    req in that release, or None when unavailable (release not loaded /
    req absent). Same-release records short-circuit to "ok".
    Cannot-verify == verified-changed: both yield a non-"ok" verdict
    ("unknown" / "changed") and the record is HELD.
    """
    def verdict(origin_release: str, req_id: str) -> str:
        if origin_release == current_release:
            return "ok"
        cur = token_sets(current_release, req_id)
        org = token_sets(origin_release, req_id)
        if cur is None or org is None:
            return "unknown"
        return "ok" if jaccard(cur, org) >= threshold else "changed"

    return verdict
