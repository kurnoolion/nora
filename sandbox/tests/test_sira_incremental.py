"""Tests for sandbox/sira_incremental.py — content-hash-aware
incremental enrichment helper (plan-aware-sira strand).

Run: python -m pytest sandbox/tests/test_sira_incremental.py
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from sandbox.sira_incremental import (
    cmd_promote,
    cmd_retry_failed,
    compute_evictions,
    corpus_hashes,
    growth_assessment,
    heal_run_dir,
    heal_torn_lines,
    load_hash_store,
    load_meta,
    main,
    merge_kept_enrichments,
    prune_run_files,
    retry_failed_in_run,
    save_hash_store,
    save_meta,
    _combined_text,
)


def _write_corpus(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _write_trace(path: Path, doc_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for did in doc_ids:
            f.write(json.dumps({"doc_id": did, "status": "ok", "kept": ["x"]}) + "\n")


# ── _combined_text mirrors SIRA's read_corpus_texts ─────────────────


def test_combined_text_with_title():
    assert _combined_text("Title", "body") == "Title. body"


def test_combined_text_empty_title():
    assert _combined_text("", "body") == "body"


def test_combined_text_whitespace_title():
    assert _combined_text("   ", "body") == "body"


def test_combined_text_none_safe():
    assert _combined_text(None, None) == ""


# ── corpus_hashes ───────────────────────────────────────────────────


def test_corpus_hashes_keys_by_id(tmp_path):
    corpus = tmp_path / "raw" / "corpus.jsonl"
    _write_corpus(corpus, [
        {"_id": "A", "title": "Alpha", "text": "aaa"},
        {"_id": "B", "title": "", "text": "bbb"},
    ])
    h = corpus_hashes(corpus)
    assert set(h) == {"A", "B"}
    # Same content → same hash; different content → different hash
    assert h["A"] != h["B"]


def test_corpus_hashes_changes_on_title_or_text(tmp_path):
    c1 = tmp_path / "c1.jsonl"
    c2 = tmp_path / "c2.jsonl"
    c3 = tmp_path / "c3.jsonl"
    _write_corpus(c1, [{"_id": "A", "title": "T", "text": "body"}])
    _write_corpus(c2, [{"_id": "A", "title": "T", "text": "body-EDIT"}])  # text change
    _write_corpus(c3, [{"_id": "A", "title": "T-EDIT", "text": "body"}])  # title change
    assert corpus_hashes(c1)["A"] != corpus_hashes(c2)["A"]
    assert corpus_hashes(c1)["A"] != corpus_hashes(c3)["A"]


# ── hash store round-trip ───────────────────────────────────────────


def test_hash_store_roundtrip(tmp_path):
    store = tmp_path / ".incremental_hashes.json"
    assert load_hash_store(store) == {}  # missing → empty
    save_hash_store(store, {"A": "h1", "B": "h2"})
    assert load_hash_store(store) == {"A": "h1", "B": "h2"}


def test_hash_store_corrupt_returns_empty(tmp_path):
    store = tmp_path / ".incremental_hashes.json"
    store.write_text("not json{", encoding="utf-8")
    assert load_hash_store(store) == {}


# ── compute_evictions ───────────────────────────────────────────────


def test_compute_evictions_partition():
    stored = {"A": "h_a", "B": "h_b", "C": "h_c"}
    current = {
        "A": "h_a",          # unchanged
        "B": "h_b_NEW",      # changed
        "D": "h_d",          # new
        "E": "h_e",          # new
        # C removed
    }
    changed, removed, new = compute_evictions(current, stored)
    assert changed == {"B"}
    assert removed == {"C"}
    assert new == {"D", "E"}


def test_compute_evictions_all_unchanged():
    h = {"A": "1", "B": "2"}
    changed, removed, new = compute_evictions(h, h)
    assert changed == set() and removed == set() and new == set()


def test_compute_evictions_fresh_run():
    current = {"A": "1", "B": "2"}
    changed, removed, new = compute_evictions(current, {})
    assert changed == set() and removed == set()
    assert new == {"A", "B"}  # everything is new on first run


# ── prune_run_files ─────────────────────────────────────────────────


def test_prune_evicts_changed_and_removed(tmp_path):
    run_dir = tmp_path / "runs" / "doc-enrich" / "enrich-stable"
    _write_trace(run_dir / "trace.kept.jsonl", ["A", "B", "C"])
    _write_trace(run_dir / "enrichments.kept.jsonl", ["A", "B", "C"])

    # Evict B (changed) + C (removed)
    counts = prune_run_files(run_dir, {"B", "C"})
    assert counts["trace.kept.jsonl"] == 2
    assert counts["enrichments.kept.jsonl"] == 2
    assert counts["trace.failed.jsonl"] == 0  # file absent → 0

    # Only A survives → SIRA resume skips A, re-enriches B (re-added) + new docs
    remaining = {
        json.loads(line)["doc_id"]
        for line in (run_dir / "trace.kept.jsonl").read_text().splitlines()
        if line.strip()
    }
    assert remaining == {"A"}


def test_prune_no_evictions_is_noop(tmp_path):
    run_dir = tmp_path / "run"
    _write_trace(run_dir / "trace.kept.jsonl", ["A", "B"])
    counts = prune_run_files(run_dir, set())
    assert counts["trace.kept.jsonl"] == 0
    remaining = {
        json.loads(line)["doc_id"]
        for line in (run_dir / "trace.kept.jsonl").read_text().splitlines()
        if line.strip()
    }
    assert remaining == {"A", "B"}


def test_prune_missing_run_dir_safe(tmp_path):
    counts = prune_run_files(tmp_path / "does-not-exist", {"A"})
    assert all(v == 0 for v in counts.values())


def test_prune_preserves_unparseable_lines(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    path = run_dir / "trace.kept.jsonl"
    path.write_text(
        json.dumps({"doc_id": "A"}) + "\n"
        + "garbage-not-json\n"
        + json.dumps({"doc_id": "B"}) + "\n",
        encoding="utf-8",
    )
    prune_run_files(run_dir, {"A"})
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    # A dropped; garbage kept (defensive); B kept
    assert "garbage-not-json" in lines
    assert any('"doc_id": "B"' in l for l in lines)
    assert not any('"doc_id": "A"' in l for l in lines)


# ── growth assessment (cumulative-drift detection) ─────────────────


def test_growth_assessment_no_baseline():
    ratio, exceeded = growth_assessment(1000, None, 1.5)
    assert ratio is None and exceeded is False


def test_growth_assessment_under_threshold():
    ratio, exceeded = growth_assessment(1200, 1000, 1.5)
    assert ratio == pytest.approx(1.2)
    assert exceeded is False


def test_growth_assessment_over_threshold():
    ratio, exceeded = growth_assessment(1600, 1000, 1.5)
    assert ratio == pytest.approx(1.6)
    assert exceeded is True


def test_growth_assessment_exactly_at_threshold_not_exceeded():
    # ratio == threshold is NOT "exceeded" (strict >)
    ratio, exceeded = growth_assessment(1500, 1000, 1.5)
    assert ratio == pytest.approx(1.5)
    assert exceeded is False


def test_meta_roundtrip(tmp_path):
    meta_path = tmp_path / ".incremental_meta.json"
    assert load_meta(meta_path) == {}
    save_meta(meta_path, {"full_rebuild_size": 13974, "full_rebuild_date": "2026-05-26"})
    loaded = load_meta(meta_path)
    assert loaded["full_rebuild_size"] == 13974


def test_cumulative_drift_across_many_small_ingests():
    """The motivating case: 10 incremental ingests of 10% each. Each
    individual ingest looks small (baseline moves forward every commit),
    but cumulative growth vs the last FULL rebuild is ~2.6x and SHOULD
    trip the drift warning."""
    full_rebuild_size = 1000
    size = full_rebuild_size
    tripped_at = None
    for i in range(1, 11):
        size = int(size * 1.1)  # +10% each round
        ratio, exceeded = growth_assessment(size, full_rebuild_size, 1.5)
        if exceeded and tripped_at is None:
            tripped_at = i
    # 1.1^N > 1.5 first at N=5 (1.1^5 ≈ 1.61)
    assert tripped_at == 5, tripped_at
    # Final cumulative ratio well past threshold
    assert size / full_rebuild_size > 2.5


# ── promote (full-resume best.jsonl reconstruction) ─────────────────


def test_merge_kept_enrichments_merges_per_doc_no_dedup(tmp_path):
    kept = tmp_path / "enrichments.kept.jsonl"
    kept.write_text(
        json.dumps({"doc_id": "A", "phrases": ["x", "y"]}) + "\n"
        + json.dumps({"doc_id": "B", "phrases": ["z"]}) + "\n"
        + json.dumps({"doc_id": "A", "phrases": ["x", "w"]}) + "\n"  # 2nd row, dup x
        + "\n"                                                        # blank line
        + "garbage-not-json\n",                                       # skipped
        encoding="utf-8",
    )
    merged = merge_kept_enrichments(kept)
    # insertion order preserved; A merged across rows; dups kept (matches SIRA)
    assert list(merged) == ["A", "B"]
    assert merged["A"] == ["x", "y", "x", "w"]
    assert merged["B"] == ["z"]


def test_merge_kept_skips_empty_and_missing_fields(tmp_path):
    kept = tmp_path / "enrichments.kept.jsonl"
    kept.write_text(
        json.dumps({"doc_id": "A", "phrases": []}) + "\n"       # empty phrases → skip
        + json.dumps({"doc_id": "B"}) + "\n"                    # no phrases → skip
        + json.dumps({"phrases": ["x"]}) + "\n"                 # no doc_id → skip
        + json.dumps({"doc_id": "C", "phrases": ["ok"]}) + "\n",
        encoding="utf-8",
    )
    merged = merge_kept_enrichments(kept)
    assert list(merged) == ["C"]


def test_cmd_promote_writes_run_jsonl_and_resolves_best(tmp_path):
    """The motivating bug: a full-resume run left best.jsonl dangling.
    promote writes the per-run target so best.jsonl resolves."""
    ds = tmp_path / "ds"
    run_dir = ds / "runs" / "doc-enrich" / "enrich-stable"
    run_dir.mkdir(parents=True)
    (run_dir / "enrichments.kept.jsonl").write_text(
        json.dumps({"doc_id": "A", "phrases": ["alpha"]}) + "\n"
        + json.dumps({"doc_id": "B", "phrases": ["beta", "b2"]}) + "\n",
        encoding="utf-8",
    )

    # Simulate SIRA's dangling best.jsonl symlink (target not yet written).
    enr_dir = ds / "enrichments" / "doc"
    enr_dir.mkdir(parents=True)
    dangling = enr_dir / "best.jsonl"
    os.symlink("enrich-stable.jsonl", dangling)
    assert not dangling.exists()  # dangling → exists() is False (the bug symptom)

    args = argparse.Namespace(dataset=str(ds), run_name="enrich-stable")
    assert cmd_promote(args) == 0

    # Per-run target now written, and best.jsonl resolves to it.
    run_jsonl = enr_dir / "enrich-stable.jsonl"
    assert run_jsonl.exists()
    best = enr_dir / "best.jsonl"
    assert best.exists()  # no longer dangling
    assert os.readlink(best) == "enrich-stable.jsonl"  # relative, matches SIRA
    rows = {
        json.loads(l)["doc_id"]: json.loads(l)["phrases"]
        for l in run_jsonl.read_text().splitlines() if l.strip()
    }
    assert rows == {"A": ["alpha"], "B": ["beta", "b2"]}


def test_cmd_promote_missing_kept_returns_error(tmp_path):
    ds = tmp_path / "ds"
    (ds / "runs" / "doc-enrich" / "enrich-stable").mkdir(parents=True)
    args = argparse.Namespace(dataset=str(ds), run_name="enrich-stable")
    assert cmd_promote(args) == 1


# ── retry-failed: doc-enrich (doc_id-keyed) ─────────────────────────


def _write_doc_enrich_failed(run_dir: Path, rows: list[dict]) -> None:
    """Seed a doc-enrich run's trace.failed.jsonl + trace.kept.jsonl."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "trace.failed.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    # Seed kept too with some unrelated docs that should not be touched
    (run_dir / "trace.kept.jsonl").write_text(
        json.dumps({"doc_id": "KEEP-1"}) + "\n"
        + json.dumps({"doc_id": "KEEP-2"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "enrichments.kept.jsonl").write_text(
        json.dumps({"doc_id": "KEEP-1", "phrases": ["x"]}) + "\n",
        encoding="utf-8",
    )


def test_retry_doc_enrich_default_skips_all_filtered(tmp_path):
    run_dir = tmp_path / "ds" / "runs" / "doc-enrich" / "stable"
    _write_doc_enrich_failed(run_dir, [
        {"doc_id": "FAIL-1", "status": "error"},
        {"doc_id": "FAIL-2", "status": "error"},
        {"doc_id": "AF-1",   "status": "all_filtered"},
        {"doc_id": "AF-2",   "status": "all_filtered"},
    ])
    n, counts = retry_failed_in_run(run_dir, "doc-enrich")
    assert n == 2  # only the two errors
    assert counts["trace.failed.jsonl"] == 2
    # KEEP-1/KEEP-2 stay untouched (they weren't in the eviction set)
    assert counts["trace.kept.jsonl"] == 0
    assert counts["enrichments.kept.jsonl"] == 0
    remaining_failed = {
        json.loads(l)["doc_id"]
        for l in (run_dir / "trace.failed.jsonl").read_text().splitlines()
        if l.strip()
    }
    assert remaining_failed == {"AF-1", "AF-2"}, (
        "all_filtered rows must stay in failed trace by default"
    )


def test_retry_doc_enrich_include_all_filtered_evicts_everything(tmp_path):
    run_dir = tmp_path / "ds" / "runs" / "doc-enrich" / "stable"
    _write_doc_enrich_failed(run_dir, [
        {"doc_id": "FAIL-1", "status": "error"},
        {"doc_id": "AF-1",   "status": "all_filtered"},
    ])
    n, counts = retry_failed_in_run(
        run_dir, "doc-enrich", include_all_filtered=True,
    )
    assert n == 2
    assert counts["trace.failed.jsonl"] == 2


def test_retry_doc_enrich_also_evicts_if_present_in_kept_or_enrichments(tmp_path):
    """If a failed doc_id somehow leaked into kept/enrichments (unlikely
    but defensive), the eviction should clean it up across all three files."""
    run_dir = tmp_path / "ds" / "runs" / "doc-enrich" / "stable"
    _write_doc_enrich_failed(run_dir, [
        {"doc_id": "FAIL-1", "status": "error"},
    ])
    # Inject FAIL-1 into kept + enrichments as well
    with open(run_dir / "trace.kept.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"doc_id": "FAIL-1"}) + "\n")
    with open(run_dir / "enrichments.kept.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"doc_id": "FAIL-1", "phrases": ["leaked"]}) + "\n")

    _, counts = retry_failed_in_run(run_dir, "doc-enrich")
    assert counts["trace.failed.jsonl"] == 1
    assert counts["trace.kept.jsonl"] == 1
    assert counts["enrichments.kept.jsonl"] == 1


def test_retry_doc_enrich_missing_failed_file_returns_zero(tmp_path):
    run_dir = tmp_path / "ds" / "runs" / "doc-enrich" / "stable"
    run_dir.mkdir(parents=True)
    n, counts = retry_failed_in_run(run_dir, "doc-enrich")
    assert n == 0
    assert all(v == 0 for v in counts.values())


# ── retry-failed: rerank (pair-keyed: query_id, doc_id) ─────────────


def _write_rerank_failed(run_dir: Path, rows: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "trace.failed.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_retry_rerank_keys_on_pair_not_doc_id(tmp_path):
    """Rerank's resume keys on (query_id, doc_id). Two failed rows with
    the SAME doc_id but DIFFERENT query_ids must both be evicted; a kept
    row with the same doc_id but a third query_id must stay."""
    run_dir = tmp_path / "ds" / "runs" / "rerank" / "stable"
    _write_rerank_failed(run_dir, [
        {"query_id": "Q1", "doc_id": "DOC-X", "status": "error"},
        {"query_id": "Q2", "doc_id": "DOC-X", "status": "error"},
    ])
    # Seed kept with (Q3, DOC-X) which shares doc_id but is a different pair
    (run_dir / "trace.kept.jsonl").write_text(
        json.dumps({"query_id": "Q3", "doc_id": "DOC-X", "score": 60}) + "\n"
        + json.dumps({"query_id": "Q1", "doc_id": "DOC-Y", "score": 80}) + "\n",
        encoding="utf-8",
    )
    n, counts = retry_failed_in_run(run_dir, "rerank")
    assert n == 2
    assert counts["trace.failed.jsonl"] == 2
    assert counts["trace.kept.jsonl"] == 0  # neither kept row matched the failed pairs

    remaining_kept = [
        json.loads(l) for l in (run_dir / "trace.kept.jsonl").read_text().splitlines()
        if l.strip()
    ]
    pairs_kept = {(r["query_id"], r["doc_id"]) for r in remaining_kept}
    assert pairs_kept == {("Q3", "DOC-X"), ("Q1", "DOC-Y")}


def test_retry_rerank_no_enrichments_file_expected(tmp_path):
    """Rerank stage files are trace.kept + trace.failed only — no
    enrichments.kept.jsonl. The counts dict should not include it."""
    run_dir = tmp_path / "ds" / "runs" / "rerank" / "stable"
    _write_rerank_failed(run_dir, [
        {"query_id": "Q1", "doc_id": "DOC-1", "status": "error"},
    ])
    _, counts = retry_failed_in_run(run_dir, "rerank")
    assert set(counts) == {"trace.kept.jsonl", "trace.failed.jsonl"}


def test_retry_rerank_skips_row_with_missing_query_id(tmp_path):
    """Defensive: a rerank failed row missing query_id has no valid resume
    key — it should be ignored (not evicted), not crash."""
    run_dir = tmp_path / "ds" / "runs" / "rerank" / "stable"
    _write_rerank_failed(run_dir, [
        {"doc_id": "DOC-1", "status": "error"},          # missing query_id
        {"query_id": "Q1", "doc_id": "DOC-2", "status": "error"},
    ])
    n, _ = retry_failed_in_run(run_dir, "rerank")
    assert n == 1  # only the well-formed pair counted


# ── retry-failed: stage routing + CLI ───────────────────────────────


def test_retry_unknown_stage_raises():
    with pytest.raises(ValueError, match="unknown stage"):
        retry_failed_in_run(Path("/tmp/nowhere"), "bogus")


def test_cmd_retry_failed_both_stages(tmp_path, capsys):
    """End-to-end via the CLI handler: both stages, default scope."""
    ds = tmp_path / "ds"
    (ds / "raw").mkdir(parents=True)
    (ds / "raw" / "corpus.jsonl").write_text("", encoding="utf-8")  # marker
    _write_doc_enrich_failed(
        ds / "runs" / "doc-enrich" / "qwen3", [
            {"doc_id": "FAIL-A", "status": "error"},
            {"doc_id": "AF-A",   "status": "all_filtered"},
        ],
    )
    _write_rerank_failed(
        ds / "runs" / "rerank" / "qwen3", [
            {"query_id": "Q1", "doc_id": "DOC-1", "status": "error"},
            {"query_id": "Q2", "doc_id": "DOC-2", "status": "error"},
        ],
    )

    args = argparse.Namespace(
        dataset=str(ds), run_name="qwen3",
        stage="both", include_all_filtered=False,
    )
    rc = cmd_retry_failed(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "doc-enrich" in out and "rerank" in out
    # doc-enrich evicts 1 (FAIL-A error; AF-A all_filtered is kept by default).
    # rerank evicts 2 (both errors; no all_filtered concept for rerank).
    assert "doc-enrich (qwen3): evicted 1 key" in out
    assert "rerank (qwen3): evicted 2 key" in out

    # doc-enrich: only FAIL-A evicted from failed; AF-A still there.
    de_remaining = {
        json.loads(l)["doc_id"]
        for l in (ds / "runs" / "doc-enrich" / "qwen3" / "trace.failed.jsonl"
                  ).read_text().splitlines() if l.strip()
    }
    assert de_remaining == {"AF-A"}
    # rerank: both error pairs evicted.
    rr_remaining = [
        l for l in (ds / "runs" / "rerank" / "qwen3" / "trace.failed.jsonl"
                    ).read_text().splitlines() if l.strip()
    ]
    assert rr_remaining == []


def test_cmd_retry_failed_missing_run_dir_skips_gracefully(tmp_path, capsys):
    ds = tmp_path / "ds"
    (ds / "raw").mkdir(parents=True)
    (ds / "raw" / "corpus.jsonl").write_text("", encoding="utf-8")
    # No runs/doc-enrich/qwen3 or runs/rerank/qwen3 — both stages absent
    args = argparse.Namespace(
        dataset=str(ds), run_name="qwen3",
        stage="both", include_all_filtered=False,
    )
    assert cmd_retry_failed(args) == 0
    out = capsys.readouterr().out
    assert "no run dir" in out


def test_cmd_retry_failed_not_a_dataset_dir_errors(tmp_path, capsys):
    args = argparse.Namespace(
        dataset=str(tmp_path / "no-such"), run_name="x",
        stage="both", include_all_filtered=False,
    )
    assert cmd_retry_failed(args) == 1


# ── end-to-end: a 10x-ish growth cycle ──────────────────────────────


def test_full_incremental_cycle(tmp_path):
    """Simulate: baseline of 3 docs → change 1, remove 1, add many.
    Verify only changed+new reach the LLM (i.e., stay out of the
    post-prune trace) and unchanged stays skipped."""
    ds = tmp_path / "ds"
    corpus = ds / "raw" / "corpus.jsonl"
    store = ds / ".incremental_hashes.json"
    run_dir = ds / "runs" / "doc-enrich" / "enrich-stable"

    # Baseline corpus + trace (all enriched)
    _write_corpus(corpus, [
        {"_id": "A", "title": "Alpha", "text": "aaa"},
        {"_id": "B", "title": "Beta", "text": "bbb"},
        {"_id": "C", "title": "Gamma", "text": "ccc"},
    ])
    _write_trace(run_dir / "trace.kept.jsonl", ["A", "B", "C"])
    _write_trace(run_dir / "enrichments.kept.jsonl", ["A", "B", "C"])
    save_hash_store(store, corpus_hashes(corpus))

    # Growth: A unchanged, B changed, C removed, D..M added (10 new)
    new_rows = [
        {"_id": "A", "title": "Alpha", "text": "aaa"},
        {"_id": "B", "title": "Beta", "text": "bbb-EDITED"},
    ] + [{"_id": f"N{i}", "title": f"New{i}", "text": f"n{i}"} for i in range(10)]
    _write_corpus(corpus, new_rows)

    current = corpus_hashes(corpus)
    stored = load_hash_store(store)
    changed, removed, new = compute_evictions(current, stored)
    assert changed == {"B"}
    assert removed == {"C"}
    assert len(new) == 10

    prune_run_files(run_dir, changed | removed)

    # Post-prune trace = unchanged docs only (A). SIRA resume will skip
    # A (no LLM), and enrich B (changed, evicted) + N0..N9 (new). That's
    # 11 LLM calls for a 12-doc corpus that grew 4x — NOT a full rebuild.
    remaining = {
        json.loads(line)["doc_id"]
        for line in (run_dir / "trace.kept.jsonl").read_text().splitlines()
        if line.strip()
    }
    assert remaining == {"A"}

    # Commit new baseline
    save_hash_store(store, current)
    assert set(load_hash_store(store)) == {"A", "B"} | {f"N{i}" for i in range(10)}


# ── heal-torn: post-interruption run-dir repair ─────────────────────


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(l + "\n" for l in lines), encoding="utf-8")


def test_heal_torn_lines_drops_only_unparseable(tmp_path):
    p = tmp_path / "trace.kept.jsonl"
    _write_lines(p, [
        json.dumps({"doc_id": "A", "status": "ok"}),
        json.dumps({"doc_id": "B", "status": "ok"}),
        '{"doc_id": "C", "sta',  # torn tail
    ])
    kept, dropped = heal_torn_lines(p)
    assert (kept, dropped) == (2, 1)
    remaining = [json.loads(l)["doc_id"]
                 for l in p.read_text().splitlines() if l.strip()]
    assert remaining == ["A", "B"]


def test_heal_torn_lines_healthy_file_untouched(tmp_path):
    p = tmp_path / "trace.kept.jsonl"
    _write_lines(p, [json.dumps({"doc_id": "A"})])
    before = p.read_text()
    kept, dropped = heal_torn_lines(p)
    assert (kept, dropped) == (1, 0)
    assert p.read_text() == before


def test_heal_torn_lines_missing_file_noop(tmp_path):
    assert heal_torn_lines(tmp_path / "absent.jsonl") == (0, 0)


def test_heal_run_dir_evicts_kept_without_enrichment(tmp_path):
    """Interruption case: trace.kept row survived, its enrichment row was
    lost with the buffer. The doc must re-enrich — evict it from kept."""
    rd = tmp_path / "runs" / "doc-enrich" / "r1"
    _write_lines(rd / "trace.kept.jsonl", [
        json.dumps({"doc_id": "A", "status": "ok"}),
        json.dumps({"doc_id": "B", "status": "ok"}),  # enrichment lost
    ])
    _write_lines(rd / "enrichments.kept.jsonl", [
        json.dumps({"doc_id": "A", "phrases": ["x"]}),
    ])
    s = heal_run_dir(rd)
    assert s["evicted_kept"] == 1
    remaining = {json.loads(l)["doc_id"] for l in
                 (rd / "trace.kept.jsonl").read_text().splitlines() if l.strip()}
    assert remaining == {"A"}


def test_heal_run_dir_drops_orphan_enrichment(tmp_path):
    """Reverse case: enrichment row survived, trace.kept row lost. The doc
    re-enriches — drop the orphan so phrases don't merge twice."""
    rd = tmp_path / "runs" / "doc-enrich" / "r1"
    _write_lines(rd / "trace.kept.jsonl", [
        json.dumps({"doc_id": "A", "status": "ok"}),
    ])
    _write_lines(rd / "enrichments.kept.jsonl", [
        json.dumps({"doc_id": "A", "phrases": ["x"]}),
        json.dumps({"doc_id": "B", "phrases": ["y"]}),  # kept row lost
    ])
    s = heal_run_dir(rd)
    assert s["orphan_enrichments"] == 1
    remaining = {json.loads(l)["doc_id"] for l in
                 (rd / "enrichments.kept.jsonl").read_text().splitlines() if l.strip()}
    assert remaining == {"A"}


def test_heal_run_dir_torn_then_invariant(tmp_path):
    """A torn enrichment line IS the lost enrichment: after the torn drop,
    its (complete) kept row must be evicted too."""
    rd = tmp_path / "runs" / "doc-enrich" / "r1"
    _write_lines(rd / "trace.kept.jsonl", [
        json.dumps({"doc_id": "A", "status": "ok"}),
        json.dumps({"doc_id": "B", "status": "ok"}),
    ])
    _write_lines(rd / "enrichments.kept.jsonl", [
        json.dumps({"doc_id": "A", "phrases": ["x"]}),
        '{"doc_id": "B", "phras',  # torn
    ])
    s = heal_run_dir(rd)
    assert s["torn"] == {"enrichments.kept.jsonl": 1}
    assert s["evicted_kept"] == 1
    remaining = {json.loads(l)["doc_id"] for l in
                 (rd / "trace.kept.jsonl").read_text().splitlines() if l.strip()}
    assert remaining == {"A"}


def test_heal_run_dir_failed_rows_not_touched_by_invariant(tmp_path):
    """trace.failed rows have no enrichment by design — the invariant
    repair must not evict them (they'd lose their retry-failed history)."""
    rd = tmp_path / "runs" / "doc-enrich" / "r1"
    _write_lines(rd / "trace.kept.jsonl", [
        json.dumps({"doc_id": "A", "status": "ok"}),
    ])
    _write_lines(rd / "trace.failed.jsonl", [
        json.dumps({"doc_id": "F", "status": "batch_error"}),
    ])
    _write_lines(rd / "enrichments.kept.jsonl", [
        json.dumps({"doc_id": "A", "phrases": ["x"]}),
    ])
    s = heal_run_dir(rd)
    assert s == {"torn": {}, "evicted_kept": 0, "orphan_enrichments": 0}
    assert "F" in (rd / "trace.failed.jsonl").read_text()


def test_heal_run_dir_shard_files_cross_checked(tmp_path):
    """Shard-suffixed files participate: torn drop and the invariant run
    over the union of shards, evicting from whichever file holds the row."""
    rd = tmp_path / "runs" / "doc-enrich" / "r1"
    _write_lines(rd / "trace.kept.shard0.jsonl", [
        json.dumps({"doc_id": "A", "status": "ok"}),
    ])
    _write_lines(rd / "trace.kept.shard1.jsonl", [
        json.dumps({"doc_id": "B", "status": "ok"}),  # enrichment lost
    ])
    _write_lines(rd / "enrichments.kept.shard0.jsonl", [
        json.dumps({"doc_id": "A", "phrases": ["x"]}),
    ])
    _write_lines(rd / "enrichments.kept.shard1.jsonl", [
        '{"doc',  # torn
    ])
    s = heal_run_dir(rd)
    assert s["torn"] == {"enrichments.kept.shard1.jsonl": 1}
    assert s["evicted_kept"] == 1
    assert (rd / "trace.kept.shard1.jsonl").read_text() == ""


def test_heal_run_dir_rerank_torn_only(tmp_path):
    """rerank has no enrichments file — heal drops torn lines but runs no
    kept/enrichment cross-check."""
    rd = tmp_path / "runs" / "rerank" / "r1"
    _write_lines(rd / "trace.kept.jsonl", [
        json.dumps({"query_id": "Q1", "doc_id": "A"}),
        '{"query_id": "Q2", "doc',  # torn
    ])
    s = heal_run_dir(rd, stage="rerank")
    assert s["torn"] == {"trace.kept.jsonl": 1}
    assert s["evicted_kept"] == 0 and s["orphan_enrichments"] == 0


def test_cmd_heal_torn_via_main(tmp_path, capsys):
    """End-to-end via the CLI: default --stage both, missing rerank dir
    skips gracefully, doc-enrich gets repaired."""
    ds = tmp_path / "ds"
    rd = ds / "runs" / "doc-enrich" / "r1"
    _write_lines(rd / "trace.kept.jsonl", [
        json.dumps({"doc_id": "A", "status": "ok"}),
        '{"doc_id": "B"',  # torn
    ])
    _write_lines(rd / "enrichments.kept.jsonl", [
        json.dumps({"doc_id": "A", "phrases": ["x"]}),
    ])
    rc = main(["heal-torn", "--dataset", str(ds), "--run-name", "r1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "doc-enrich (r1): 1 torn line(s) dropped" in out
    assert "rerank: no run dir" in out


# ── verify-run: read-only run diagnostics ───────────────────────────


from sandbox.sira_incremental import (  # noqa: E402
    batches_report,
    compare_runs,
    trace_report,
    verify_run,
)


def _batch_row(**kw) -> str:
    row = {"batch_id": "b0", "plan": "PA", "n_reqs": 2, "status": "ok",
           "answered": 2, "missing": 0, "prompt_tokens_est": 100,
           "resp_tokens_est": 180, "closed_by": "end", "oversized": False,
           "attempt": 0, "ms": 5}
    row.update(kw)
    return json.dumps(row)


def _seed_verified_run(tmp_path, run="r1"):
    """Healthy 3-row cell: 2 req rows + 1 coarse doc row, all kept."""
    ds = tmp_path / "MNOA__Feb2026"
    rd = ds / "runs" / "doc-enrich" / run
    _write_corpus(ds / "raw" / "corpus.jsonl", [
        {"_id": "R1", "title": "t", "text": "one"},
        {"_id": "R2", "title": "t", "text": "two"},
        {"_id": "doc:PA", "title": "", "text": "plan"},
    ])
    _write_lines(rd / "trace.kept.jsonl", [
        json.dumps({"doc_id": d, "status": "ok", "batch_id": "b0"})
        for d in ("R1", "R2", "doc:PA")
    ])
    _write_lines(rd / "enrichments.kept.jsonl", [
        json.dumps({"doc_id": "R1", "phrases": ["alpha", "beta"]}),
        json.dumps({"doc_id": "R2", "phrases": ["gamma"]}),
        json.dumps({"doc_id": "doc:PA", "phrases": ["delta"]}),
    ])
    _write_lines(rd / "batches.jsonl", [
        _batch_row(n_reqs=2, answered=2),
        _batch_row(batch_id="b1", n_reqs=1, answered=1, closed_by="response"),
    ])
    return ds, rd


def test_verify_run_healthy_passes(tmp_path):
    ds, _ = _seed_verified_run(tmp_path)
    rep = verify_run(ds, "r1")
    assert rep["verdict"] == "PASS" and not rep["fails"] and not rep["warns"]
    assert rep["batches"]["n"] == 2 and rep["batches"]["status"] == {"ok": 2}
    assert rep["batches"]["single_req_mode"] is False
    assert rep["trace"]["kept"] == 3
    assert rep["trace"]["kept_granularity"] == {"req": 2, "doc": 1}
    assert rep["trace"]["uncovered"] == 0 and rep["trace"]["not_in_corpus"] == 0


def test_verify_run_detects_single_req_mode(tmp_path):
    ds, rd = _seed_verified_run(tmp_path)
    _write_lines(rd / "batches.jsonl", [
        _batch_row(n_reqs=1, answered=1),
        _batch_row(batch_id="b1", n_reqs=1, answered=1),
    ])
    assert verify_run(ds, "r1")["batches"]["single_req_mode"] is True


def test_verify_run_no_batches_file_reports_legacy(tmp_path):
    ds, rd = _seed_verified_run(tmp_path)
    (rd / "batches.jsonl").unlink()
    rep = verify_run(ds, "r1")
    assert rep["batches"] is None and rep["verdict"] == "PASS"


def test_verify_run_broken_invariant_fails_and_is_readonly(tmp_path):
    ds, rd = _seed_verified_run(tmp_path)
    # R2's enrichment lost + a torn kept line
    _write_lines(rd / "enrichments.kept.jsonl", [
        json.dumps({"doc_id": "R1", "phrases": ["alpha"]}),
        json.dumps({"doc_id": "doc:PA", "phrases": ["delta"]}),
    ])
    with open(rd / "trace.kept.jsonl", "a", encoding="utf-8") as f:
        f.write('{"doc_id": "torn')
    before = {p.name: p.read_text() for p in rd.glob("*.jsonl")}
    rep = verify_run(ds, "r1")
    assert rep["verdict"] == "FAIL"
    assert rep["invariant"]["kept_without_enrichment"] == 1
    assert rep["invariant"]["torn"] == {"trace.kept.jsonl": 1}
    # read-only: nothing healed/rewritten
    assert {p.name: p.read_text() for p in rd.glob("*.jsonl")} == before


def test_verify_run_quality_signals_warn(tmp_path):
    ds, rd = _seed_verified_run(tmp_path)
    _write_lines(rd / "batches.jsonl", [
        _batch_row(n_reqs=2, answered=2),
        _batch_row(batch_id="b1", status="parse_error", answered=0,
                   missing=1, attempt=2),
    ])
    # R2 actually failed (benign + non-benign rows for the histogram)
    _write_lines(rd / "trace.kept.jsonl", [
        json.dumps({"doc_id": d, "status": "ok"}) for d in ("R1", "doc:PA")
    ])
    _write_lines(rd / "enrichments.kept.jsonl", [
        json.dumps({"doc_id": "R1", "phrases": ["alpha"]}),
        json.dumps({"doc_id": "doc:PA", "phrases": ["delta"]}),
    ])
    _write_lines(rd / "trace.failed.jsonl", [
        json.dumps({"doc_id": "R2", "status": "missing_in_batch_response"}),
    ])
    rep = verify_run(ds, "r1")
    assert rep["verdict"] == "WARN" and not rep["fails"]
    assert any("parse_error" in w for w in rep["warns"])
    assert any("missing at final round" in w for w in rep["warns"])
    assert rep["trace"]["failed_non_benign"] == 1


def test_verify_run_duplicate_and_overlap_fail(tmp_path):
    ds, rd = _seed_verified_run(tmp_path)
    with open(rd / "trace.kept.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"doc_id": "R1", "status": "ok"}) + "\n")
    _write_lines(rd / "trace.failed.jsonl", [
        json.dumps({"doc_id": "R2", "status": "http_500"}),
    ])
    rep = verify_run(ds, "r1")
    assert rep["verdict"] == "FAIL"
    assert rep["trace"]["kept_duplicates"] == 1
    assert rep["trace"]["kept_and_failed_overlap"] == 1


def test_compare_runs_identical_and_divergent(tmp_path):
    ds, rd1 = _seed_verified_run(tmp_path, run="r1")
    _, rd2 = _seed_verified_run(tmp_path / "b", run="r2")
    # r2: R1 identical, R2 disjoint, doc:PA half-overlap, R3 only in r2
    _write_lines(rd2 / "enrichments.kept.jsonl", [
        json.dumps({"doc_id": "R1", "phrases": ["alpha", "beta"]}),
        json.dumps({"doc_id": "R2", "phrases": ["other"]}),
        json.dumps({"doc_id": "doc:PA", "phrases": ["delta", "extra"]}),
        json.dumps({"doc_id": "R3", "phrases": ["new"]}),
    ])
    c = compare_runs(rd1, rd2)
    assert c["docs_a"] == 3 and c["docs_b"] == 4
    assert c["common"] == 3 and c["only_a"] == 0 and c["only_b"] == 1
    assert c["identical"] == 1 and c["jaccard_min"] == 0.0
    assert c["buckets"] == {"=1.0": 1, ">=0.8": 0, ">=0.5": 1, "<0.5": 1}


def test_cmd_verify_run_via_main(tmp_path, capsys):
    ds, _ = _seed_verified_run(tmp_path)
    assert main(["verify-run", "--dataset", str(ds), "--run-name", "r1"]) == 0
    out = capsys.readouterr().out
    assert "verdict: PASS" in out and "[invariant] torn 0" in out


def test_cmd_verify_run_fail_exit_and_strict(tmp_path, capsys):
    ds, rd = _seed_verified_run(tmp_path)
    # WARN-only run: benign structural state, one parse_error batch
    _write_lines(rd / "batches.jsonl", [_batch_row(status="parse_error")])
    assert main(["verify-run", "--dataset", str(ds), "--run-name", "r1"]) == 0
    assert main(["verify-run", "--dataset", str(ds), "--run-name", "r1",
                 "--strict"]) == 1
    capsys.readouterr()
    assert main(["verify-run", "--dataset", str(ds), "--run-name",
                 "missing"]) == 1
    assert "no run dir" in capsys.readouterr().out


def test_cmd_verify_run_compare_via_main(tmp_path, capsys):
    ds, _ = _seed_verified_run(tmp_path)
    rd2 = ds / "runs" / "doc-enrich" / "r2"
    _write_lines(rd2 / "enrichments.kept.jsonl", [
        json.dumps({"doc_id": "R1", "phrases": ["alpha", "beta"]}),
        json.dumps({"doc_id": "R2", "phrases": ["gamma"]}),
        json.dumps({"doc_id": "doc:PA", "phrases": ["delta"]}),
    ])
    rc = main(["verify-run", "--dataset", str(ds), "--run-name", "r1",
               "--compare-run", "r2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "identical phrase-sets 3/3" in out and "jaccard mean 1.0" in out
