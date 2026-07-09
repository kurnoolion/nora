"""SIRA lane runner — one entrypoint for the sira retrieval lane
(docker-distro lane model), symmetric with `run_cli --lane ingestion|nora`.

Runs, in order:
  1. adapter  : nora_to_beir  (parse trees -> per-cell BEIR datasets)
  2. builds   : sira_multi    (prepare, bm25, enrich_corpus per cell)

Consumes the ingestion lane's parse output; independent of the nora lane.
Lives sandbox-side by design — core never imports sandbox (D-111 boundary).

Usage:
    python -m sandbox.sira_lane \\
        --env-dir <env_dir> --db-root <db_root> \\
        [--sira-clone sandbox/sira] [--run-name enrich-stable] \\
        [--only <MNO>__<REL>[,...]] \\
        [--wipe-stale-index | --wipe-all-derived] \\
        [--stages prepare,bm25,enrich_corpus] [--dry-run]

`--only` restricts BOTH steps to the named cells (same <MNO>__<REL> format
either side). After the lane completes, restart the sira-query service(s) so
new cells load (/healthz shows them).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def build_commands(args: argparse.Namespace) -> list[list[str]]:
    """Pure command builder (unit-testable, drives --dry-run)."""
    adapter = [
        sys.executable, "-m", "sandbox.adapter.nora_to_beir",
        "--env-dir", str(args.env_dir),
        "--output", str(args.db_root),
        "--multi-cell",
    ]
    if args.only:
        adapter += ["--only", args.only]
    if args.wipe_all_derived:
        adapter += ["--wipe-all-derived"]
    elif args.wipe_stale_index:
        adapter += ["--wipe-stale-index"]

    multi = [
        sys.executable, "-m", "sandbox.sira_multi",
        "--db-root", str(args.db_root),
        "--sira-clone", str(args.sira_clone),
        "--run-name", args.run_name,
        "--stages", args.stages,
    ]
    if args.only:
        multi += ["--only", args.only]
    return [adapter, multi]


def main() -> int:
    p = argparse.ArgumentParser(
        prog="sira_lane",
        description="Run the SIRA retrieval lane: adapter + per-cell index/enrich.",
    )
    p.add_argument("--env-dir", required=True, type=Path,
                   help="NORA env dir (ingestion lane output: out/parse trees)")
    p.add_argument("--db-root", required=True, type=Path,
                   help="SIRA db_root (one <MNO>__<MMMYYYY>/ dataset per cell)")
    p.add_argument("--sira-clone", default=Path("sandbox/sira"), type=Path)
    p.add_argument("--run-name", default="enrich-stable",
                   help="Pinned run name — reuse forever (resume/prune/retry)")
    p.add_argument("--only", default=None,
                   help="Comma-separated <MNO>__<REL> cells (both steps)")
    p.add_argument("--wipe-stale-index", action="store_true",
                   help="Incremental-safe wipe (keep runs/ enrichment cache)")
    p.add_argument("--wipe-all-derived", action="store_true",
                   help="Full-rebuild wipe (re-enriches everything)")
    p.add_argument("--stages", default="prepare,bm25,enrich_corpus")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the two commands, run nothing")
    args = p.parse_args()

    cmds = build_commands(args)
    for cmd in cmds:
        print("sira-lane$ " + " ".join(cmd))
        if args.dry_run:
            continue
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"sira-lane: step failed (exit {rc}) — stopping.", file=sys.stderr)
            return rc
    if not args.dry_run:
        print("sira-lane: done. Restart sira-query service(s) to load new cells "
              "(/healthz lists them).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
