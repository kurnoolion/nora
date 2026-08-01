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

Four sub-commands wrap a SIRA enrich run (pin the run_name across runs
so the trace accumulates):

  prune         — BEFORE enrich. Compare current corpus content-hashes to
                  the stored baseline. Evict changed + removed doc_ids
                  from the run's trace.kept / trace.failed /
                  enrichments.kept files so SIRA's resume re-enriches the
                  changed ones (and removed ones drop out, avoiding a
                  KeyError in enrich_batch's doc_id_to_idx lookup).
                  Unchanged docs stay in the trace → skipped → no LLM call.

  commit        — AFTER a successful enrich. Record current corpus hashes
                  as the new baseline for the next round.

  promote       — RECOVERY. Reconstruct enrichments/doc/<run>.jsonl and
                  best.jsonl from the run's enrichments.kept.jsonl when a
                  full-resume run (enriched_count == 0) leaves best.jsonl
                  as a dangling symlink.

  retry-failed  — Evict failed entries from a stage's resume trace so the
                  next pipeline run re-processes them. Default scope:
                  status != 'all_filtered' (genuine errors only). Pass
                  --include-all-filtered after a prompt or LLM swap to
                  also retry the all_filtered rows.

  heal-torn     — RECOVERY, after a hard interruption (power loss, killed
                  container). Drops torn (half-written) trailing lines
                  from every *.jsonl in the run dir, then repairs the
                  doc-enrich resume invariant the interruption may have
                  broken: a trace.kept row whose enrichment row was lost
                  is evicted (else the doc stays 'done' with its
                  enrichment silently gone), and an enrichment row whose
                  trace.kept row was lost is dropped (else the re-enriched
                  doc merges duplicate phrases). Then resume as usual —
                  same command, same run_name. (`sira_lane --heal-torn`
                  runs this inline before the lane.)

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
import collections
import hashlib
import json
import os
from pathlib import Path


_HASH_STORE_NAME = ".incremental_hashes.json"
_META_NAME = ".incremental_meta.json"

# Default cumulative-growth ratio (current corpus size ÷ size at last
# full rebuild) above which we warn that the corpus-wide DF statistics
# have likely drifted enough that cached enrichment is meaningfully
# stale and a --wipe-all-derived full rebuild is advisable. 1.5 = 50%
# cumulative growth since the last full re-baseline. Tunable per corpus.
_DEFAULT_MAX_GROWTH_RATIO = 1.5


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


def load_meta(path: Path) -> dict:
    """Incremental metadata sidecar: tracks the corpus size + date at
    the last FULL rebuild, for cumulative-drift detection. Distinct
    from the per-doc hash store (which moves forward on every commit
    and so can't see cumulative drift)."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_meta(path: Path, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")


def growth_assessment(
    current_size: int, full_rebuild_size: int | None, max_ratio: float,
) -> tuple[float | None, bool]:
    """Return (ratio, exceeded). ratio is current/full-rebuild size, or
    None when no full-rebuild baseline is recorded. exceeded is True
    when ratio > max_ratio."""
    if not full_rebuild_size:
        return None, False
    ratio = current_size / full_rebuild_size
    return ratio, ratio > max_ratio


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


# ── retry-failed: per-stage, key-aware eviction ─────────────────────────
#
# Doc-enrich resumes by doc_id; rerank resumes by (query_id, doc_id) pair —
# so eviction needs different key shapes per stage.

_STAGE_FILES: dict[str, tuple[str, ...]] = {
    "doc-enrich": ("trace.kept.jsonl", "trace.failed.jsonl", "enrichments.kept.jsonl"),
    "rerank":     ("trace.kept.jsonl", "trace.failed.jsonl"),
}

_STAGE_KEY_FN = {
    "doc-enrich": lambda r: r.get("doc_id"),
    "rerank":     lambda r: (r.get("query_id"), r.get("doc_id")),
}


def _prune_jsonl_by_keys(path: Path, evict_keys: set, key_fn) -> int:
    """Generalization of _prune_jsonl: drop rows whose key (via key_fn) is
    in evict_keys. Returns number of lines dropped. No-op if file absent.
    Defensive — unparseable lines are kept; rows whose key contains a None
    component (e.g., missing query_id on a rerank row) are kept."""
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
                key = key_fn(json.loads(stripped))
            except json.JSONDecodeError:
                kept_lines.append(stripped)
                continue
            if key in evict_keys:
                dropped += 1
            else:
                kept_lines.append(stripped)
    if dropped:
        with open(path, "w", encoding="utf-8") as f:
            for ln in kept_lines:
                f.write(ln + "\n")
    return dropped


def retry_failed_in_run(
    run_dir: Path,
    stage: str,
    *,
    include_all_filtered: bool = False,
) -> tuple[int, dict[str, int]]:
    """Evict failed entries from one SIRA stage's run dir so SIRA's resume
    reprocesses them next run. Returns (eviction_count, per_file_dropped).

    Default behavior skips `status='all_filtered'` rows — re-running them
    won't change the outcome unless prompts or LLM changed. Pass
    `include_all_filtered=True` after a prompt or LLM swap to retry them
    too. Doc-enrich keys on doc_id; rerank keys on (query_id, doc_id).
    """
    if stage not in _STAGE_FILES:
        raise ValueError(
            f"unknown stage {stage!r}; expected one of {tuple(_STAGE_FILES)}"
        )
    file_names = _STAGE_FILES[stage]
    key_fn = _STAGE_KEY_FN[stage]
    failed_path = run_dir / "trace.failed.jsonl"

    if not failed_path.exists():
        return 0, {name: 0 for name in file_names}

    evict_keys: set = set()
    with open(failed_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not include_all_filtered and rec.get("status") == "all_filtered":
                continue
            key = key_fn(rec)
            # Skip keys with any None component — they can't have been
            # written to the resume trace with a valid lookup shape.
            if key is None:
                continue
            if isinstance(key, tuple) and any(c is None for c in key):
                continue
            evict_keys.add(key)

    counts: dict[str, int] = {}
    for name in file_names:
        counts[name] = _prune_jsonl_by_keys(run_dir / name, evict_keys, key_fn)
    return len(evict_keys), counts


# ── heal-torn: post-interruption run-dir repair ──────────────────────────
#
# A hard interruption (power loss, SIGKILL) loses each open file's buffered
# tail independently, so a run dir can be left with (a) torn half-written
# trailing lines in any *.jsonl, and (b) a broken resume invariant: for
# doc-enrich, every trace.kept doc_id must have an enrichments.kept row
# (write_enrich runs first, but the two buffers flush independently — either
# side's row can survive without the other).


def heal_torn_lines(path: Path) -> tuple[int, int]:
    """Drop unparseable (torn) lines from a JSONL file. Returns
    (kept, dropped); rewrites the file only when something was dropped.
    No-op if the file is absent."""
    if not path.exists():
        return 0, 0
    kept_lines: list[str] = []
    dropped = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                json.loads(stripped)
            except json.JSONDecodeError:
                dropped += 1
                continue
            kept_lines.append(stripped)
    if dropped:
        with open(path, "w", encoding="utf-8") as f:
            for ln in kept_lines:
                f.write(ln + "\n")
    return len(kept_lines), dropped


def _jsonl_doc_ids(paths: list[Path]) -> set[str]:
    """Union of doc_ids across JSONL files (missing files, blank or
    unparseable lines, and rows without doc_id are skipped)."""
    ids: set[str] = set()
    for p in paths:
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    did = json.loads(line).get("doc_id")
                except json.JSONDecodeError:
                    continue
                if did is not None:
                    ids.add(did)
    return ids


def heal_run_dir(run_dir: Path, stage: str = "doc-enrich") -> dict:
    """Repair one SIRA stage run dir after a hard interruption.

    1. Drop torn lines from every *.jsonl (shard-suffixed files included).
    2. doc-enrich only — restore the resume invariant (kept ids ==
       enrichment ids, both directions):
         - trace.kept row without an enrichment row → evicted, so the doc
           re-enriches instead of staying 'done' with its enrichment lost;
         - enrichment row without a trace.kept row → dropped, so the
           re-enriched doc doesn't merge duplicate phrases.

    Returns {"torn": {filename: dropped}, "evicted_kept": n,
    "orphan_enrichments": n}. Safe on a healthy dir (all zeros, no writes).
    """
    torn: dict[str, int] = {}
    for p in sorted(run_dir.glob("*.jsonl")):
        _, dropped = heal_torn_lines(p)
        if dropped:
            torn[p.name] = dropped

    evicted_kept = orphan_enrichments = 0
    if stage == "doc-enrich":
        kept_files = sorted(run_dir.glob("trace.kept*.jsonl"))
        enrich_files = sorted(run_dir.glob("enrichments.kept*.jsonl"))
        kept_ids = _jsonl_doc_ids(kept_files)
        enrich_ids = _jsonl_doc_ids(enrich_files)
        lost_enrichment = kept_ids - enrich_ids
        orphans = enrich_ids - kept_ids
        for p in kept_files:
            evicted_kept += _prune_jsonl(p, lost_enrichment)
        for p in enrich_files:
            orphan_enrichments += _prune_jsonl(p, orphans)

    return {"torn": torn, "evicted_kept": evicted_kept,
            "orphan_enrichments": orphan_enrichments}


def merge_kept_enrichments(
    kept_path: Path,
) -> "collections.OrderedDict[str, list]":
    """Merge a run's enrichments.kept.jsonl into per-doc_id phrase lists.

    Mirrors SIRA's apply step (add_doc_index_adapter.py: setdefault +
    extend, no dedup, insertion order) so the reconstructed per-run file
    is byte-equivalent to what SIRA would have written itself.
    """
    merged: "collections.OrderedDict[str, list]" = collections.OrderedDict()
    with open(kept_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            did = rec.get("doc_id")
            phrases = rec.get("phrases")
            if did is None or not phrases:
                continue
            merged.setdefault(did, []).extend(phrases)
    return merged


# ── verify-run (read-only diagnostics) ──────────────────────────────
#
# Paste-safe by design: reports carry counts, statuses, and filenames
# only — never doc_ids/req_ids or corpus text (collab protocol).

_BENIGN_FAILED_STATUSES = {"all_filtered", "no_phrases"}


def _count_torn_lines(path: Path) -> int:
    """Read-only counterpart of heal_torn_lines: count unparseable
    lines without rewriting anything."""
    if not path.exists():
        return 0
    torn = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                json.loads(stripped)
            except json.JSONDecodeError:
                torn += 1
    return torn


def _jsonl_rows(paths: list[Path]):
    """Yield parsed rows across JSONL files (torn/blank lines skipped)."""
    for p in paths:
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def _granularity(doc_id: str) -> str:
    """Corpus row granularity: doc: / section: prefixed ids are coarse
    rows; everything else is a per-req row."""
    head = doc_id.split(":", 1)[0]
    return head if head in ("doc", "section") else "req"


def batches_report(run_dir: Path) -> dict | None:
    """Aggregate batches*.jsonl (batched-enrich only). None when the run
    has no batch rows (legacy per-req path)."""
    rows = list(_jsonl_rows(sorted(run_dir.glob("batches*.jsonl"))))
    if not rows:
        return None
    status = collections.Counter(r.get("status", "?") for r in rows)
    ok_sizes = [r.get("n_reqs", 0) for r in rows if r.get("status") == "ok"]
    pe_sizes = [r.get("n_reqs", 0) for r in rows if r.get("status") == "parse_error"]
    final_round = max((r.get("attempt", 0) for r in rows), default=0)
    return {
        "n": len(rows),
        "status": dict(status),
        "closed_by": dict(collections.Counter(r.get("closed_by", "?") for r in rows)),
        "rounds": dict(sorted(collections.Counter(
            r.get("attempt", 0) for r in rows).items())),
        "oversized": sum(1 for r in rows if r.get("oversized")),
        "reqs_ok": {"min": min(ok_sizes), "avg": round(sum(ok_sizes) / len(ok_sizes), 1),
                    "max": max(ok_sizes)} if ok_sizes else None,
        "reqs_parse_error_avg": (round(sum(pe_sizes) / len(pe_sizes), 1)
                                 if pe_sizes else None),
        "answered": sum(r.get("answered", 0) for r in rows),
        "missing_final": sum(r.get("missing", 0) for r in rows
                             if r.get("attempt", 0) == final_round),
        "single_req_mode": all(r.get("n_reqs") == 1 for r in rows),
    }


def trace_report(run_dir: Path, corpus_ids: set[str] | None) -> dict:
    """Per-req outcome reconciliation over trace.kept*/trace.failed*."""
    kept_ids: list[str] = []
    for row in _jsonl_rows(sorted(run_dir.glob("trace.kept*.jsonl"))):
        if row.get("doc_id") is not None:
            kept_ids.append(row["doc_id"])
    failed_status: collections.Counter = collections.Counter()
    failed_ids: set[str] = set()
    for row in _jsonl_rows(sorted(run_dir.glob("trace.failed*.jsonl"))):
        if row.get("doc_id") is not None:
            failed_ids.add(row["doc_id"])
            failed_status[row.get("status", "?")] += 1
    kept_set = set(kept_ids)
    rep = {
        "kept": len(kept_ids),
        "kept_granularity": dict(collections.Counter(
            _granularity(d) for d in kept_ids)),
        "kept_duplicates": len(kept_ids) - len(kept_set),
        "failed": sum(failed_status.values()),
        "failed_status": dict(failed_status),
        "failed_non_benign": sum(n for s, n in failed_status.items()
                                 if s not in _BENIGN_FAILED_STATUSES),
        "kept_and_failed_overlap": len(kept_set & failed_ids),
    }
    if corpus_ids is not None:
        covered = kept_set | failed_ids
        rep["corpus"] = len(corpus_ids)
        rep["uncovered"] = len(corpus_ids - covered)
        rep["not_in_corpus"] = len(covered - corpus_ids)
    return rep


def invariant_report(run_dir: Path) -> dict:
    """Read-only version of heal_run_dir's checks: torn-line counts and
    the two-way kept↔enrichment invariant. All zeros on a healthy run."""
    torn = {p.name: n for p in sorted(run_dir.glob("*.jsonl"))
            if (n := _count_torn_lines(p))}
    kept_ids = _jsonl_doc_ids(sorted(run_dir.glob("trace.kept*.jsonl")))
    enrich_ids = _jsonl_doc_ids(sorted(run_dir.glob("enrichments.kept*.jsonl")))
    return {"torn": torn,
            "kept_without_enrichment": len(kept_ids - enrich_ids),
            "orphan_enrichments": len(enrich_ids - kept_ids)}


def verify_run(dataset_dir: Path, run_name: str) -> dict:
    """Assemble the full read-only report + verdict for one doc-enrich
    run. verdict: FAIL on structural-integrity breaks (torn lines,
    invariant, duplicate/overlapping trace rows), WARN on quality
    signals (parse errors, unanswered reqs, uncovered corpus rows,
    non-benign failures), PASS otherwise."""
    run_dir = dataset_dir / "runs" / "doc-enrich" / run_name
    corpus = dataset_dir / "raw" / "corpus.jsonl"
    corpus_ids = set(corpus_hashes(corpus)) if corpus.exists() else None

    batches = batches_report(run_dir)
    trace = trace_report(run_dir, corpus_ids)
    invariant = invariant_report(run_dir)

    fails: list[str] = []
    warns: list[str] = []
    if invariant["torn"]:
        fails.append(f"torn lines: {sum(invariant['torn'].values())}")
    if invariant["kept_without_enrichment"]:
        fails.append(f"kept-without-enrichment: "
                     f"{invariant['kept_without_enrichment']}")
    if invariant["orphan_enrichments"]:
        fails.append(f"orphan enrichments: {invariant['orphan_enrichments']}")
    if trace["kept_duplicates"]:
        fails.append(f"duplicate kept rows: {trace['kept_duplicates']}")
    if trace["kept_and_failed_overlap"]:
        fails.append(f"kept∩failed overlap: {trace['kept_and_failed_overlap']}")
    if batches:
        if batches["status"].get("parse_error"):
            warns.append(f"parse_error batches: {batches['status']['parse_error']}")
        if batches["missing_final"]:
            warns.append(f"missing at final round: {batches['missing_final']}")
    if trace["failed_non_benign"]:
        warns.append(f"non-benign failures: {trace['failed_non_benign']}")
    if trace.get("uncovered"):
        warns.append(f"corpus rows never attempted: {trace['uncovered']}")
    if trace.get("not_in_corpus"):
        warns.append(f"trace rows not in current corpus: "
                     f"{trace['not_in_corpus']} (stale corpus?)")

    verdict = "FAIL" if fails else ("WARN" if warns else "PASS")
    return {"batches": batches, "trace": trace, "invariant": invariant,
            "fails": fails, "warns": warns, "verdict": verdict}


def _merged_enrichments(run_dir: Path) -> dict[str, list]:
    """Per-doc merged phrase lists across enrichments.kept*.jsonl shards
    (same setdefault+extend semantics as merge_kept_enrichments)."""
    merged: dict[str, list] = {}
    for row in _jsonl_rows(sorted(run_dir.glob("enrichments.kept*.jsonl"))):
        did, phrases = row.get("doc_id"), row.get("phrases")
        if did is None or not phrases:
            continue
        merged.setdefault(did, []).extend(phrases)
    return merged


def compare_runs(run_dir_a: Path, run_dir_b: Path) -> dict:
    """Phrase-set agreement between two runs of the same cell (e.g.
    batch mode vs --max-reqs 1). Jaccard over per-doc phrase SETS;
    counts only — ids and phrases never leave this function."""
    a, b = _merged_enrichments(run_dir_a), _merged_enrichments(run_dir_b)
    common = sorted(set(a) & set(b))
    jaccards = []
    for did in common:
        sa, sb = set(a[did]), set(b[did])
        union = sa | sb
        jaccards.append(len(sa & sb) / len(union) if union else 1.0)
    buckets = {"=1.0": 0, ">=0.8": 0, ">=0.5": 0, "<0.5": 0}
    for j in jaccards:
        if j == 1.0:
            buckets["=1.0"] += 1
        elif j >= 0.8:
            buckets[">=0.8"] += 1
        elif j >= 0.5:
            buckets[">=0.5"] += 1
        else:
            buckets["<0.5"] += 1
    return {
        "docs_a": len(a), "docs_b": len(b), "common": len(common),
        "only_a": len(set(a) - set(b)), "only_b": len(set(b) - set(a)),
        "identical": buckets["=1.0"],
        "jaccard_mean": round(sum(jaccards) / len(jaccards), 3) if jaccards else None,
        "jaccard_min": round(min(jaccards), 3) if jaccards else None,
        "buckets": buckets,
    }


# ── CLI ─────────────────────────────────────────────────────────────


def _resolve_paths(
    dataset_dir: Path, run_name: str,
) -> tuple[Path, Path, Path, Path]:
    """Return (corpus_path, hash_store_path, meta_path, run_dir)."""
    corpus = dataset_dir / "raw" / "corpus.jsonl"
    hash_store = dataset_dir / _HASH_STORE_NAME
    meta = dataset_dir / _META_NAME
    run_dir = dataset_dir / "runs" / "doc-enrich" / run_name
    return corpus, hash_store, meta, run_dir


def _print_growth_banner(ratio: float, full_rebuild_size: int,
                         current_size: int, max_ratio: float) -> None:
    pct = (ratio - 1.0) * 100.0
    print()
    print("  " + "#" * 70)
    print("  # CORPUS DRIFT WARNING — consider a FULL rebuild")
    print("  #")
    print(f"  # Corpus has grown {pct:.0f}% since the last full rebuild")
    print(f"  #   last full rebuild: {full_rebuild_size} docs")
    print(f"  #   now:               {current_size} docs  (ratio {ratio:.2f}x)")
    print(f"  #   threshold:         {max_ratio:.2f}x")
    print("  #")
    print("  # SIRA's doc-enrichment + DF-filter depend on CORPUS-WIDE")
    print("  # document-frequency statistics. Cached enrichment was")
    print("  # DF-filtered against the smaller corpus; at this much growth")
    print("  # those kept-phrase decisions may be meaningfully stale.")
    print("  #")
    print("  # Recommended: re-run the adapter with --wipe-all-derived and")
    print("  # do a full re-enrich, then `commit --full` to re-baseline.")
    print("  # (Incremental will still WORK if you proceed — this is about")
    print("  #  retrieval quality, not a hard failure.)")
    print("  " + "#" * 70)


def cmd_prune(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dataset)
    corpus, hash_store_path, meta_path, run_dir = _resolve_paths(
        dataset_dir, args.run_name,
    )

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

    # Cumulative-drift assessment against the last FULL rebuild.
    meta = load_meta(meta_path)
    full_size = meta.get("full_rebuild_size")
    ratio, exceeded = growth_assessment(
        len(current), full_size, args.max_growth_ratio,
    )
    if full_size is None:
        print(f"\nNOTE: no full-rebuild baseline recorded ({meta_path.name} "
              f"missing). Run `commit --full` after your next full rebuild "
              f"so cumulative-drift warnings can fire.")
    elif exceeded:
        _print_growth_banner(ratio, full_size, len(current), args.max_growth_ratio)
        if args.strict_growth:
            print("\n--strict-growth set → exiting non-zero (2) so an "
                  "automated pipeline halts. Do a full rebuild, or re-run "
                  "prune without --strict-growth to proceed incrementally.")
            return 2
    else:
        print(f"\ncumulative growth since last full rebuild: {ratio:.2f}x "
              f"(under {args.max_growth_ratio:.2f}x threshold — incremental OK)")

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
    corpus, hash_store_path, meta_path, _ = _resolve_paths(
        dataset_dir, args.run_name,
    )
    if not corpus.exists():
        print(f"ERROR: corpus not found at {corpus}")
        return 1
    current = corpus_hashes(corpus)
    save_hash_store(hash_store_path, current)
    print(f"baseline updated: {len(current)} doc hashes → {hash_store_path}")

    if args.full:
        import datetime
        meta = load_meta(meta_path)
        meta["full_rebuild_size"] = len(current)
        meta["full_rebuild_date"] = datetime.date.today().isoformat()
        save_meta(meta_path, meta)
        print(f"FULL-rebuild baseline recorded: {len(current)} docs "
              f"→ {meta_path.name}")
        print("→ cumulative-drift warnings now measure growth from here.")
    else:
        print("→ next `prune` compares against this baseline. (Pass --full "
              "if this commit followed a full --wipe-all-derived rebuild, so "
              "the cumulative-drift baseline resets.)")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    """Reconstruct enrichments/doc/<run_name>.jsonl from the run's
    enrichments.kept.jsonl and (re)point best.jsonl at it.

    Needed for the full-resume case: when SIRA's enrich_corpus resumes a
    run where every doc is already enriched (enriched_count == 0), it
    skips the apply block (add_doc_index_adapter.py:363 `if ... and
    enriched_count > 0`). That block is what writes the per-run
    enrichments/doc/<run_name>.jsonl. The unconditional update_best still
    creates the best.jsonl symlink → <run_name>.jsonl, but the target is
    never written, leaving best.jsonl DANGLING. enrich_query then logs
    "No doc enrichments found, using base index" and retrieval runs on the
    bare index. This command writes the missing target so best.jsonl
    resolves and the cached enrichment is actually used.
    """
    dataset_dir = Path(args.dataset)
    _, _, _, run_dir = _resolve_paths(dataset_dir, args.run_name)
    kept = run_dir / "enrichments.kept.jsonl"
    if not kept.exists():
        print(f"ERROR: no enrichments.kept.jsonl at {kept}")
        return 1

    merged = merge_kept_enrichments(kept)
    if not merged:
        print(f"ERROR: {kept} held no usable enrichments — nothing to promote.")
        return 1

    enr_dir = dataset_dir / "enrichments" / "doc"
    enr_dir.mkdir(parents=True, exist_ok=True)
    run_jsonl = enr_dir / f"{args.run_name}.jsonl"
    total_phrases = 0
    with open(run_jsonl, "w", encoding="utf-8") as f:
        for did, phrases in merged.items():
            total_phrases += len(phrases)
            f.write(
                json.dumps({"doc_id": did, "phrases": phrases}, ensure_ascii=False)
                + "\n"
            )
    print(f"wrote {len(merged)} docs / {total_phrases} phrases → {run_jsonl}")

    # (Re)point best.jsonl at this run's file with a relative symlink,
    # matching SIRA's update_best convention.
    best = enr_dir / "best.jsonl"
    if best.is_symlink() or best.exists():
        best.unlink()
    os.symlink(f"{args.run_name}.jsonl", best)
    print(f"linked best.jsonl → {os.readlink(best)}")
    print(
        "\n→ enrich_query / the per-query service will now apply these "
        "enrichments\n  (best.jsonl resolves). Re-run the enrich_query + "
        "rerank stages, e.g.:\n    python scripts/run_pipeline.py data=nora "
        "enrich=nora rerank=nora \\\n        db_root=$(realpath ../adapter/out) "
        f"sglang.port=8030 +run_name={args.run_name} \\\n        "
        "stages='[enrich_query,rerank]'"
    )
    return 0


def cmd_retry_failed(args: argparse.Namespace) -> int:
    """Evict failed entries from one or both SIRA stage run dirs so the
    next pipeline run's resume reprocesses them."""
    dataset_dir = Path(args.dataset)
    if not (dataset_dir / "raw" / "corpus.jsonl").exists():
        print(f"ERROR: not a SIRA dataset dir (raw/corpus.jsonl missing): "
              f"{dataset_dir}")
        return 1

    stages: tuple[str, ...]
    if args.stage == "both":
        stages = ("doc-enrich", "rerank")
    else:
        stages = (args.stage,)

    scope = ("errors + all_filtered" if args.include_all_filtered
             else "errors only (all_filtered kept)")
    print(f"retry-failed: scope = {scope}")

    for stage in stages:
        run_dir = dataset_dir / "runs" / stage / args.run_name
        if not run_dir.is_dir():
            print(f"\n{stage}: no run dir at {run_dir} — skipping.")
            continue
        evicted, counts = retry_failed_in_run(
            run_dir, stage, include_all_filtered=args.include_all_filtered,
        )
        print(f"\n{stage} ({args.run_name}): evicted {evicted} key(s)")
        for name, dropped in counts.items():
            print(f"  {name}: dropped {dropped}")

    print(f"\n→ Re-run the pipeline with `+run_name={args.run_name}` and SIRA's "
          f"resume will pick the evicted entries.")
    return 0


