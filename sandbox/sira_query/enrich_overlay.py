"""Enrichment-corrections overlay — load, filter, apply (sira-enrichment-review).

Domain-expert corrections to SIRA's per-requirement enrichments live OUTSIDE
builds and serve labels, per MNO, as word-level records:

    <corrections-root>/sira-enrich/<MNO>.json            # {req_id: entry}
    <corrections-root>/sira-enrich/accepted-labels.json  # {"accepted": [...]}
    <corrections-root>/sira-enrich/reason-categories.json

Entry shape (see strand design doc):

    { "remove": [ {word, label, reason{category,note}, by, at,
                   origin{release}} ],
      "add":    [ ...same record shape... ],
      "suppress_all": {value, label, reason, by, at, origin{release}} }

Semantics implemented here (D-DRAFT-1/2/3 of strand sira-enrichment-review):
- Labels are branches: a record participates in a view only when its label
  is ALLOWED there — allowed = unlabeled ∪ accepted labels ("main") ∪ the
  view's own label. Merging a label into main = adding it to
  accepted-labels.json; records are never rewritten, so provenance
  (label / by / reason) survives merging.
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
  re-affirm/discard. Records outside the view's allowed labels are
  neither applied nor held — they are invisible to that view.

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


def load_accepted_labels(corrections_root: Path | str) -> set[str]:
    """Labels merged into "main" — the default view everyone gets. The
    file is the merge log: appending a label merges it, removing it
    un-merges. Records themselves are never rewritten."""
    p = sira_enrich_dir(corrections_root) / "accepted-labels.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data.get("accepted", []))
    except (OSError, json.JSONDecodeError):
        return set()


def allowed_labels(accepted: set[str], label: str = "") -> set[str]:
    """The labels visible in a view: unlabeled legacy records ("") always
    count as main; `label` is the branch the viewer opted into."""
    allowed = {""} | set(accepted)
    if label:
        allowed.add(label)
    return allowed


def filter_overlay(overlay: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    """Project an overlay onto a label view: keep only records whose label
    is allowed, pruning emptied directions/entries. Canonical basis for
    view digests — the web store must filter identically (it imports this
    function) so served-vs-current comparisons are content-exact."""
    out: dict[str, Any] = {}
    for rid, entry in overlay.items():
        kept: dict[str, Any] = {}
        for direction in ("remove", "add"):
            recs = [r for r in (entry.get(direction) or [])
                    if (r.get("label") or "") in allowed]
            if recs:
                kept[direction] = recs
        sup = entry.get("suppress_all")
        if isinstance(sup, dict) and (sup.get("label") or "") in allowed:
            kept["suppress_all"] = sup
        if kept:
            out[rid] = kept
    return out


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


def _record_state(rec: dict[str, Any], allowed: "set[str] | None",
                  verdict_fn: VerdictFn, req_id: str) -> str:
    """'applied' | 'held' | 'excluded' for one record. `allowed` is the
    view's label allowlist (None = every label participates — callers
    that pre-filter with `filter_overlay` pass None)."""
    if allowed is not None and (rec.get("label") or "") not in allowed:
        return "excluded"
    origin = (rec.get("origin") or {}).get("release", "")
    if not origin:
        return "applied"  # no origin recorded: legacy/manual entry — apply
    verdict = verdict_fn(origin, req_id)
    return "applied" if verdict == "ok" else "held"


def apply_overlay_to_req(
    llm_words: list[str],
    entry: dict[str, Any] | None,
    allowed: "set[str] | None",
    verdict_fn: VerdictFn,
    req_id: str,
) -> OverlayResult:
    """Fold one requirement's records into its effective enrichment set."""
    if not entry:
        return OverlayResult(effective=list(llm_words))

    held: list[dict[str, Any]] = []
    removed_at: dict[str, str] = {}
    add_recs: list[dict[str, Any]] = []

    for direction in ("remove", "add"):
        for rec in entry.get(direction) or []:
            state = _record_state(rec, allowed, verdict_fn, req_id)
            if state == "applied":
                if direction == "remove":
                    removed_at[rec["word"]] = rec.get("at") or ""
                else:
                    add_recs.append(rec)
            elif state == "held":
                held.append({**rec, "direction": direction})

    suppressed = False
    sup = entry.get("suppress_all")
    if isinstance(sup, dict) and sup.get("value"):
        state = _record_state(sup, allowed, verdict_fn, req_id)
        if state == "applied":
            suppressed = True
        elif state == "held":
            held.append({**sup, "direction": "suppress_all"})

    remove_set = set(removed_at)
    # remove wins over add for the same word, unless the add is strictly
    # NEWER — a later correction (e.g. a new label, after an earlier
    # label's remove was merged into main) re-adds the word without
    # touching the original record. Ties keep the legacy remove-wins bias.
    adds = [r["word"] for r in add_recs
            if r["word"] not in removed_at
            or (r.get("at") or "") > removed_at[r["word"]]]
    countermanded = remove_set & set(adds)

    if suppressed:
        base: list[str] = []
    else:
        base = [w for w in llm_words
                if w not in remove_set or w in countermanded]
    seen = set(base)
    for w in adds:
        if w not in seen:
            base.append(w)
            seen.add(w)

    return OverlayResult(
        effective=base,
        held=held,
        suppressed=suppressed,
        applied_removes=sorted((remove_set - countermanded) & set(llm_words))
        if not suppressed else [],
        applied_adds=adds,
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
