"""verify_run's parse-error verdict: WARN only when a parse-error batch
left a request UNRESOLVED; transient events (all requests later kept or
failed) are notes, not warns (field-found: two transient batches kept a
WARN alive and sent the investigation after a request that was kept all
along)."""

from __future__ import annotations

import json
from pathlib import Path

from sandbox.sira_incremental import verify_run


def _mk_run(tmp_path: Path, *, kept, failed, batches, corpus_ids):
    ds = tmp_path / "cell"
    run = ds / "runs" / "doc-enrich" / "r1"
    run.mkdir(parents=True)
    (ds / "raw").mkdir(parents=True)
    with open(ds / "raw" / "corpus.jsonl", "w", encoding="utf-8") as f:
        for rid in corpus_ids:
            f.write(json.dumps({"_id": rid, "title": "t", "text": "x"}) + "\n")
    with open(run / "trace.kept.jsonl", "w", encoding="utf-8") as f:
        for rid in kept:
            f.write(json.dumps({"doc_id": rid, "status": "ok",
                                "batch_id": "t.b00000"}) + "\n")
    # invariant: every kept row needs its enrichment row
    with open(run / "enrichments.kept.jsonl", "w", encoding="utf-8") as f:
        for rid in kept:
            f.write(json.dumps({"doc_id": rid, "phrases": ["p"]}) + "\n")
    with open(run / "trace.failed.jsonl", "w", encoding="utf-8") as f:
        for rid in failed:
            f.write(json.dumps({"doc_id": rid, "status": "all_filtered",
                                "batch_id": "t.b00000"}) + "\n")
    with open(run / "batches.jsonl", "w", encoding="utf-8") as f:
        for row in batches:
            base = {"batch_id": "t.b00000", "plan": "PA", "n_reqs": 1,
                    "attempt": 0, "status": "ok"}
            base.update(row)
            f.write(json.dumps(base) + "\n")
    return ds


def test_transient_parse_error_is_note_not_warn(tmp_path):
    ds = _mk_run(tmp_path, kept=["R1", "R2"], failed=[],
                 corpus_ids=["R1", "R2"],
                 batches=[{"status": "parse_error", "req_ids": ["R2"],
                           "batch_id": "t.b00000"},
                          {"status": "ok", "req_ids": ["R1", "R2"],
                           "batch_id": "t.b00001", "attempt": 1}])
    rep = verify_run(ds, "r1")
    assert rep["verdict"] == "PASS", (rep["warns"], rep["fails"])
    assert any("transient" in n for n in rep["notes"])


def test_unresolved_parse_error_warns(tmp_path):
    ds = _mk_run(tmp_path, kept=["R1"], failed=[],
                 corpus_ids=["R1", "R2"],
                 batches=[{"status": "parse_error", "req_ids": ["R2"],
                           "batch_id": "t.b00000"}])
    rep = verify_run(ds, "r1")
    assert rep["verdict"] == "WARN"
    assert any("unresolved req" in w for w in rep["warns"])


def test_legacy_rows_with_full_coverage_pass(tmp_path):
    # old batch rows carry no req_ids — complete coverage proves resolution
    ds = _mk_run(tmp_path, kept=["R1", "R2"], failed=[],
                 corpus_ids=["R1", "R2"],
                 batches=[{"status": "parse_error", "batch_id": "t.b00000"}])
    rep = verify_run(ds, "r1")
    assert rep["verdict"] == "PASS", (rep["warns"], rep["fails"])
    assert any("transient" in n for n in rep["notes"])


def test_legacy_rows_with_incomplete_coverage_warn(tmp_path):
    ds = _mk_run(tmp_path, kept=["R1"], failed=[],
                 corpus_ids=["R1", "R2"],
                 batches=[{"status": "parse_error", "batch_id": "t.b00000"}])
    rep = verify_run(ds, "r1")
    assert rep["verdict"] == "WARN"
    assert any("resolution unknown" in w for w in rep["warns"])
