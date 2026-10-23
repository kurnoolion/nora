"""Enrichment-corrections overlay store — the WEB side's edit surface
(strand sira-enrichment-review, D-DRAFT-1/2; storage moved behind
`core.src.stores.overlay` in strand corp-db-layer S3, D-DRAFT-24; edits
run on the Overlay authority since S6, D-DRAFT-28).

Reads and the edit semantics (ops, labels as branches, the label-view
digest) over an `OverlayStore` backend from `open_overlay_store`:

- HTTP (`$NORA_OVERLAY_URL`, wins): nora-store's `/stores/overlay/v1`.
  Every mutation is ONE request to the server's `edit` route, which runs
  the op inside its backend transaction (`core.src.stores.overlay_ops`),
  so the web never does a read-modify-write over the wire.
- SQLite (`$NORA_OVERLAY_DB`) or JSON (`<corrections_root>/sira-enrich/`):
  the dev fallback when no URL is set — the same `overlay_ops` run here,
  in one local backend transaction (JSON: a flock'd lock file; SQLite:
  BEGIN IMMEDIATE); last-writer-wins per record. Writes are atomic.

The op logic itself lives in `overlay_ops` so both paths are identical
code; this class only chooses where it runs. Record shape: see that
module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.src.stores import overlay_ops
from core.src.stores.overlay import OverlayStore, open_overlay_store
from core.src.stores.overlay_ops import (  # noqa: F401  (re-exports)
    DEFAULT_REASON_CATEGORIES,
    EDIT_OPS,
    allowed_labels,
    filter_overlay,
)


class EnrichOverlayStore:
    """Edit semantics over an `OverlayStore` bound to a corrections root
    ("" and no $NORA_OVERLAY_DB / $NORA_OVERLAY_URL = disabled). `backend`
    overrides the factory (tests, the import CLI)."""

    def __init__(self, corrections_root: str | Path,
                 backend: "OverlayStore | None" = None):
        self.root = Path(corrections_root) if corrections_root else None
        self.backend: "OverlayStore | None" = (
            backend if backend is not None
            else open_overlay_store(str(corrections_root or "")))

    @property
    def enabled(self) -> bool:
        return self.backend is not None

    @property
    def remote(self) -> bool:
        """True when edits run on the Overlay authority over HTTP."""
        return hasattr(self.backend, "run_op")

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
        (main = accepted + unlabeled, plus `label`'s own records) —
        `overlay_ops.view_digest`, the formula nora-store applies to its
        APPLIED view, so pending detection compares like with like. Over
        HTTP the authority computes it (one round trip)."""
        if self.remote:
            return self.backend.digest(mno, label)
        filtered = filter_overlay(self.get_overlay(mno),
                                  allowed_labels(self.accepted_labels(), label))
        return overlay_ops.view_digest(filtered)

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
        return overlay_ops.reason_categories_or_default(self.backend)

    # ── mutations (one backend transaction each) ──────────────────

    def _run(self, name: str, local, **payload: Any) -> Any:
        """Run one store op where the lock is: on the authority over HTTP
        (`run_op`), else in the local backend's transaction."""
        backend = self._require()
        if self.remote:
            return backend.run_op(name, payload)
        return local(backend, **payload)

    def edit(self, mno: str, req_id: str, op: str, *,
             words: "list[str] | None" = None,
             pairs: "list[dict] | None" = None,
             label: str = "",
             reason: "dict | None" = None,
             by: str = "",
             origin_release: str = "",
             fingerprint: str = "") -> "dict[str, Any] | None":
        """Apply one edit op; returns the req's updated entry (None when the
        entry emptied away). Arguments as `overlay_ops.apply_edit`."""
        if op not in EDIT_OPS:
            raise ValueError(f"unknown op: {op}")
        return self._run(
            "edit", lambda b, **kw: overlay_ops.run_edit(
                b, kw.pop("mno"), kw.pop("req_id"), kw.pop("op"), **kw),
            mno=mno, req_id=req_id, op=op, words=words, pairs=pairs,
            label=label, reason=reason, by=by, origin_release=origin_release,
            fingerprint=fingerprint)

    def set_label_merged(self, label: str, merged: bool) -> set[str]:
        """Merge a label into main (or un-merge it) — the admin's
        branch-landing action. Only mutates the merge log; records keep
        their label, so provenance survives and un-merge is exact."""
        return set(self._run("set_label_merged", overlay_ops.run_set_label_merged,
                             label=label, merged=merged))

    def set_label_disabled(self, label: str, disabled: bool) -> set[str]:
        return set(self._run("set_label_disabled", overlay_ops.run_set_label_disabled,
                             label=label, disabled=disabled))

    def delete_label(self, label: str) -> int:
        """Strip every record carrying `label` from every MNO (the
        prompt-fix cleanup, D-DRAFT-5). Returns records removed."""
        return int(self._run("delete_label", overlay_ops.run_delete_label,
                             label=label))

    def add_reason_category(self, category: str) -> list[str]:
        return list(self._run("add_reason_category", overlay_ops.run_add_reason_category,
                              category=category))