def cmd_heal_torn(args: argparse.Namespace) -> int:
    """Post-interruption repair: torn-line drop + resume-invariant fix,
    per stage run dir. Resume the lane afterwards with the same run_name."""
    dataset_dir = Path(args.dataset)
    stages = ("doc-enrich", "rerank") if args.stage == "both" else (args.stage,)

    for stage in stages:
        run_dir = dataset_dir / "runs" / stage / args.run_name
        if not run_dir.is_dir():
            print(f"{stage}: no run dir at {run_dir} — skipping.")
            continue
        s = heal_run_dir(run_dir, stage)
        n_torn = sum(s["torn"].values())
        print(f"{stage} ({args.run_name}): {n_torn} torn line(s) dropped"
              + "".join(f"\n  {name}: {n}" for name, n in s["torn"].items()))
        if stage == "doc-enrich":
            print(f"  kept-without-enrichment evicted: {s['evicted_kept']}")
            print(f"  orphan enrichments dropped:      {s['orphan_enrichments']}")

    print(f"\n→ Resume with the SAME command / `+run_name={args.run_name}`; "
          f"repaired docs re-enrich, everything else skips.")
    return 0


def verify_cell(dataset_dir: Path, run_name: str,
                compare_run: str | None = None) -> str:
    """Print one cell's verify report; return its verdict string.

    The per-cell primitive behind `verify-run` — multi-cell sweeps live
    in the orchestrators (`sira_multi --verify`, `sira_lane --verify`),
    which loop cells and call this, mirroring how `sira_lane` drives
    `heal_run_dir` / `retry_failed_in_run` per cell."""
    run_dir = dataset_dir / "runs" / "doc-enrich" / run_name
    rep = verify_run(dataset_dir, run_name)
    print(f"verify-run: {dataset_dir.name}/runs/doc-enrich/{run_name}")

    b = rep["batches"]
    if b is None:
        print("[batches]   no batches*.jsonl — legacy per-req path "
              "(or run predates batched enrich)")
    else:
        mode = ("single-req (all batches carry 1 req)" if b["single_req_mode"]
                else "batched")
        print(f"[batches]   {b['n']} | status {b['status']} | "
              f"closed_by {b['closed_by']} | rounds {b['rounds']}")
        line = f"            mode: {mode} | oversized {b['oversized']}"
        if b["reqs_ok"]:
            r = b["reqs_ok"]
            line += (f" | reqs/batch ok: min {r['min']} avg {r['avg']} "
                     f"max {r['max']}")
        if b["reqs_parse_error_avg"] is not None:
            line += f" | reqs/batch parse_error avg: {b['reqs_parse_error_avg']}"
        print(line)
        print(f"            answered {b['answered']} | "
              f"missing@final-round {b['missing_final']}")

    t = rep["trace"]
    print(f"[trace]     kept {t['kept']} {t['kept_granularity']} | "
          f"failed {t['failed']} {t['failed_status']}")
    print(f"            duplicates {t['kept_duplicates']} | "
          f"kept∩failed {t['kept_and_failed_overlap']}")
    if "corpus" in t:
        print(f"[coverage]  corpus {t['corpus']} | uncovered {t['uncovered']} "
              f"| not-in-corpus {t['not_in_corpus']}")
    else:
        print("[coverage]  raw/corpus.jsonl missing — coverage skipped")

    i = rep["invariant"]
    print(f"[invariant] torn {sum(i['torn'].values())}"
          + "".join(f" ({name}: {n})" for name, n in i["torn"].items())
          + f" | kept-without-enrichment {i['kept_without_enrichment']}"
          + f" | orphan-enrichments {i['orphan_enrichments']}")

    if compare_run:
        other = dataset_dir / "runs" / "doc-enrich" / compare_run
        if not other.is_dir():
            print(f"[compare]   no run dir at {other} — skipped")
        else:
            c = compare_runs(run_dir, other)
            print(f"[compare]   vs {compare_run}: docs {c['docs_a']} "
                  f"| other {c['docs_b']} | common {c['common']} "
                  f"| only-this {c['only_a']} | only-other {c['only_b']}")
            print(f"            identical phrase-sets {c['identical']}"
                  f"/{c['common']} | jaccard mean {c['jaccard_mean']} "
                  f"min {c['jaccard_min']} | buckets {c['buckets']}")

    for f_ in rep["fails"]:
        print(f"FAIL: {f_}")
    for w in rep["warns"]:
        print(f"WARN: {w}")
    print(f"verdict: {rep['verdict']}")
    return rep["verdict"]


