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
        [--heal-torn] [--retry-failed [--include-all-filtered]] \\
        [--max-reqs N] \\
        [--stages prepare,bm25,enrich_corpus] [--dry-run]

`--only` restricts BOTH steps to the named cells (same <MNO>__<REL> format
either side). After the lane completes, restart the sira-query service(s) so
new cells load (/healthz shows them).
"""

from __future__ import annotations

import argparse
import os
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


def heal_targets(db_root: Path, run_name: str, only: str | None) -> list[Path]:
    """Existing doc-enrich run dirs the pre-lane repairs operate on, one
    per cell under db_root (restricted to --only cells when given)."""
    if not db_root.is_dir():
        return []
    cells = sorted(d for d in db_root.iterdir() if d.is_dir())
    if only:
        wanted = {c.strip() for c in only.split(",") if c.strip()}
        cells = [d for d in cells if d.name in wanted]
    run_dirs = [d / "runs" / "doc-enrich" / run_name for d in cells]
    return [rd for rd in run_dirs if rd.is_dir()]


def run_repairs(args: argparse.Namespace) -> None:
    """--heal-torn / --retry-failed: repair each target cell's doc-enrich
    run dir before the lane runs. Heal first (restores file structure so
    retry-failed can parse every row), then retry-failed.

    Mode interplay: meaningful for incremental runs (no wipe, or
    --wipe-stale-index, which KEEPS runs/). Under --wipe-all-derived the
    adapter deletes runs/ before enriching — the repairs are superseded,
    so they're skipped with a note instead of pretending to matter.
    """
    if not (args.heal_torn or args.retry_failed):
        return
    if args.wipe_all_derived:
        print("sira-lane: --wipe-all-derived wipes runs/ — skipping "
              "--heal-torn/--retry-failed (the full rebuild supersedes them).")
        return
    targets = heal_targets(args.db_root, args.run_name, args.only)
    if not targets:
        print(f"sira-lane: no runs/doc-enrich/{args.run_name} dirs under "
              f"{args.db_root} — nothing to repair.")
        return
    from sandbox.sira_incremental import heal_run_dir, retry_failed_in_run

    for rd in targets:
        cell = rd.parents[2].name
        if args.dry_run:
            verbs = " + ".join(v for v, on in (("heal-torn", args.heal_torn),
                                               ("retry-failed", args.retry_failed)) if on)
            print(f"sira-lane: would {verbs}: {rd}")
            continue
        if args.heal_torn:
            s = heal_run_dir(rd)
            print(f"sira-lane: healed {cell}/{args.run_name}: "
                  f"{sum(s['torn'].values())} torn line(s), "
                  f"{s['evicted_kept']} kept-without-enrichment, "
                  f"{s['orphan_enrichments']} orphan enrichment(s)")
        if args.retry_failed:
            evicted, _ = retry_failed_in_run(
                rd, "doc-enrich",
                include_all_filtered=args.include_all_filtered,
            )
            print(f"sira-lane: retry-failed {cell}/{args.run_name}: "
                  f"evicted {evicted} doc(s) for retry")


def main(argv: list[str] | None = None) -> int:
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
    p.add_argument("--heal-torn", action="store_true",
                   help="Before running: repair each cell's doc-enrich run "
                        "dir after a hard interruption (drop torn JSONL "
                        "lines, fix the kept/enrichment resume invariant). "
                        "No-op under --wipe-all-derived (runs/ is wiped)")
    p.add_argument("--retry-failed", action="store_true",
                   help="Before running: evict recorded-failed docs from "
                        "each cell's doc-enrich resume trace so this run "
                        "retries them (errors only; all_filtered rows kept). "
                        "No-op under --wipe-all-derived (runs/ is wiped)")
    p.add_argument("--include-all-filtered", action="store_true",
                   help="With --retry-failed: also retry status=all_filtered "
                        "rows (only useful after a prompt or LLM change)")
    p.add_argument("--max-reqs", type=int, default=None, metavar="N",
                   help="Cap reqs per enrichment batch (exported as "
                        "NORA_SIRA_BATCH_MAX_REQS to the build). 1 = "
                        "single-req mode: same batched prompt + retry/trace "
                        "machinery, one LLM call per req — the typical "
                        "pairing with --retry-failed. Never loosens the "
                        "response-budget-derived cap")
    p.add_argument("--stages", default="prepare,bm25,enrich_corpus")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the two commands, run nothing")
    args = p.parse_args(argv)

    if args.max_reqs is not None:
        if args.max_reqs < 1:
            p.error("--max-reqs must be >= 1")
        # child steps inherit os.environ; the flag wins over any preset var
        os.environ["NORA_SIRA_BATCH_MAX_REQS"] = str(args.max_reqs)
        print(f"sira-lane: NORA_SIRA_BATCH_MAX_REQS={args.max_reqs} "
              f"(reqs/batch cap{' — single-req mode' if args.max_reqs == 1 else ''})")

    run_repairs(args)

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
