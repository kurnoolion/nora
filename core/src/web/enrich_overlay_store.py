"""Enrichment-corrections overlay store — the WEB side's writer
(strand sira-enrichment-review, D-DRAFT-1/2).

Owns read-modify-write of the per-MNO overlay files under
`<corrections_root>/sira-enrich/`:

    <MNO>.json                # {req_id: entry} — word records
    labels.json               # {"disabled": [...]}
    reason-categories.json    # extensible category list

The web app is the SOLE writer (sira-query mounts the volume ro and applies
the overlay at cell load). Concurrency: a single flock'd lock file guards
every mutation (both stacks' web UIs share the volume); last-writer-wins per
record. Writes are atomic (tmp + rename).

Record shape (word-level granularity):
    {"word": w, "label": l, "reason": {"category": c, "note": n},
     "by": user, "at": iso8601, "origin": {"release": rel}}
One active record per (word, direction): re-editing a word replaces its
record. `suppress_all` is an entry-level record with the same stamp fields
plus {"value": true}.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    """Filesystem store bound to a corrections root ("" = disabled)."""

    def __init__(self, corrections_root: str | Path):
        self.root = Path(corrections_root) if corrections_root else None

    @property
    def enabled(self) -> bool:
        return self.root is not None

    @property
    def dir(self) -> Path:
        assert self.root is not None
        return self.root / "sira-enrich"

    # ── locking + IO ────────────────────────────────────────────────

    @contextmanager
    def _locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.dir / ".lock"
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1, sort_keys=True)
                f.write("\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _mno_path(self, mno: str) -> Path:
        return self.dir / f"{mno}.json"

    # ── reads (no lock: single-file atomic reads) ──────────────────

    def get_overlay(self, mno: str) -> dict[str, Any]:
        return self._read_json(self._mno_path(mno), {}) if self.enabled else {}

    def get_entry(self, mno: str, req_id: str) -> dict[str, Any] | None:
        return self.get_overlay(mno).get(req_id)

    def overlay_mtime(self, mno: str) -> float:
        """0.0 when absent — pending = mtime > cell loaded_at."""
        try:
            return self._mno_path(mno).stat().st_mtime
        except OSError:
            return 0.0

    def disabled_labels(self) -> set[str]:
        if not self.enabled:
            return set()
        data = self._read_json(self.dir / "labels.json", {})
        return set(data.get("disabled", []))

    def label_counts(self, mnos: "list[str] | None" = None) -> dict[str, int]:
        """Record counts per label across MNO files (drawer display)."""
        counts: dict[str, int] = {}
        if not self.enabled:
            return counts
        paths = ([self._mno_path(m) for m in mnos] if mnos is not None
                 else sorted(self.dir.glob("*.json")))
        for p in paths:
            if p.name in ("labels.json", "reason-categories.json"):
                continue
            for entry in (self._read_json(p, {}) or {}).values():
                for rec in (entry.get("remove") or []) + (entry.get("add") or []):
                    counts[rec.get("label") or ""] = counts.get(rec.get("label") or "", 0) + 1
                sup = entry.get("suppress_all")
                if isinstance(sup, dict) and sup.get("value"):
                    counts[sup.get("label") or ""] = counts.get(sup.get("label") or "", 0) + 1
        return counts

    def reason_categories(self) -> list[str]:
        if not self.enabled:
            return list(DEFAULT_REASON_CATEGORIES)
        cats = self._read_json(self.dir / "reason-categories.json", None)
        return cats if isinstance(cats, list) and cats else list(DEFAULT_REASON_CATEGORIES)

    # ── mutations (locked) ─────────────────────────────────────────

    def edit(self, mno: str, req_id: str, op: str, *,
             words: "list[str] | None" = None,
             pairs: "list[dict] | None" = None,
             label: str = "",
             reason: "dict | None" = None,
             by: str = "",
             origin_release: str = "") -> "dict[str, Any] | None":
        """Apply one edit op; returns the req's updated entry (None when the
        entry emptied away). `words` drives remove/unremove/add/unadd;
        `pairs` [{direction, word}] drives reaffirm/discard (held-record
        refs from the service); suppress/unsuppress need neither.
        `origin_release` = the release the reviewer is VIEWING (stamped as
        the record's origin; for reaffirm it is the NEW origin)."""
        if op not in EDIT_OPS:
            raise ValueError(f"unknown op: {op}")
        if not self.enabled:
            raise RuntimeError("corrections_root not configured")

        stamp = {"label": label or "",
                 "reason": {"category": (reason or {}).get("category", ""),
                            "note": (reason or {}).get("note", "")},
                 "by": by or "",
                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "origin": {"release": origin_release or ""}}

        with self._locked():
            path = self._mno_path(mno)
            overlay = self._read_json(path, {})
            entry = overlay.setdefault(req_id, {})
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
                                sup["origin"] = {"release": origin_release}
                                sup["by"], sup["at"] = stamp["by"], stamp["at"]
                    elif d in ("remove", "add"):
                        if op == "discard":
                            _drop(d, w)
                        else:
                            for r in entry[d]:
                                if r.get("word") == w:
                                    r["origin"] = {"release": origin_release}
                                    r["by"], r["at"] = stamp["by"], stamp["at"]

            # prune empties
            if not entry["remove"]:
                entry.pop("remove")
            if not entry["add"]:
                entry.pop("add")
            if not entry:
                overlay.pop(req_id, None)
            result = overlay.get(req_id)
            self._write_json(path, overlay)
        return result

    def set_label_disabled(self, label: str, disabled: bool) -> set[str]:
        if not self.enabled:
            raise RuntimeError("corrections_root not configured")
        with self._locked():
            p = self.dir / "labels.json"
            data = self._read_json(p, {})
            cur = set(data.get("disabled", []))
            (cur.add if disabled else cur.discard)(label)
            self._write_json(p, {"disabled": sorted(cur)})
        return cur

    def delete_label(self, label: str) -> int:
        """Strip every record carrying `label` from every MNO file (the
        prompt-fix cleanup, D-DRAFT-5). Returns records removed."""
        if not self.enabled:
            raise RuntimeError("corrections_root not configured")
        removed = 0
        with self._locked():
            for p in sorted(self.dir.glob("*.json")):
                if p.name in ("labels.json", "reason-categories.json"):
                    continue
                overlay = self._read_json(p, {})
                changed = False
                for rid in list(overlay):
                    entry = overlay[rid]
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
                    if not entry:
                        overlay.pop(rid)
                if changed:
                    self._write_json(p, overlay)
            # a deleted label needn't linger in the disabled list
            lp = self.dir / "labels.json"
            data = self._read_json(lp, {})
            if label in data.get("disabled", []):
                self._write_json(lp, {"disabled": sorted(
                    set(data["disabled"]) - {label})})
        return removed

    def add_reason_category(self, category: str) -> list[str]:
        if not self.enabled:
            raise RuntimeError("corrections_root not configured")
        category = category.strip()
        with self._locked():
            cats = self.reason_categories()
            if category and category not in cats:
                cats.append(category)
                self._write_json(self.dir / "reason-categories.json", cats)
        return cats
