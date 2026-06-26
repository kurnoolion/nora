"""Tests for the verify_tables helper."""

from __future__ import annotations

import json
from pathlib import Path

from sandbox.verify_tables import has_inline_table, main, scan_corpus, scan_parse


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(reqs: list[dict]) -> str:
    return json.dumps({"requirements": reqs})


def _corpus(rows: list[dict]) -> str:
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def test_has_inline_table():
    assert has_inline_table("intro\n| Band | BW |\n| n78 | 100 |")
    assert has_inline_table("| a | b |")              # starts with pipe
    assert not has_inline_table("just prose, no table")
    assert not has_inline_table("")


def test_scan_parse_counts_inline_vs_missing(tmp_path):
    parse = tmp_path / "out" / "parse"
    _write(parse / "VZW" / "Feb2026" / "d_tree.json", _tree([
        {"text": "intro\n| Band | BW |\n| n78 | 100 |", "tables": [{"headers": ["Band"]}]},  # inlined
        {"text": "table missing from text", "tables": [{"headers": ["Band"]}]},               # MISSING
        {"text": "plain req, no table", "tables": []},                                         # no field
    ]))
    res = scan_parse(parse)
    assert res["reqs"] == 3
    assert res["tables_field"] == 2
    assert res["inlined"] == 1
    assert res["missing"] == 1
    assert res["per"]["VZW/Feb2026"]["missing"] == 1


def test_scan_corpus_per_cell(tmp_path):
    db = tmp_path / "db"
    _write(db / "VZW__Feb2026" / "raw" / "corpus.jsonl", _corpus([
        {"_id": "A-1", "text": "intro\n| Band | BW |\n| n78 | 100 |"},
        {"_id": "A-2", "text": "no table here"},
    ]))
    _write(db / "ATT__Nov2025" / "raw" / "corpus.jsonl", _corpus([
        {"_id": "B-1", "text": "| x | y |"},
    ]))
    res = scan_corpus(db)
    assert res["rows"] == 3 and res["with_tables"] == 2
    assert res["per"]["VZW__Feb2026"] == {"rows": 2, "with_tables": 1}
    assert res["per"]["ATT__Nov2025"] == {"rows": 1, "with_tables": 1}


def test_main_fails_on_missing_inline(tmp_path, capsys):
    parse = tmp_path / "out" / "parse"
    _write(parse / "VZW" / "Feb2026" / "d_tree.json", _tree([
        {"text": "table missing", "tables": [{"headers": ["Band"]}]},
    ]))
    rc = main(["--parse", str(parse)])
    assert rc == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_ok_and_crosscheck(tmp_path, capsys):
    parse = tmp_path / "out" / "parse"
    _write(parse / "VZW" / "Feb2026" / "d_tree.json", _tree([
        {"text": "intro\n| Band | BW |\n| n78 | 100 |", "tables": [{"headers": ["Band"]}]},
    ]))
    db = tmp_path / "db"
    _write(db / "VZW__Feb2026" / "raw" / "corpus.jsonl", _corpus([
        {"_id": "A-1", "text": "intro\n| Band | BW |\n| n78 | 100 |"},
    ]))
    rc = main(["--parse", str(parse), "--db-root", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[ok]" in out and "cross-check" in out


def test_main_crosscheck_fails_when_corpus_has_no_tables(tmp_path, capsys):
    parse = tmp_path / "out" / "parse"
    _write(parse / "VZW" / "Feb2026" / "d_tree.json", _tree([
        {"text": "intro\n| Band |\n| n78 |", "tables": [{"headers": ["Band"]}]},
    ]))
    db = tmp_path / "db"
    _write(db / "VZW__Feb2026" / "raw" / "corpus.jsonl", _corpus([
        {"_id": "A-1", "text": "tables dropped — old corpus"},
    ]))
    rc = main(["--parse", str(parse), "--db-root", str(db)])
    assert rc == 1
    assert "did not reach the BEIR corpus" in capsys.readouterr().out


def test_main_no_args_errors():
    assert main(["--db-root", ""]) == 2
