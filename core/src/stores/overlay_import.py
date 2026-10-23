"""One-time import of the JSON Overlay into SQLite (strand corp-db-layer S3).

Copies every MNO overlay (records keep their provenance: label, reason,
author, time, origin release, fingerprint), the accepted and disabled
label sets and the reason categories from `<corrections-root>/sira-enrich/`
into an Overlay SQLite file, then verifies: `get_overlay(mno)` must be
equal between the two backends for every MNO, and so must the label sets
and categories. Copy and verify run in ONE transaction, so a failed
verify leaves the target unchanged.

Usage:

    python -m core.src.stores.overlay_import \
        --corrections-root <dir> --db <overlay.db> [--force]

Prints counts only — never words, reasons or requirement content.

Error codes (stable):
    OVL-E001  corrections root has no overlay directory
    OVL-E002  target DB is not empty and --force was not given
    OVL-E003  verification mismatch between the two backends
    OVL-E004  SQLite write failure
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from core.src.pipeline.error_codes import PipelineError
from core.src.stores.overlay import (
    DIRECTIONS,
    SUPPRESS,
    JsonOverlayStore,
    SqliteOverlayStore,
)


def import_overlay(corrections_root: "str | Path", db_path: "str | Path",
                   force: bool = False) -> dict:
    """Copy + verify; returns the counts report. Raises PipelineError."""
    src = JsonOverlayStore(corrections_root, read_only=True)
    if not src.dir.is_dir():
        raise PipelineError("OVL-E001", {"path": str(src.dir)})
    dst = SqliteOverlayStore(db_path)

    counts = {"mnos": 0, "entries": 0, "records": 0, "suppress_all": 0,
              "accepted_labels": 0, "disabled_labels": 0,
              "reason_categories": 0, "per_mno": {}}
    try:
        if not dst.is_empty() and not force:
            raise PipelineError("OVL-E002", {"path": str(db_path)})
        with dst.transaction():
            dst.clear()
            mnos = src.list_mnos()
            for mno in mnos:
                overlay = src.get_overlay(mno)
                n_rec = n_sup = 0
                for rid, entry in overlay.items():
                    dst.put_entry(mno, rid, entry)
                    n_rec += sum(len(entry.get(d) or []) for d in DIRECTIONS)
                    n_sup += 1 if entry.get(SUPPRESS) is not None else 0
                if not overlay:
                    dst.put_entry(mno, "", None)    # registers the MNO
                counts["per_mno"][mno] = {"entries": len(overlay),
                                          "records": n_rec, "suppress_all": n_sup}
                counts["entries"] += len(overlay)
                counts["records"] += n_rec
                counts["suppress_all"] += n_sup
            counts["mnos"] = len(mnos)
            accepted, disabled = src.accepted_labels(), src.disabled_labels()
            dst.set_accepted_labels(accepted)
            dst.set_disabled_labels(disabled)
            cats = src.reason_categories()
            if cats is not None:
                dst.set_reason_categories(cats)
            counts["accepted_labels"] = len(accepted)
            counts["disabled_labels"] = len(disabled)
            counts["reason_categories"] = len(cats or [])
            _verify(src, dst)   # raises -> the transaction rolls back
    except sqlite3.Error as exc:
        raise PipelineError("OVL-E004", {"reason": type(exc).__name__}, exc) from exc
    return counts


def _verify(src: JsonOverlayStore, dst: SqliteOverlayStore) -> None:
    """Both backends must return the same content for every read."""
    if src.list_mnos() != dst.list_mnos():
        raise PipelineError("OVL-E003", {"what": "MNO list", "count": "1"})
    for mno in src.list_mnos():
        a, b = src.get_overlay(mno), dst.get_overlay(mno)
        bad = sum(1 for rid in set(a) | set(b) if a.get(rid) != b.get(rid))
        if bad:
            raise PipelineError("OVL-E003", {"what": f"overlay {mno}",
                                             "count": str(bad)})
    for what, x, y in (
            ("accepted labels", src.accepted_labels(), dst.accepted_labels()),
            ("disabled labels", src.disabled_labels(), dst.disabled_labels()),
            ("reason categories", src.reason_categories(), dst.reason_categories())):
        if x != y:
            raise PipelineError("OVL-E003", {"what": what, "count": "1"})


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        prog="overlay_import",
        description="Import the JSON Overlay (sira-enrich/) into an Overlay SQLite file.")
    ap.add_argument("--corrections-root", required=True,
                    help="dir holding sira-enrich/")
    ap.add_argument("--db", required=True, help="target Overlay SQLite file")
    ap.add_argument("--force", action="store_true",
                    help="replace the contents of a non-empty target")
    args = ap.parse_args(argv)
    try:
        counts = import_overlay(args.corrections_root, args.db, force=args.force)
    except PipelineError as exc:
        print(f"FAILED {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"  hint: {exc.hint}", file=sys.stderr)
        return 1
    print(f"imported {counts['mnos']} MNO(s): {counts['entries']} entries, "
          f"{counts['records']} word records, {counts['suppress_all']} suppress_all; "
          f"labels accepted={counts['accepted_labels']} "
          f"disabled={counts['disabled_labels']}; "
          f"reason categories={counts['reason_categories']}")
    for mno, c in sorted(counts["per_mno"].items()):
        print(f"  {mno}: {c['entries']} entries, {c['records']} records, "
              f"{c['suppress_all']} suppress_all")
    print("verified: every MNO overlay and label set equal in both backends")
    return 0


if __name__ == "__main__":
    sys.exit(main())
