"""Tests for sandbox/sira_incremental.py — content-hash-aware
incremental enrichment helper (plan-aware-sira strand).

Run: python -m pytest sandbox/tests/test_sira_incremental.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sandbox.sira_incremental import (
    compute_evictions,
    corpus_hashes,
    load_hash_store,
    prune_run_files,
    save_hash_store,
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
