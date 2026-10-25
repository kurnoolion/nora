"""Overlay edit semantics — the one implementation both writers run
(strand corp-db-layer S6; store-split-design §5).

`EnrichOverlayStore` (nora-web, local backend) and the nora-store
`/stores/overlay/v1/edit` route (the Overlay authority taking edits over
HTTP) call these functions, so an edit lands identically whichever side
holds the backend. Each `run_*` function is one backend transaction:
read, fold the op, write — the backend's lock (flock / BEGIN IMMEDIATE)
makes it atomic against other writers on the same host.

Record shape (word-level granularity):
    {"word": w, "label": l, "reason": {"category": c, "note": n},
     "by": user, "at": iso8601, "origin": {"release": rel},
     "fingerprint": fp}      # fingerprint only when the caller knew it
One active record per (word, direction): re-editing a word replaces its
record. `suppress_all` is an entry-level record with the same stamp fields
plus {"value": true}. `fingerprint` is the requirement's content
fingerprint at edit time (store-split-design §3) — the fold applies a
record without the Jaccard guard while the req still carries it.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from core.src.stores.overlay import OverlayStore

DEFAULT_REASON_CATEGORIES = [
    "misleading-enrichment",
    "too-generic",
    "wrong-context",
    "hallucinated-concept",
    "duplicate-of-req-text",
    "missing-synonym",
    "missing-acronym",
    "domain-term-missed",
]

EDIT_OPS = ("remove", "unremove", "add", "unadd", "suppress", "unsuppress",
            "reaffirm", "discard")

# Store-level ops the HTTP edit route dispatches (`run_op`): the word-record
# edit plus the label / reason mutations, each a read-modify-write that
# must run where the backend lock is.
STORE_OPS = ("edit", "set_label_merged", "set_label_disabled", "delete_label",
             "add_reason_category")


def now_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def view_digest(filtered_overlay: dict) -> str:
    """Canonical content digest of one label VIEW of an MNO overlay (the
    overlay already filtered to the view's allowed labels). The filtered
    records fully determine what the view serves, so the merge log stays
    OUT of the formula: a merge/un-merge flags only the MNOs the label
    touches, and fully-undone edits digest back to the served state. The
    web's pending signal compares this with what nora-store applied."""
    payload = json.dumps({"overlay": filtered_overlay},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


# ── label views (branches) ─────────────────────────────────────────


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
    function, as does the fold in ``core.src.store_service.enrich_overlay``) so served-vs-current comparisons are content-exact."""
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


def reason_categories_or_default(backend: "OverlayStore | None") -> list[str]:
    cats = backend.reason_categories() if backend is not None else None
    return cats if isinstance(cats, list) and cats else list(DEFAULT_REASON_CATEGORIES)


# ── the word-record edit ───────────────────────────────────────────


def apply_edit(entry: "dict[str, Any] | None", op: str, *,
               words: "list[str] | None" = None,
               pairs: "list[dict] | None" = None,
               label: str = "",
               reason: "dict | None" = None,
               by: str = "",
               origin_release: str = "",
               fingerprint: str = "",
               at: str = "") -> "dict[str, Any] | None":
    """Fold one edit op into `entry` (mutated and returned; None when the
    entry emptied away). `words` drives remove/unremove/add/unadd; `pairs`
    [{direction, word}] drives reaffirm/discard (held-record refs from the
    service); suppress/unsuppress need neither. `origin_release` = the
    release the reviewer is VIEWING (stamped as the record's origin; for
    reaffirm it is the NEW origin). `fingerprint` = the req's content
    fingerprint in that release ("" = unknown, legacy shape). `at` = the
    stamp time ("" = now)."""
    if op not in EDIT_OPS:
        raise ValueError(f"unknown op: {op}")

    stamp = {"label": label or "",
             "reason": {"category": (reason or {}).get("category", ""),
                        "note": (reason or {}).get("note", "")},
             "by": by or "",
             "at": at or now_stamp(),
             "origin": {"release": origin_release or ""}}
    if fingerprint:
        stamp["fingerprint"] = fingerprint

    def _restamp(rec: dict) -> None:
        # reaffirm: the record now speaks for the viewed release's
        # content — a stale fingerprint must not outlive its origin
        rec["origin"] = {"release": origin_release}
        rec["by"], rec["at"] = stamp["by"], stamp["at"]
        if fingerprint:
            rec["fingerprint"] = fingerprint
        else:
            rec.pop("fingerprint", None)

    entry = entry or {}
    entry.setdefault("remove", [])
    entry.setdefault("add", [])

    def _replace(direction: str, word: str) -> None:
        entry[direction] = [r for r in entry[direction]
                            if r.get("word") != word]
        entry[direction].append({"word": word, **stamp})

    def _drop(direction: str, word: str) -> None:
        entry[direction] = [r for r in entry[direction]
                            if r.get("word") != word]

    if op in ("remove", "add"):
        for w in dict.fromkeys(words or []):   # dedupe, keep order
            if w:
                _replace(op, w)
    elif op in ("unremove", "unadd"):
        for w in (words or []):
            _drop("remove" if op == "unremove" else "add", w)
    elif op == "suppress":
        entry["suppress_all"] = {"value": True, "word": "", **stamp}
    elif op == "unsuppress":
        entry.pop("suppress_all", None)
    elif op in ("reaffirm", "discard"):
        for p in (pairs or []):
            d, w = p.get("direction"), p.get("word", "")
            if d == "suppress_all":
                sup = entry.get("suppress_all")
                if isinstance(sup, dict):
                    if op == "discard":
                        entry.pop("suppress_all", None)
                    else:
                        _restamp(sup)
            elif d in ("remove", "add"):
                if op == "discard":
                    _drop(d, w)
                else:
                    for r in entry[d]:
                        if r.get("word") == w:
                            _restamp(r)

    # prune empties
    if not entry["remove"]:
        entry.pop("remove")
    if not entry["add"]:
        entry.pop("add")
    return entry or None


# ── transactional runners (one backend transaction each) ───────────


def run_edit(backend: OverlayStore, mno: str, req_id: str, op: str,
             **kw: Any) -> "dict[str, Any] | None":
    """Apply one edit op to the stored entry; returns the updated entry
    (None when it emptied away). Keyword args as `apply_edit`."""
    if op not in EDIT_OPS:
        raise ValueError(f"unknown op: {op}")
    with backend.transaction():
        result = apply_edit(backend.get_entry(mno, req_id), op, **kw)
        backend.put_entry(mno, req_id, result)
    return result


def run_set_label_merged(backend: OverlayStore, label: str,
                         merged: bool) -> set[str]:
    """Merge a label into main (or un-merge it) — the admin's
    branch-landing action. Only mutates the merge log; records keep
    their label, so provenance survives and un-merge is exact."""
    with backend.transaction():
        cur = backend.accepted_labels()
        (cur.add if merged else cur.discard)(label)
        backend.set_accepted_labels(cur)
    return cur


def run_set_label_disabled(backend: OverlayStore, label: str,
                           disabled: bool) -> set[str]:
    with backend.transaction():
        cur = backend.disabled_labels()
        (cur.add if disabled else cur.discard)(label)
        backend.set_disabled_labels(cur)
    return cur


def run_delete_label(backend: OverlayStore, label: str) -> int:
    """Strip every record carrying `label` from every MNO (the
    prompt-fix cleanup, D-DRAFT-5). Returns records removed."""
    removed = 0
    with backend.transaction():
        for mno in backend.list_mnos():
            overlay = backend.get_overlay(mno)
            for rid in list(overlay):
                entry = overlay[rid]
                changed = False
                for d in ("remove", "add"):
                    recs = entry.get(d) or []
                    kept = [r for r in recs if (r.get("label") or "") != label]
                    removed += len(recs) - len(kept)
                    if len(kept) != len(recs):
                        changed = True
                        if kept:
                            entry[d] = kept
                        else:
                            entry.pop(d, None)
                sup = entry.get("suppress_all")
                if isinstance(sup, dict) and (sup.get("label") or "") == label:
                    entry.pop("suppress_all")
                    removed += 1
                    changed = True
                if changed:
                    backend.put_entry(mno, rid, entry or None)
        # a deleted label needn't linger in the disabled/accepted lists
        disabled = backend.disabled_labels()
        if label in disabled:
            backend.set_disabled_labels(disabled - {label})
        accepted = backend.accepted_labels()
        if label in accepted:
            backend.set_accepted_labels(accepted - {label})
    return removed


def run_add_reason_category(backend: OverlayStore, category: str) -> list[str]:
    category = category.strip()
    with backend.transaction():
        cats = reason_categories_or_default(backend)
        if category and category not in cats:
            cats.append(category)
            backend.set_reason_categories(cats)
    return cats


def run_op(backend: OverlayStore, name: str, payload: dict) -> Any:
    """Dispatch one store op by name with a JSON payload (the HTTP edit
    route's body) — the server-side counterpart of
    `HttpOverlayStore.run_op`. Results are JSON-ready: label sets come
    back sorted."""
    if name == "edit":
        return run_edit(
            backend, str(payload.get("mno", "")), str(payload.get("req_id", "")),
            str(payload.get("op", "")),
            words=payload.get("words"), pairs=payload.get("pairs"),
            label=str(payload.get("label") or ""),
            reason=payload.get("reason"),
            by=str(payload.get("by") or ""),
            origin_release=str(payload.get("origin_release") or ""),
            fingerprint=str(payload.get("fingerprint") or ""))
    label = str(payload.get("label") or "")
    if name == "set_label_merged":
        return sorted(run_set_label_merged(backend, label, bool(payload.get("merged"))))
    if name == "set_label_disabled":
        return sorted(run_set_label_disabled(backend, label, bool(payload.get("disabled"))))
    if name == "delete_label":
        return run_delete_label(backend, label)
    if name == "add_reason_category":
        return run_add_reason_category(backend, str(payload.get("category") or ""))
    raise ValueError(f"unknown store op: {name}")
