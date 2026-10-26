"""Restamp Overlay record fingerprints after a canonical-form change
(strand rel-history; canonical form v3, D-279 amended).

Every record carries the requirement's content fingerprint as it was when
the edit was made (``v<N>:<sha256>``). When the canonical form changes,
stored values keep their old version prefix and never equal the new
values, so the fold falls back to the same-release and similarity checks.
This tool recomputes each stale record's fingerprint from the
requirement's text in its origin release, read through the Trees store.

Usage:
    python -m core.src.stores.overlay_restamp --db <overlay.db> \\
        (--trees-root <dir> | --env-dir <dir>) [--mno M] [--stamp-missing] [--dry-run]

Rules: a record is restamped when its fingerprint carries another
version's prefix (``--stamp-missing`` also stamps records with none); a
requirement not found in its origin release leaves the record as it was
and is counted as unresolved. One transaction per MNO. Prints counts
only — never words, reasons or requirement text.

Error codes (stable): OVL-E006 no Trees source; OVL-E004 write failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.src.models.fingerprint import FINGERPRINT_VERSION, text_fp
from core.src.pipeline.error_codes import PipelineError
from core.src.stores.overlay import DIRECTIONS, SUPPRESS, open_overlay_store
from core.src.stores.trees import open_tree_store

_PREFIX = FINGERPRINT_VERSION + ":"


def _records(entry: dict):
    for d in DIRECTIONS:
        for rec in entry.get(d) or []:
            if isinstance(rec, dict):
                yield rec
    sup = entry.get(SUPPRESS)
    if isinstance(sup, dict):
        yield sup


def restamp(db_path: "str | Path", trees_root: str = "", env_dir: str = "",
            mno: str = "", stamp_missing: bool = False,
            dry_run: bool = False) -> dict:
    """Recompute stale record fingerprints; returns the counts report."""
    if not trees_root and not env_dir:
        raise PipelineError("OVL-E006", {})
    trees = open_tree_store(env_dir or "", root_dir=trees_root, url="")
    store = open_overlay_store(db_path=str(db_path), read_only=dry_run, url="")
    report: dict = {"version": FINGERPRINT_VERSION, "dry_run": dry_run, "mnos": {}}
    cache: dict[tuple, str] = {}

    def current_fp(m: str, release: str, req_id: str) -> str:
        key = (m, release, req_id)
        if key not in cache:
            rows = trees.find_req(req_id, mno=m, release=release) if release else []
            cache[key] = text_fp(rows[0].get("title"), rows[0].get("text")) if rows else ""
        return cache[key]

    for m in sorted(store.list_mnos()):
        if mno and m != mno:
            continue
        c = {"entries": 0, "records": 0, "restamped": 0, "current": 0,
             "missing": 0, "unresolved": 0}
        overlay = store.get_overlay(m)
        changed: dict[str, dict] = {}
        for req_id, entry in overlay.items():
            c["entries"] += 1
            touched = False
            for rec in _records(entry):
                c["records"] += 1
                fp = str(rec.get("fingerprint") or "")
                if fp.startswith(_PREFIX):
                    c["current"] += 1
                    continue
                if not fp:
                    c["missing"] += 1
                    if not stamp_missing:
                        continue
                release = str((rec.get("origin") or {}).get("release") or "")
                new = current_fp(m, release, req_id)
                if not new:
                    c["unresolved"] += 1
                    continue
                rec["fingerprint"] = new
                c["restamped"] += 1
                touched = True
            if touched:
                changed[req_id] = entry
        if changed and not dry_run:
            try:
                with store.transaction():
                    for req_id, entry in changed.items():
                        store.put_entry(m, req_id, entry)
            except Exception as exc:  # noqa: BLE001 — surfaced as a coded error
                raise PipelineError("OVL-E004", {"reason": type(exc).__name__}) from exc
        report["mnos"][m] = c
    return report


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="the Overlay SQLite file")
    ap.add_argument("--trees-root", default="", help="directory of trees-*.db files")
    ap.add_argument("--env-dir", default="", help="env dir with out/parse trees (fallback)")
    ap.add_argument("--mno", default="", help="restamp one MNO only")
    ap.add_argument("--stamp-missing", action="store_true",
                    help="also stamp records that carry no fingerprint")
    ap.add_argument("--dry-run", action="store_true", help="count only; write nothing")
    args = ap.parse_args(argv)
    try:
        report = restamp(args.db, trees_root=args.trees_root, env_dir=args.env_dir,
                         mno=args.mno, stamp_missing=args.stamp_missing,
                         dry_run=args.dry_run)
    except PipelineError as exc:
        print(f"FAILED {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"  hint: {exc.hint}", file=sys.stderr)
        return 1
    tag = "would restamp" if report["dry_run"] else "restamped"
    for m, c in report["mnos"].items():
        print(f"  {m}: {c['entries']} entries, {c['records']} records — {tag} {c['restamped']}, "
              f"current {c['current']}, missing {c['missing']}, unresolved {c['unresolved']}")
    print(f"fingerprint version {report['version']}; {len(report['mnos'])} MNO(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
