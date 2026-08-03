"""Tests for the SIRA doc-enrichment inspector CLI."""

from __future__ import annotations

import json
from pathlib import Path

from sandbox.sira_enrich_inspect import (
    find_trace_row,
    inspect_cell,
    load_corpus_row,
    load_phrases,
    main,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _seed_cell(db_root: Path, name: str = "VZW__Feb2026") -> Path:
    """A minimal cell laid out the way enumerate_cells + the inspector expect."""
    cell = db_root / name
    (cell / "raw").mkdir(parents=True, exist_ok=True)
    (cell / "raw" / "metadata.json").write_text("{}", encoding="utf-8")  # cell marker
    _write_jsonl(cell / "raw" / "corpus.jsonl",
                 [{"_id": "A-1", "title": "NR SA Bands", "text": "SA NR band n78"}])
    _write_jsonl(cell / "enrichments" / "doc" / "best.jsonl",
                 [{"_id": "A-1", "phrases": ["5G", "n78"]}])
    run = cell / "runs" / "doc-enrich" / "enrich-1"
    _write_jsonl(run / "enrichments.kept.jsonl",
                 [{"doc_id": "A-1", "phrases": ["5G", "standalone"]}])
    _write_jsonl(run / "trace.kept.jsonl",
                 [{"doc_id": "A-1", "text": "SA NR band n78", "raw": "5G; standalone"}])
    return cell


def test_load_phrases_aggregates_and_dedups(tmp_path):
    p = tmp_path / "best.jsonl"
    _write_jsonl(p, [
        {"_id": "A-1", "phrases": ["5G", "NR"]},
        {"doc_id": "A-1", "phrases": ["NR", "n78"]},   # sharded; dup NR dropped
        {"_id": "A-2", "phrases": ["zzz"]},
    ])
    assert load_phrases(p, "A-1") == ["5G", "NR", "n78"]
    assert load_phrases(p, "A-2") == ["zzz"]
    assert load_phrases(p, "A-9") == []
    assert load_phrases(tmp_path / "missing.jsonl", "A-1") == []   # missing file → []


def test_load_corpus_row_by_id(tmp_path):
    c = tmp_path / "corpus.jsonl"
    _write_jsonl(c, [{"_id": "A-1", "title": "Bands", "text": "body"}])
    row = load_corpus_row(c, "A-1")
    assert row and row["title"] == "Bands"
    assert load_corpus_row(c, "A-9") is None


def test_find_trace_row(tmp_path):
    t = tmp_path / "trace.kept.jsonl"
    _write_jsonl(t, [{"doc_id": "A-1", "raw": "5G; standalone"}])
    assert find_trace_row(t, "A-1")["raw"] == "5G; standalone"
    assert find_trace_row(t, "A-9") is None


def test_inspect_cell_prints_both_sources(tmp_path, capsys):
    cell = _seed_cell(tmp_path)
    found = inspect_cell(cell, "A-1", run=None, show_text=False, show_trace=False)
    out = capsys.readouterr().out
    assert found
    assert "cell: VZW__Feb2026" in out and "NR SA Bands" in out
    assert "best.jsonl phrases (2)" in out          # 5G, n78
    assert "enrich-1" in out and "standalone" in out  # latest run's phrases


def test_inspect_cell_missing_req_returns_false(tmp_path):
    cell = _seed_cell(tmp_path)
    assert inspect_cell(cell, "NOPE", run=None, show_text=False, show_trace=False) is False


def test_main_found_across_cells(tmp_path, capsys):
    _seed_cell(tmp_path, "VZW__Feb2026")
    _seed_cell(tmp_path, "ATT__Nov2025")
    rc = main(["A-1", "--db-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "VZW__Feb2026" in out and "ATT__Nov2025" in out   # searched both cells


def test_main_not_found_returns_1(tmp_path):
    _seed_cell(tmp_path)
    assert main(["A-9", "--db-root", str(tmp_path)]) == 1


def test_main_no_db_root_errors(tmp_path):
    assert main(["A-1", "--db-root", ""]) == 2


# ── --failed triage listing ───────────────────────────────────────

from sandbox.sira_enrich_inspect import list_failed_cell, load_corpus_plans


def _seed_failed_cell(db_root: Path, name: str = "VZW__Feb2026") -> Path:
    """A cell whose run has failed rows across two statuses / two plans."""
    cell = _seed_cell(db_root, name)
    _write_jsonl(cell / "raw" / "corpus.jsonl", [
        {"_id": "A-1", "title": "t", "text": "**plan**: PA / Plan Alpha\nbody"},
        {"_id": "A-2", "title": "t", "text": "**plan**: PA\nbody"},
        {"_id": "B-1", "title": "t", "text": "**plan**: PB\nbody"},
        {"_id": "doc:PC", "title": "", "text": "whole-plan row, no stamp"},
    ])
    _write_jsonl(cell / "runs" / "doc-enrich" / "enrich-1" / "trace.failed.jsonl", [
        {"doc_id": "A-1", "status": "missing_in_batch_response"},
        {"doc_id": "A-2", "status": "missing_in_batch_response"},
        {"doc_id": "B-1", "status": "missing_in_batch_response"},
        {"doc_id": "doc:PC", "status": "all_filtered"},
    ])
    return cell


def test_load_corpus_plans_reads_stamp(tmp_path):
    cell = _seed_failed_cell(tmp_path)
    plans = load_corpus_plans(cell / "raw" / "corpus.jsonl")
    assert plans["A-1"] == "PA"        # composite stamp → plan_id part
    assert plans["B-1"] == "PB"
    assert plans["doc:PC"] == ""       # unstamped row


def test_list_failed_groups_status_then_plan(tmp_path, capsys):
    cell = _seed_failed_cell(tmp_path)
    n = list_failed_cell(cell, run="enrich-1", limit=10)
    assert n == 4
    out = capsys.readouterr().out
    assert "failed: 4 row(s), 2 status(es)" in out
    assert "status missing_in_batch_response (3):" in out
    assert "plan PA: 2 req(s)" in out and "plan PB: 1 req(s)" in out
    # coarse doc: row falls back to the plan embedded in its id
    assert "status all_filtered (1):" in out and "plan PC: 1 req(s)" in out
    assert "- A-1" in out and "- doc:PC" in out


def test_list_failed_limit_caps_ids(tmp_path, capsys):
    cell = _seed_failed_cell(tmp_path)
    list_failed_cell(cell, run="enrich-1", limit=1)
    out = capsys.readouterr().out
    assert "- A-1" in out and "- A-2" not in out
    assert "(+1 more)" in out


def test_list_failed_clean_cell(tmp_path, capsys):
    cell = _seed_cell(tmp_path)          # has a run, no trace.failed
    assert list_failed_cell(cell, run="enrich-1", limit=10) == 0
    assert "no failed rows — clean" in capsys.readouterr().out


def test_list_failed_counts_but_hides_skipped_rows(tmp_path, capsys):
    """skipped_* rows are build policy (coarse chunks excluded), not
    failures — summarized as a count, never listed as triage targets."""
    cell = _seed_failed_cell(tmp_path)
    with open(cell / "runs" / "doc-enrich" / "enrich-1" /
              "trace.failed.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"doc_id": "section:PA 3.2",
                            "status": "skipped_section_chunk"}) + "\n")
    n = list_failed_cell(cell, run="enrich-1", limit=10)
    assert n == 4                        # skipped row not counted as failed
    out = capsys.readouterr().out
    assert "(+1 skipped_* row(s)" in out
    assert "section:PA 3.2" not in out


def test_main_failed_sweeps_cells_with_banner(tmp_path, capsys):
    _seed_failed_cell(tmp_path, "VZW__Feb2026")
    _seed_cell(tmp_path, "TMO__Jan2026")
    rc = main(["--failed", "--db-root", str(tmp_path), "--run", "enrich-1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "local triage only, redact before sharing" in out
    assert "cell: TMO__Jan2026" in out and "cell: VZW__Feb2026" in out
    assert "total failed rows: 4" in out


def test_main_failed_and_req_id_mutually_exclusive(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        main(["A-1", "--failed", "--db-root", str(tmp_path)])
    with pytest.raises(SystemExit):
        main(["--db-root", str(tmp_path)])   # neither req_id nor --failed
