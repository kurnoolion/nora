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
        [--only VZW__Feb2026,TMO__Jan2026] [--dry-run] \\
        [--verify --run-name NAME [--compare-run NAME] [--strict]]

`--verify` runs a READ-ONLY per-cell health sweep (sira_incremental
verify-run per cell + summary) instead of builds — paste-safe output.

LLM endpoints are configured via the per-stage-routing env vars
(NORA_SIRA_{ENRICH,RERANK}_LLM_URL etc.); when those are set, sglang
port is irrelevant. See sandbox/sira_patches/README.md.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from sandbox.sira_cells import CellKey, cell_dirname, enumerate_cells, parse_cell_dirname


def cell_config_root(db_root: Path) -> Path:
    """The out-of-clone Hydra config dir for generated per-cell data YAMLs.

    Lives under the db root (always writable — a mounted volume in
    containers) so the SIRA clone itself is never written at run time.
    The dot-name keeps it out of cell enumeration (`<mno>__<MMMYYYY>`
    pattern) and out of promote.sh's cell glob.
    """
    return Path(db_root) / ".hydra-configs"


def ensure_cell_data_config(
    sira_clone: Path,
    cell_name: str,
    template: str = "nora",
    config_root: "Path | None" = None,
) -> Path:
    """Write the per-cell data YAML; return its path.

    SIRA's `run_pipeline.py._with_dataset(cfg, ds_name)` re-reads
    `configs/data/<ds_name>.yaml` from disk for each dataset — it does NOT
    honor `data.name=<cell>` as an override on a reused config (correcting the
    SIRA D-DRAFT-6 "no per-cell config files" assumption). So each cell needs
    its own data YAML. We generate it from the installed `<template>.yaml`
    (`nora.yaml`) with only the `name:` field set to the cell — every other
    field (split, k_values, …) is cell-independent. Idempotent: rewritten each
    run so a changed template propagates.

    With `config_root` set (the normal path — see `cell_config_root`), the
    YAML lands OUTSIDE the clone at `<config_root>/data/<cell>.yaml` and the
    pipeline command carries `--config-dir <config_root>` so Hydra finds it;
    the template is still read from the clone (read-only). Without it, the
    legacy write-into-the-clone layout is used (requires a writable clone).
    """
    cfg_dir = sira_clone / "scripts" / "configs" / "data"
    tmpl_path = cfg_dir / f"{template}.yaml"
    if not tmpl_path.is_file():
        raise SystemExit(
            f"data config template not found: {tmpl_path} — run "
            f"install_configs.sh in the clone (SETUP.md §4) first?"
        )
    text = tmpl_path.read_text(encoding="utf-8")
    if re.search(r"(?m)^name:", text):
        text = re.sub(r"(?m)^name:.*$", f"name: {cell_name}", text)
    else:
        text = f"name: {cell_name}\n" + text
    if config_root is not None:
        out_dir = Path(config_root) / "data"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{cell_name}.yaml"
    else:
        out = cfg_dir / f"{cell_name}.yaml"
    out.write_text(text, encoding="utf-8")
    return out


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
    run_name: str | None = None,
) -> list[str]:
    """Build the `run_pipeline.py` argv for one cell (cwd = SIRA clone).

    `data.name=<cell>` makes SIRA's `_with_dataset` read
    `configs/data/<cell>.yaml`, which `run_cells` generates per cell via
    `ensure_cell_data_config` (SIRA's pipeline re-reads the file by name — it
    does NOT honor `data.name` as an override on a reused config, correcting the
    SIRA D-DRAFT-6 assumption). `db_root` is passed absolute so the command is
    cwd-independent of the clone. NOTE: `config_root` does NOT go on the
    argv — SIRA's `_with_dataset` reads the file via a literal path, not
    Hydra compose, so the external dir is passed via the
    `SIRA_EXTRA_CONFIG_DIR` env var (extra-config-dir.patch) by `run_cells`.
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
    if run_name:
        # Pin the doc-enrich run dir name so SIRA's resume accumulates across
        # invocations (continue after a crash; incremental re-enrich). Per cell
        # the run lives at <cell>/runs/doc-enrich/<run_name>/. run_pipeline
        # accepts `+run_name` (see sira_incremental's documented flow).
        cmd.append(f"+run_name={run_name}")
    return cmd


def run_cells(
    db_root: Path,
    sira_clone: Path,
    *,
    only: list[CellKey] | None = None,
    sglang_port: int | None = None,
    stages: list[str] | None = None,
    run_name: str | None = None,
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
    config_root = cell_config_root(db_root)
    # SIRA's _with_dataset reads per-dataset configs via a literal path;
    # extra-config-dir.patch makes it consult this env var FIRST, so the
    # generated cell YAMLs live OUT-OF-CLONE (the clone stays read-only).
    child_env = {**os.environ, "SIRA_EXTRA_CONFIG_DIR": str(config_root.resolve())}
    for cell in cells:
        cmd = build_pipeline_cmd(
            cell, db_root, sglang_port=sglang_port, stages=stages,
            run_name=run_name,
        )
        name = cell_dirname(cell)
        print(f"=== cell {name} ===")
        print(f"  SIRA_EXTRA_CONFIG_DIR={config_root} " + " ".join(cmd))
        if dry_run:
            results[cell] = 0
            continue
        ensure_cell_data_config(sira_clone, name, config_root=config_root)
        proc = subprocess.run(cmd, cwd=str(sira_clone), env=child_env)
        results[cell] = proc.returncode
        print(f"  cell {name}: exit {proc.returncode}")
        print()

    failed = [cell_dirname(c) for c, rc in results.items() if rc != 0]
    print(f"done — {len(results)} cell(s), {len(failed)} failed"
          + (f": {', '.join(failed)}" if failed else ""))
    return results


def verify_cells(
    db_root: Path,
    run_name: str,
    *,
    only: list[CellKey] | None = None,
    compare_run: str | None = None,
    strict: bool = False,
) -> int:
    """Read-only verify sweep over the cells (same enumeration + --only
    intersection semantics as run_cells; a requested-but-absent cell
    gets a warning). Per cell: sira_incremental.verify_cell — batch
    stats, trace reconciliation, kept↔enrichment invariant. Paste-safe.

    Exit code 1 when any cell FAILs (or WARNs under strict), when no
    cells match, or when every cell lacks the run name."""
    from sandbox.sira_incremental import verify_cell

    cells = enumerate_cells(db_root)
    if only is not None:
        only_set = set(only)
        for missing in sorted(only_set - set(cells)):
            print(f"--only: cell not found under {db_root}: "
                  f"{cell_dirname(missing)} — skipping")
        cells = [c for c in cells if c in only_set]
    if not cells:
        print(f"no cells found under {db_root} (expected <mno>__<MMMYYYY>/ dirs)")
        return 1

    print(f"{len(cells)} cell(s): {', '.join(cell_dirname(c) for c in cells)}")
    print()
    verdicts: dict[str, int] = {}
    for i, cell in enumerate(cells):
        if i:
            print()
        cell_dir = db_root / cell_dirname(cell)
        if not (cell_dir / "runs" / "doc-enrich" / run_name).is_dir():
            print(f"verify-run: {cell_dir.name}: no run dir for "
                  f"'{run_name}' — skipping")
            verdicts["skipped"] = verdicts.get("skipped", 0) + 1
            continue
        v = verify_cell(cell_dir, run_name, compare_run=compare_run)
        verdicts[v] = verdicts.get(v, 0) + 1

    print(f"\nverify summary: {len(cells)} cell(s) — "
          + ", ".join(f"{n} {v}" for v, n in sorted(verdicts.items())))
    if verdicts.get("skipped") == len(cells):
        print("ERROR: every cell was skipped — check --run-name")
        return 1
    if verdicts.get("FAIL") or (strict and verdicts.get("WARN")):
        return 1
    return 0


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
    p.add_argument("--sira-clone", type=Path, default=None,
                   help="Path to the SIRA clone (run_pipeline.py lives in "
                        "<sira-clone>/scripts/). Required for builds; "
                        "unused with --verify.")
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
    p.add_argument("--run-name", default=None,
                   help="Pin the doc-enrich run dir name (e.g. enrich-stable) so "
                        "SIRA's resume accumulates across runs — needed for "
                        "reliable resume after an interrupted run and for "
                        "incremental re-enrich. Per cell the run lives at "
                        "<cell>/runs/doc-enrich/<run-name>/; point the service's "
                        "NORA_SIRA_DOC_ENRICH_RUN at the same name. Default: "
                        "run_pipeline's own (timestamped) run name.")
    p.add_argument("--enrich-doc-chunks", action="store_true",
                   help="Also enrich coarse doc:-prefixed corpus rows. "
                        "Default: builds skip them (traced as "
                        "skipped_doc_chunk).")
    p.add_argument("--enrich-section-chunks", action="store_true",
                   help="Also enrich coarse section:-prefixed corpus rows. "
                        "Default: builds skip them (traced as "
                        "skipped_section_chunk).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the per-cell commands without executing.")
    p.add_argument("--verify", action="store_true",
                   help="Read-only verify sweep INSTEAD of builds: per-cell "
                        "sira_incremental verify-run report + summary. "
                        "Requires --run-name; --sira-clone not needed. "
                        "Paste-safe (counts/statuses only).")
    p.add_argument("--compare-run", default=None, metavar="NAME",
                   help="With --verify: diff each cell's phrase sets against "
                        "this second run name (Jaccard agreement, counts only).")
    p.add_argument("--strict", action="store_true",
                   help="With --verify: exit 1 on WARN too (default: only FAIL).")
    args = p.parse_args(argv)

    only = _parse_cell_list(args.only) if args.only else None

    if args.verify:
        if not args.run_name:
            raise SystemExit("--verify requires --run-name (the pinned "
                             "doc-enrich run to verify)")
        return verify_cells(
            args.db_root, args.run_name,
            only=only, compare_run=args.compare_run, strict=args.strict,
        )

    if args.sira_clone is None or not (
            args.sira_clone / "scripts" / "run_pipeline.py").is_file():
        raise SystemExit(
            f"run_pipeline.py not found under "
            f"{args.sira_clone}/scripts/ — is --sira-clone correct? "
            f"(--sira-clone is required except with --verify)"
        )

    # Coarse-chunk opt-ins travel to the batched-enrich layer as env vars
    # (children inherit; BatchConfig.from_env reads them). Default: skip.
    if args.enrich_doc_chunks:
        os.environ["NORA_SIRA_BATCH_ENRICH_DOC_CHUNKS"] = "1"
        print("sira-multi: NORA_SIRA_BATCH_ENRICH_DOC_CHUNKS=1 "
              "(doc: chunks will be enriched)")
    if args.enrich_section_chunks:
        os.environ["NORA_SIRA_BATCH_ENRICH_SECTION_CHUNKS"] = "1"
        print("sira-multi: NORA_SIRA_BATCH_ENRICH_SECTION_CHUNKS=1 "
              "(section: chunks will be enriched)")

    stages = [s.strip() for s in args.stages.split(",")] if args.stages else None
    results = run_cells(
        args.db_root, args.sira_clone,
        only=only, sglang_port=args.sglang_port, stages=stages,
        run_name=args.run_name, dry_run=args.dry_run,
    )
    return 1 if any(rc != 0 for rc in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
