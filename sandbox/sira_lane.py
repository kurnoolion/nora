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
        [--heal-torn] [--retry-failed [--include-all-filtered] [--include-skipped]
                       [--include-refused]] \\
        [--enrich-doc-chunks] [--enrich-section-chunks] \\
        [--max-reqs N] [--verify] \\
        [--index flat|cell-db] [--overlay-root <corrections-root>] \\
        [--stages prepare,bm25,enrich_corpus] [--dry-run]

`--index=cell-db` (corp-db-layer phase 3) DUAL-EMITS: after the flat
build (and --verify, when given) succeeds, each built cell is also
converted into a SQLite cell DB under `<db_root>/cell-dbs/` via
sandbox.cell_db_convert — flat files remain the primary output and the
default (`--index=flat`) stays byte-identical to today. The DBs ride
label promotion with the rest of the build tree; a cell-db stack mounts
`<label>/sira/cell-dbs` as its NORA_SIRA_DB_ROOT.

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
    if args.enrich_doc_chunks:
        multi += ["--enrich-doc-chunks"]
    if args.enrich_section_chunks:
        multi += ["--enrich-section-chunks"]
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
                include_skipped=args.include_skipped,
                include_refused=args.include_refused,
            )
            print(f"sira-lane: retry-failed {cell}/{args.run_name}: "
                  f"evicted {evicted} doc(s) for retry")


def run_cell_db_emit(args: argparse.Namespace) -> int:
    """`--index=cell-db` dual-emit (corp-db-layer phase 3): convert the
    just-built cells into SQLite cell DBs under <db_root>/cell-dbs/.

    Runs only after the flat build succeeded — flat files stay the
    primary output; a conversion failure fails the lane loudly rather
    than leaving a silently stale DB next to a fresh flat cell. The
    enrichment selection mirrors what this lane just built: the lane's
    --run-name is pinned when the enrich_corpus stage ran, else the
    converter's best-pointer fallback (a bm25-only build has no run to
    pin). --force is implied: the lane just rebuilt these cells, so any
    existing DB for them is stale by definition.
    """
    from sandbox.cell_db_convert import ConvertError, convert, print_report, write_report

    out_dir = args.db_root / "cell-dbs"
    enrich_run = args.run_name if "enrich_corpus" in args.stages else ""
    cells_filter = ([c.strip() for c in args.only.split(",") if c.strip()]
                    if args.only else None)
    if args.dry_run:
        scope = args.only or "all cells"
        pin = f"pinned run {enrich_run}" if enrich_run else "best-pointer enrichment"
        print(f"sira-lane: would emit cell DBs ({scope}, {pin}) -> {out_dir}")
        return 0
    try:
        report = convert(
            args.db_root, out_dir, cells_filter=cells_filter,
            enrich_run=enrich_run, overlay_root=str(args.overlay_root or ""),
            force=True,
        )
    except ConvertError as exc:
        print(f"sira-lane: cell-db emit FAILED {exc}", file=sys.stderr)
        return 1
    write_report(report, out_dir)
    print("sira-lane: cell DBs emitted (dual-emit — flat files unchanged):")
    print_report(report)
    return 0


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
    p.add_argument("--include-skipped", action="store_true",
                   help="With --retry-failed: also evict skipped_* rows "
                        "(coarse doc:/section: chunks). Pair with "
                        "--enrich-doc-chunks/--enrich-section-chunks so this "
                        "run actually enriches them instead of re-skipping")
    p.add_argument("--include-refused", action="store_true",
                   help="With --retry-failed: also evict llm_refused rows "
                        "(inputs the endpoint permanently refuses). Set "
                        "after configuring the fallback LLM in "
                        ".env.sira-batch so the retry can succeed")
    p.add_argument("--enrich-doc-chunks", action="store_true",
                   help="Enrich coarse doc:-prefixed corpus rows too "
                        "(forwarded to sira_multi; default: skipped, traced "
                        "as skipped_doc_chunk)")
    p.add_argument("--enrich-section-chunks", action="store_true",
                   help="Enrich coarse section:-prefixed corpus rows too "
                        "(forwarded to sira_multi; default: skipped, traced "
                        "as skipped_section_chunk)")
    p.add_argument("--verify", action="store_true",
                   help="After the lane completes: read-only per-cell "
                        "verify sweep (sira_multi.verify_cells over the "
                        "same --only scope and --run-name). Non-zero exit "
                        "when any cell FAILs — the lane's post-build gate. "
                        "Paste-safe (counts/statuses only)")
    p.add_argument("--max-reqs", type=int, default=None, metavar="N",
                   help="Cap reqs per enrichment batch (exported as "
                        "NORA_SIRA_BATCH_MAX_REQS to the build). 1 = "
                        "single-req mode: same batched prompt + retry/trace "
                        "machinery, one LLM call per req — the typical "
                        "pairing with --retry-failed. Never loosens the "
                        "response-budget-derived cap")
    p.add_argument("--stages", default="prepare,bm25,enrich_corpus")
    p.add_argument("--index", choices=("flat", "cell-db"), default="flat",
                   help="flat (default): today's output, unchanged. "
                        "cell-db: ALSO emit SQLite cell DBs under "
                        "<db_root>/cell-dbs/ after the build (dual-emit; "
                        "corp-db-layer phase 3)")
    p.add_argument("--overlay-root", default=None, type=Path,
                   help="with --index=cell-db: corrections root whose "
                        "overlay (main view) is baked into the cell DBs; "
                        "default none")
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
    if args.verify:
        if args.dry_run:
            print(f"sira-lane: would verify cells (run-name {args.run_name})")
        else:
            from sandbox.sira_multi import _parse_cell_list, verify_cells
            print("sira-lane: verifying cells (read-only)…")
            rc = verify_cells(
                args.db_root, args.run_name,
                only=_parse_cell_list(args.only) if args.only else None,
            )
            if rc != 0:
                print("sira-lane: verification FAILED — see reports above.",
                      file=sys.stderr)
                return rc
    if args.index == "cell-db":
        rc = run_cell_db_emit(args)
        if rc != 0:
            return rc
    if not args.dry_run:
        print("sira-lane: done. Restart sira-query service(s) to load new cells "
              "(/healthz lists them).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
