"""Tests for the golden run report inspector CLI."""

import json

from core.src.eval.golden_report_cli import format_sample, load_queries, main


def _write_run(tmp_path, stage1):
    golden = tmp_path / "golden"
    run_dir = golden / "runs" / "20260101T000000-test"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(json.dumps({"stage1": stage1}))
    samples = golden / "samples"
    samples.mkdir()
    (samples / "gs-0001.json").write_text(
        json.dumps({"sample_id": "gs-0001", "query": "what is foo?"})
    )
    return run_dir


STAGE1 = [
    {
        "sample_id": "gs-0001",
        "recall": 0.5,
        "hits": [{"req_id": "REQ-1", "mno": None, "release": None, "rank": 2}],
        "misses": [{"req_id": "REQ-2", "mno": None, "release": None}],
        "retrieved_req_ids": ["REQ-9", "REQ-1", "REQ-8"],
    },
    {
        "sample_id": "gs-0002",
        "recall": 1.0,
        "hits": [{"req_id": "REQ-3", "mno": None, "release": None, "rank": 1}],
        "misses": [],
        "retrieved_req_ids": ["REQ-3"],
    },
]


def test_format_sample_side_by_side():
    lines = format_sample(STAGE1[0], query="what is foo?")
    text = "\n".join(lines)
    assert "gs-0001" in text
    assert "Q: what is foo?" in text
    assert "hit r2" in text
    assert "MISS" in text
    # retrieved ground-truth ids are starred, others are not
    assert "REQ-1 *" in text
    assert "REQ-9 *" not in text


def test_load_queries_skips_bad_files(tmp_path):
    (tmp_path / "gs-0001.json").write_text(
        json.dumps({"sample_id": "gs-0001", "query": "q1"})
    )
    (tmp_path / "gs-0002.json").write_text("not json")
    assert load_queries(tmp_path) == {"gs-0001": "q1"}


def test_main_default_samples_dir(tmp_path, capsys):
    run_dir = _write_run(tmp_path, STAGE1)
    assert main([str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "gs-0001" in out
    assert "gs-0002" in out
    assert "Q: what is foo?" in out
    assert "2/2 samples shown" in out


def test_main_misses_flag_filters(tmp_path, capsys):
    run_dir = _write_run(tmp_path, STAGE1)
    assert main([str(run_dir), "--misses"]) == 0
    out = capsys.readouterr().out
    assert "gs-0001" in out
    assert "gs-0002" not in out
    assert "1/2 samples shown (misses only)" in out


def test_main_missing_report(tmp_path, capsys):
    assert main([str(tmp_path)]) == 2
    assert "not found" in capsys.readouterr().err
