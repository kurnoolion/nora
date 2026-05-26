"""Incremental enrichment helper for the plan-aware-sira pipeline.

Rides SIRA's native doc_id-keyed resume (add_doc_index_adapter.py:179
"Resume: skip already-processed docs") to avoid re-enriching unchanged
documents, and adds CONTENT-hash awareness so a doc whose text changed
under the same doc_id (e.g., a correction was applied) gets correctly
re-enriched instead of wrongly skipped.

Why this matters: corpus growth is the steady state (more releases,
more MNOs). A full re-enrichment is ~13h. SIRA's resume already skips
docs whose doc_id is in the run's trace — so adding a release only
LLM-enriches the NEW doc_ids. This helper closes the one gap: detecting
docs whose *content* changed (same id) so they don't get stale
enrichment reused.

What stays cheap and always runs (corpus-wide, NO LLM):
  - BM25 index rebuild (eval_bm25) — seconds-to-minutes
  - DF-filter (during enrich, over the corpus's NGramIndex)
What scales with NEW+CHANGED docs only (the expensive LLM part):
  - per-doc enrichment LLM calls

Two sub-commands wrap a SIRA enrich run (pin the run_name across runs
so the trace accumulates):

  prune   — BEFORE enrich. Compare current corpus content-hashes to the
            stored baseline. Evict changed + removed doc_ids from the
            run's trace.kept / trace.failed / enrichments.kept files so
            SIRA's resume re-enriches the changed ones (and removed ones
            drop out, avoiding a KeyError in enrich_batch's
            doc_id_to_idx lookup). Unchanged docs stay in the trace →
            skipped → no LLM call.

  commit  — AFTER a successful enrich. Record current corpus hashes as
            the new baseline for the next round.

Usage (work-PC flow, run_name pinned):
  RUN=enrich-stable
  DS=$NORA_SIRA_DB_ROOT/$NORA_SIRA_DATASET

  # 1. adapter rebuild (keeps runs/ cache):
  python -m sandbox.adapter.nora_to_beir --env-dir <env> \\
      --output $DS_PARENT/$DATASET --wipe-stale-index

  # 2. prune changed/removed docs from the resume trace:
  python -m sandbox.sira_incremental prune \\
      --dataset $DS --run-name $RUN

  # 3. rebuild index + resume-enrich (only new+changed docs hit the LLM):
  #    (eval_bm25 then run_pipeline enrich +run_name=$RUN ...)

  # 4. record the new baseline:
  python -m sandbox.sira_incremental commit \\
      --dataset $DS --run-name $RUN
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


_HASH_STORE_NAME = ".incremental_hashes.json"


def _combined_text(title: str, text: str) -> str:
    """Mirror SIRA's read_corpus_texts combination: "title. text" when
    title is non-empty, else just text. Hashing this exact string means
    a change to EITHER field flips the hash → triggers re-enrichment."""
    title = title or ""
    text = text or ""
    if title.strip():
        return f"{title}. {text}"
    return text


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def corpus_hashes(corpus_path: Path) -> dict[str, str]:
    """{doc_id: content_hash} for every row in corpus.jsonl, hashing
    the same combined text SIRA enriches."""
    out: dict[str, str] = {}
    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            did = obj.get("_id")
            if not did:
                continue
            out[did] = _hash(_combined_text(obj.get("title", ""), obj.get("text", "")))
    return out


def load_hash_store(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_hash_store(path: Path, hashes: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hashes, indent=0, sort_keys=True), encoding="utf-8")


def compute_evictions(
    current: dict[str, str], stored: dict[str, str],
) -> tuple[set[str], set[str], set[str]]:
    """Partition doc_ids by what the enrich run must do.

    Returns (changed, removed, new):
      - changed: in both, hash differs → re-enrich (evict from trace)
      - removed: in stored but not current → drop from trace+enrichments
                 (else enrich_batch KeyErrors on the missing doc_id)
      - new: in current but not stored → enrich (not in trace, so SIRA
             handles them natively; reported for visibility)

    Evictions = changed ∪ removed. `new` is informational.
    """
    changed = {d for d, h in current.items() if d in stored and stored[d] != h}
    removed = set(stored) - set(current)
    new = set(current) - set(stored)
    return changed, removed, new


def _prune_jsonl(path: Path, evict_ids: set[str]) -> int:
    """Rewrite a doc_id-keyed JSONL file, dropping lines whose doc_id is
    in evict_ids. Returns number of lines dropped. No-op if file absent."""
    if not path.exists():
        return 0
    kept_lines: list[str] = []
    dropped = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                did = json.loads(stripped).get("doc_id")
            except json.JSONDecodeError:
                kept_lines.append(stripped)  # keep unparseable lines as-is
                continue
            if did in evict_ids:
                dropped += 1
            else:
                kept_lines.append(stripped)
    if dropped:
        with open(path, "w", encoding="utf-8") as f:
            for ln in kept_lines:
                f.write(ln + "\n")
    return dropped


def prune_run_files(run_dir: Path, evict_ids: set[str]) -> dict[str, int]:
    """Evict doc_ids from the three resume-relevant files in a SIRA
    doc-enrich run dir. Returns per-file dropped counts."""
    counts: dict[str, int] = {}
    for name in ("trace.kept.jsonl", "trace.failed.jsonl", "enrichments.kept.jsonl"):
        counts[name] = _prune_jsonl(run_dir / name, evict_ids)
    return counts


# ── CLI ─────────────────────────────────────────────────────────────


def _resolve_paths(dataset_dir: Path, run_name: str) -> tuple[Path, Path, Path]:
    """Return (corpus_path, hash_store_path, run_dir)."""
    corpus = dataset_dir / "raw" / "corpus.jsonl"
    hash_store = dataset_dir / _HASH_STORE_NAME
    run_dir = dataset_dir / "runs" / "doc-enrich" / run_name
    return corpus, hash_store, run_dir


def cmd_prune(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dataset)
    corpus, hash_store_path, run_dir = _resolve_paths(dataset_dir, args.run_name)

    if not corpus.exists():
        print(f"ERROR: corpus not found at {corpus}")
        return 1

    current = corpus_hashes(corpus)
    stored = load_hash_store(hash_store_path)
    changed, removed, new = compute_evictions(current, stored)
    evict = changed | removed

    print(f"corpus:    {len(current)} docs")
    print(f"baseline:  {len(stored)} docs (from {hash_store_path.name})")
    print(f"  new:     {len(new)} (not in baseline → SIRA enriches natively)")
    print(f"  changed: {len(changed)} (content differs → evict from trace, re-enrich)")
    print(f"  removed: {len(removed)} (gone from corpus → drop from trace/enrichments)")

    if not run_dir.is_dir():
        print(f"\nNo run dir at {run_dir} — nothing to prune. "
              f"This is a fresh run; all {len(current)} docs will enrich.")
        return 0

    if not evict:
        print("\nNothing to evict — trace is consistent with the corpus. "
              "Resume will skip all baseline docs; only new docs enrich.")
        return 0

    counts = prune_run_files(run_dir, evict)
    print(f"\npruned run dir {run_dir.name}:")
    for name, n in counts.items():
        print(f"  {name}: dropped {n}")
    print("\n→ SIRA resume will now re-enrich the changed docs and skip "
          "the unchanged ones.")
    return 0


def cmd_commit(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dataset)
    corpus, hash_store_path, _ = _resolve_paths(dataset_dir, args.run_name)
    if not corpus.exists():
        print(f"ERROR: corpus not found at {corpus}")
        return 1
    current = corpus_hashes(corpus)
    save_hash_store(hash_store_path, current)
    print(f"baseline updated: {len(current)} doc hashes → {hash_store_path}")
    print("→ next `prune` compares against this baseline.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sira_incremental",
        description="Content-hash-aware incremental enrichment for SIRA.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    common_args = [
        (("--dataset",), {"required": True,
         "help": "SIRA dataset dir (<db_root>/<dataset>), the one with raw/, runs/."}),
        (("--run-name",), {"required": True,
         "help": "Pinned doc-enrich run_name (matches +run_name= passed to SIRA)."}),
    ]

    pp = sub.add_parser("prune", help="Pre-enrich: evict changed/removed docs from the resume trace.")
    pc = sub.add_parser("commit", help="Post-enrich: record current corpus hashes as the new baseline.")
    for parser in (pp, pc):
        for a, kw in common_args:
            parser.add_argument(*a, **kw)

    args = p.parse_args(argv)
    return {"prune": cmd_prune, "commit": cmd_commit}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
