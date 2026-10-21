"""Enrichment-corrections overlay store — the WEB side's writer
(strand sira-enrichment-review, D-DRAFT-1/2; storage moved behind
`core.src.stores.overlay` in strand corp-db-layer S3, D-DRAFT-24).

Owns the edit semantics (ops, labels as branches, the label-view digest)
on top of an `OverlayStore` backend:

- JSON (default): `<corrections_root>/sira-enrich/` — `<MNO>.json`
  ({req_id: entry}), `accepted-labels.json` (merge log), `labels.json`
  (disabled; legacy report annotation), `reason-categories.json`.
- SQLite: `$NORA_OVERLAY_DB` (one file per deployment; wins over the
  corrections root).

The web app is the SOLE writer (nora-store reads the same backend
read-only and applies the overlay at cell load). Concurrency: every
mutation runs in one backend transaction (JSON: a flock'd lock file;
SQLite: BEGIN IMMEDIATE); last-writer-wins per record. Writes are atomic.

Record shape (word-level granularity):
    {"word": w, "label": l, "reason": {"category": c, "note": n},
     "by": user, "at": iso8601, "origin": {"release": rel},
     "fingerprint": fp}      # fingerprint only when the caller knew it
One active record per (word, direction): re-editing a word replaces its
record. `suppress_all` is an entry-level record with the same stamp fields
plus {"value": true}. `fingerprint` is the requirement's content
fingerprint at edit time (store-split-design §3) — the fold applies a
record without the Jaccard guard while the req still carries it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.src.stores.overlay import OverlayStore, open_overlay_store
# stdlib-only by design so the web side can share the exact label-view
# projection the service applies (see its module docstring)
from nora_store.enrich_overlay import allowed_labels, filter_overlay

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


class EnrichOverlayStore:
    """Edit semantics over an `OverlayStore` bound to a corrections root
    ("" and no $NORA_OVERLAY_DB = disabled). `backend` overrides the
    factory (tests, the import CLI)."""

    def __init__(self, corrections_root: str | Path,
                 backend: "OverlayStore | None" = None):
        self.root = Path(corrections_root) if corrections_root else None
        self.backend: "OverlayStore | None" = (
            backend if backend is not None
            else open_overlay_store(str(corrections_root or "")))

    @property
    def enabled(self) -> bool:
        return self.backend is not None

    def _require(self) -> OverlayStore:
        if self.backend is None:
            raise RuntimeError("corrections_root not configured")
        return self.backend

    # ── reads ──────────────────────────────────────────────────────

    def get_overlay(self, mno: str) -> dict[str, Any]:
        return self.backend.get_overlay(mno) if self.enabled else {}

    def get_entry(self, mno: str, req_id: str) -> dict[str, Any] | None:
        return self.backend.get_entry(mno, req_id) if self.enabled else None

    def list_mnos(self) -> list[str]:
        """MNOs with an overlay (sorted)."""
        return self.backend.list_mnos() if self.enabled else []

    def overlay_mtime(self, mno: str) -> float:
        """Backend change marker (JSON: file mtime); 0.0 when absent —
        pending = marker > cell loaded_at."""
        return self.backend.version(mno) if self.enabled else 0.0

    def overlay_digest(self, mno: str, label: str = "") -> str:
        """Canonical content digest of the `label` VIEW of the MNO overlay
        (main = accepted + unlabeled, plus `label`'s own records). The
        filtered records fully determine what the view serves — the merge
        log itself stays OUT of the formula, so a merge/un-merge flags
        only the MNOs the label actually touches. Pending detection
        compares this against the digest nora-store reports for its
        APPLIED view — edits that are fully undone digest back to the
        served state, so no false 'pending'. Formula must match
        nora-store's `_overlay_digest`."""
        import hashlib
        filtered = filter_overlay(self.get_overlay(mno),
                                  allowed_labels(self.accepted_labels(), label))
        payload = json.dumps({"overlay": filtered},
                             sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def accepted_labels(self) -> set[str]:
        """Labels merged into main (the default view) — the merge log."""
        return self.backend.accepted_labels() if self.enabled else set()

    def disabled_labels(self) -> set[str]:
        return self.backend.disabled_labels() if self.enabled else set()

    def _overlays(self, mnos: "list[str] | None") -> list[tuple[str, dict]]:
        if not self.enabled:
            return []
        return [(m, self.backend.get_overlay(m) or {})
                for m in (mnos if mnos is not None else self.list_mnos())]

    def label_counts(self, mnos: "list[str] | None" = None) -> dict[str, int]:
        """Record counts per label across MNOs (drawer display)."""
        counts: dict[str, int] = {}
        for _mno, overlay in self._overlays(mnos):
            for entry in overlay.values():
                for rec in (entry.get("remove") or []) + (entry.get("add") or []):
                    counts[rec.get("label") or ""] = counts.get(rec.get("label") or "", 0) + 1
                sup = entry.get("suppress_all")
                if isinstance(sup, dict) and sup.get("value"):
                    counts[sup.get("label") or ""] = counts.get(sup.get("label") or "", 0) + 1
        return counts

    def label_req_counts(self, mnos: "list[str] | None" = None) -> dict[str, int]:
        """Distinct requirements touched per label across MNOs
        (drawer display renders reqs:records)."""
        reqs: dict[str, set] = {}
        for mno, overlay in self._overlays(mnos):
            for rid, entry in overlay.items():
                for rec in (entry.get("remove") or []) + (entry.get("add") or []):
                    reqs.setdefault(rec.get("label") or "", set()).add((mno, rid))
                sup = entry.get("suppress_all")
                if isinstance(sup, dict) and sup.get("value"):
                    reqs.setdefault(sup.get("label") or "", set()).add((mno, rid))
        return {label: len(rids) for label, rids in reqs.items()}

    def reason_categories(self) -> list[str]:
        cats = self.backend.reason_categories() if self.enabled else None
        return cats if isinstance(cats, list) and cats else list(DEFAULT_REASON_CATEGORIES)

    # ── mutations (one backend transaction each) ──────────────────

    def edit(self, mno: str, req_id: str, op: str, *,
             words: "list[str] | None" = None,
             pairs: "list[dict] | None" = None,
             label: str = "",
             reason: "dict | None" = None,
             by: str = "",
             origin_release: str = "",
             fingerprint: str = "") -> "dict[str, Any] | None":
        """Apply one edit op; returns the req's updated entry (None when the
        entry emptied away). `words` drives remove/unremove/add/unadd;
        `pairs` [{direction, word}] drives reaffirm/discard (held-record
        refs from the service); suppress/unsuppress need neither.
        `origin_release` = the release the reviewer is VIEWING (stamped as
        the record's origin; for reaffirm it is the NEW origin).
        `fingerprint` = the req's content fingerprint in that release
        (stamped on every record written; "" = unknown, legacy shape)."""
        if op not in EDIT_OPS:
            raise ValueError(f"unknown op: {op}")
        backend = self._require()

        stamp = {"label": label or "",
                 "reason": {"category": (reason or {}).get("category", ""),
                            "note": (reason or {}).get("note", "")},
                 "by": by or "",
                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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

        with backend.transaction():
            entry = backend.get_entry(mno, req_id) or {}
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
            result = entry or None
            backend.put_entry(mno, req_id, result)
        return result

    def set_label_merged(self, label: str, merged: bool) -> set[str]:
        """Merge a label into main (or un-merge it) — the admin's
        branch-landing action. Only mutates the merge log; records keep
        their label, so provenance survives and un-merge is exact."""
        backend = self._require()
        with backend.transaction():
            cur = backend.accepted_labels()
            (cur.add if merged else cur.discard)(label)
            backend.set_accepted_labels(cur)
        return cur

    def set_label_disabled(self, label: str, disabled: bool) -> set[str]:
        backend = self._require()
        with backend.transaction():
            cur = backend.disabled_labels()
            (cur.add if disabled else cur.discard)(label)
            backend.set_disabled_labels(cur)
        return cur

    def delete_label(self, label: str) -> int:
        """Strip every record carrying `label` from every MNO (the
        prompt-fix cleanup, D-DRAFT-5). Returns records removed."""
        backend = self._require()
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

    def add_reason_category(self, category: str) -> list[str]:
        backend = self._require()
        category = category.strip()
        with backend.transaction():
            cats = self.reason_categories()
            if category and category not in cats:
                cats.append(category)
                backend.set_reason_categories(cats)
        return cats
