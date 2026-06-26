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
