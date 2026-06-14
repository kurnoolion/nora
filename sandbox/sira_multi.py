"""Batch orchestrator for multi-MNO SIRA (multi-mno-sira D-DRAFT-7).

Enumerates the (MNO, release) cells under a db_root (emitted by
`nora_to_beir.py --multi-cell`) and runs SIRA's `run_pipeline.py` once
per cell with `data.name=<cell>`. SIRA itself is unchanged — this is the
NORA-side loop that keeps the "patch, don't fork" posture (consistent
with the per-stage-routing patch).

Each cell enriches independently; SIRA's incremental enrichment
(content-hash resume) means a new release reuses unchanged docs'
enrichment, so cell count doesn't multiply the cost.

Usage::

    python -m sandbox.sira_multi \\
        --db-root  sandbox/adapter/out \\
        --sira-clone sandbox/sira \\
        [--sglang-port 8030] [--stages prepare,bm25,enrich_corpus] \\
        [--only VZW__Feb2026,TMO__Jan2026] [--dry-run]

LLM endpoints are configured via the per-stage-routing env vars
(NORA_SIRA_{ENRICH,RERANK}_LLM_URL etc.); when those are set, sglang
port is irrelevant. See sandbox/sira_patches/README.md.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from sandbox.sira_cells import CellKey, cell_dirname, enumerate_cells, parse_cell_dirname


def build_pipeline_cmd(
    cell: CellKey,
    db_root: Path,
    *,
    python_exe: str = sys.executable,
    data_config: str = "nora",
    enrich_config: str = "nora",
    rerank_config: str = "nora",
    sglang_port: int | None = None,
    stages: list[str] | None = None,
) -> list[str]:
    """Build the `run_pipeline.py` argv for one cell (cwd = SIRA clone).

    Reuses one `data` config with `data.name=<cell>` overridden per cell
    (D-DRAFT-6) — no per-cell config files. `db_root` is passed absolute
    so the command is cwd-independent of the clone.
    """
    name = cell_dirname(cell)
    cmd = [
        python_exe, "scripts/run_pipeline.py",
        f"data={data_config}",
        f"enrich={enrich_config}",
        f"rerank={rerank_config}",
        f"data.name={name}",
        f"db_root={db_root.resolve()}",
    ]
    if sglang_port is not None:
        cmd.append(f"sglang.port={sglang_port}")
    if stages:
        cmd.append("stages=[" + ",".join(stages) + "]")
    return cmd


def run_cells(
    db_root: Path,
    sira_clone: Path,
    *,
    only: list[CellKey] | None = None,
    sglang_port: int | None = None,
    stages: list[str] | None = None,
    dry_run: bool = False,
) -> dict[CellKey, int]:
    """Run the SIRA pipeline per cell. Returns {cell: returncode}.

    Continue-on-error: a failing cell doesn't abort the rest — all are
    attempted and the per-cell return codes are reported, so one bad
    cell doesn't cost the whole batch. dry_run prints the commands
    without executing.
    """
    cells = enumerate_cells(db_root)
    if only is not None:
        only_set = set(only)
        cells = [c for c in cells if c in only_set]
    if not cells:
        print(f"no cells found under {db_root} (expected <mno>__<MMMYYYY>/ dirs)")
        return {}

    print(f"{len(cells)} cell(s): {', '.join(cell_dirname(c) for c in cells)}")
    print()
    results: dict[CellKey, int] = {}
    for cell in cells:
        cmd = build_pipeline_cmd(
            cell, db_root, sglang_port=sglang_port, stages=stages,
        )
        name = cell_dirname(cell)
        print(f"=== cell {name} ===")
        print("  " + " ".join(cmd))
        if dry_run:
            results[cell] = 0
            continue
        proc = subprocess.run(cmd, cwd=str(sira_clone))
        results[cell] = proc.returncode
        print(f"  cell {name}: exit {proc.returncode}")
        print()

    failed = [cell_dirname(c) for c, rc in results.items() if rc != 0]
    print(f"done — {len(results)} cell(s), {len(failed)} failed"
          + (f": {', '.join(failed)}" if failed else ""))
    return results


def _parse_cell_list(raw: str) -> list[CellKey]:
    cells: list[CellKey] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        ck = parse_cell_dirname(tok)
        if ck is None:
            raise SystemExit(
                f"--only: {tok!r} is not a valid cell name (<mno>__<MMMYYYY>)"
            )
        cells.append(ck)
    return cells


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sira_multi",
        description="Run SIRA's pipeline once per (MNO, release) cell.",
    )
    p.add_argument("--db-root", required=True, type=Path,
                   help="Parent dir of the <mno>__<release>/ cell datasets "
                        "(the --output of nora_to_beir --multi-cell).")
    p.add_argument("--sira-clone", required=True, type=Path,
                   help="Path to the SIRA clone (run_pipeline.py lives in "
                        "<sira-clone>/scripts/).")
    p.add_argument("--sglang-port", type=int, default=None,
                   help="sglang.port override. Omit when LLM is routed via "
                        "NORA_SIRA_*_LLM_URL env vars (per-stage routing).")
    p.add_argument("--stages", default="prepare,bm25,enrich_corpus",
                   help="Comma-separated stage list. Default "
                        "'prepare,bm25,enrich_corpus' — the corpus-only "
                        "stages the multi-cell flow needs. Cells have no "
                        "real queries/qrels (OQ-2 deferred), so "
                        "enrich_query / rerank / eval are skipped; the "
                        "runtime service does query enrichment + rerank "
                        "live. Pass an explicit list to override.")
    p.add_argument("--only", default=None,
                   help="Comma-separated cell names to run (subset). "
                        "Default: all cells under --db-root.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the per-cell commands without executing.")
    args = p.parse_args(argv)

    if not (args.sira_clone / "scripts" / "run_pipeline.py").is_file():
        raise SystemExit(
            f"run_pipeline.py not found under {args.sira_clone}/scripts/ — "
            f"is --sira-clone correct?"
        )

    stages = [s.strip() for s in args.stages.split(",")] if args.stages else None
    only = _parse_cell_list(args.only) if args.only else None
    results = run_cells(
        args.db_root, args.sira_clone,
        only=only, sglang_port=args.sglang_port, stages=stages,
        dry_run=args.dry_run,
    )
    return 1 if any(rc != 0 for rc in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