def cmd_verify_run(args: argparse.Namespace) -> int:
    """Read-only health report for ONE cell's doc-enrich run (batch and
    single-req modes), paste-safe. Multi-cell sweeps live at the
    orchestrator layer: `sira_multi --verify` / `sira_lane --verify`."""
    dataset_dir = Path(args.dataset)
    run_dir = dataset_dir / "runs" / "doc-enrich" / args.run_name
    if not run_dir.is_dir():
        print(f"ERROR: no run dir at {run_dir}")
        return 1
    verdict = verify_cell(dataset_dir, args.run_name,
                          compare_run=args.compare_run)
    return 1 if verdict == "FAIL" or (verdict == "WARN" and args.strict) else 0


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
    pr = sub.add_parser("promote", help="Reconstruct enrichments/doc/<run>.jsonl + best.jsonl from a full-resume run.")
    rf = sub.add_parser("retry-failed", help="Evict failed entries from a stage's resume trace so the next run reprocesses them.")
    ht = sub.add_parser("heal-torn", help="Post-interruption: drop torn JSONL lines and repair the kept/enrichment resume invariant.")
    vr = sub.add_parser("verify-run", help="Read-only health report for ONE cell's doc-enrich run (batch or single-req mode): batch stats, trace reconciliation, kept/enrichment invariant, optional cross-run phrase-set diff. Paste-safe (counts only). Multi-cell sweep: sira_multi --verify / sira_lane --verify.")
    for parser in (pp, pc, pr, rf, ht, vr):
        for a, kw in common_args:
            parser.add_argument(*a, **kw)

    pp.add_argument("--max-growth-ratio", type=float,
                    default=_DEFAULT_MAX_GROWTH_RATIO,
                    help=(
                        "Cumulative-growth ratio (current size ÷ size at "
                        "last full rebuild) above which prune prints a "
                        f"DRIFT WARNING. Default {_DEFAULT_MAX_GROWTH_RATIO}. "
                        "Corpus-wide DF stats drift as the corpus grows; "
                        "past this point a full rebuild is advisable."
                    ))
    pp.add_argument("--strict-growth", action="store_true",
                    help=(
                        "Make the drift warning a hard stop: exit code 2 "
                        "when cumulative growth exceeds --max-growth-ratio. "
                        "For automated pipelines that should halt and "
                        "require a deliberate full rebuild."
                    ))
    pc.add_argument("--full", action="store_true",
                    help=(
                        "Mark this commit as following a FULL rebuild "
                        "(--wipe-all-derived). Records the current corpus "
                        "size as the cumulative-drift baseline. Omit for "
                        "ordinary incremental commits."
                    ))

    rf.add_argument("--stage", choices=["doc-enrich", "rerank", "both"],
                    default="both",
                    help=(
                        "Which SIRA stage's failed entries to retry. Default "
                        "'both' processes doc-enrich + rerank in one command."
                    ))
    rf.add_argument("--include-all-filtered", action="store_true",
                    help=(
                        "Also evict rows with status='all_filtered'. Default "
                        "skips them — re-running them won't change the outcome "
                        "unless prompts or LLM changed. Set this flag after a "
                        "prompt-version bump or LLM swap so the new setup gets "
                        "a clean shot at those docs."
                    ))

    ht.add_argument("--stage", choices=["doc-enrich", "rerank", "both"],
                    default="both",
                    help=(
                        "Which SIRA stage's run dir to heal. Default 'both'. "
                        "The kept/enrichment invariant repair applies to "
                        "doc-enrich only; rerank gets the torn-line drop."
                    ))

    vr.add_argument("--compare-run", default=None, metavar="NAME",
                    help=(
                        "Second doc-enrich run name to diff phrase sets "
                        "against (e.g. a --max-reqs 1 rerun of the same "
                        "cell vs its batch-mode run). Reports per-doc "
                        "Jaccard agreement — counts only, no content."
                    ))
    vr.add_argument("--strict", action="store_true",
                    help="Exit 1 on WARN too (default: only FAIL exits 1).")

    args = p.parse_args(argv)
    return {
        "prune": cmd_prune,
        "commit": cmd_commit,
        "promote": cmd_promote,
        "retry-failed": cmd_retry_failed,
        "heal-torn": cmd_heal_torn,
        "verify-run": cmd_verify_run,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
