"""Tests for the ``dedup`` repair command (strand id-precision).

Field-observed gap: repeated single-req smoke invocations each appended
a kept + enrichment record for the same doc (different phrase sets),
``verify-run --strict`` FAILed on ``duplicate kept rows``, and no
existing repair command addressed the state — heal-torn sees no torn
lines or orphans, prune skips unchanged docs, retry-failed only evicts
failed rows. ``dedup`` closes the gap: newest-wins per resume key, both
resume files, temp + atomic-rename.
"""

from __future__ import annotations

import json
from pathlib import Path

from sandbox.sira_incremental import (
    _dedup_jsonl,
    dedup_run_files,
    main,
)


def _write_jsonl(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _run_dir(tmp_path: Path, stage: str = "doc-enrich", run: str = "enrich-stable") -> Path:
    d = tmp_path / "cell" / "runs" / stage / run
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestDedupJsonl:
    def test_newest_wins_order_preserved(self, tmp_path):
        p = tmp_path / "trace.kept.jsonl"
        _write_jsonl(p, [
            {"doc_id": "a", "v": 1},
            {"doc_id": "b", "v": 1},
            {"doc_id": "a", "v": 2},   # newer duplicate of a
        ])
        kept, dropped = _dedup_jsonl(p, lambda r: r.get("doc_id"))
        assert (kept, dropped) == (2, 1)
        rows = _read_jsonl(p)
        assert rows == [{"doc_id": "b", "v": 1}, {"doc_id": "a", "v": 2}]

    def test_no_duplicates_is_noop(self, tmp_path):
        p = tmp_path / "trace.kept.jsonl"
        _write_jsonl(p, [{"doc_id": "a"}, {"doc_id": "b"}])
        before = p.read_text(encoding="utf-8")
        assert _dedup_jsonl(p, lambda r: r.get("doc_id")) == (2, 0)
        assert p.read_text(encoding="utf-8") == before

    def test_unparseable_and_none_key_lines_kept(self, tmp_path):
        p = tmp_path / "trace.kept.jsonl"
        _write_jsonl(p, [
            {"doc_id": "a", "v": 1},
            "not json {{{",
            {"other": "no doc_id"},
            {"doc_id": "a", "v": 2},
        ])
        kept, dropped = _dedup_jsonl(p, lambda r: r.get("doc_id"))
        assert (kept, dropped) == (3, 1)
        lines = p.read_text(encoding="utf-8").splitlines()
        assert "not json {{{" in lines

    def test_dry_run_reports_without_rewriting(self, tmp_path):
        p = tmp_path / "trace.kept.jsonl"
        _write_jsonl(p, [{"doc_id": "a", "v": 1}, {"doc_id": "a", "v": 2}])
        before = p.read_text(encoding="utf-8")
        kept, dropped = _dedup_jsonl(p, lambda r: r.get("doc_id"), dry_run=True)
        assert (kept, dropped) == (1, 1)
        assert p.read_text(encoding="utf-8") == before

    def test_missing_file_noop(self, tmp_path):
        assert _dedup_jsonl(tmp_path / "absent.jsonl", lambda r: r.get("doc_id")) == (0, 0)

    def test_rewrite_lands_on_fresh_inode(self, tmp_path):
        # Promoted serve labels share files by hardlink; the repair must
        # never write through the shared inode.
        import os
        p = tmp_path / "trace.kept.jsonl"
        _write_jsonl(p, [{"doc_id": "a", "v": 1}, {"doc_id": "a", "v": 2}])
        link = tmp_path / "snapshot.jsonl"
        os.link(p, link)
        _dedup_jsonl(p, lambda r: r.get("doc_id"))
        # Snapshot keeps the original content; live file is deduped.
        assert len(_read_jsonl(link)) == 2
        assert len(_read_jsonl(p)) == 1


class TestDedupRunFiles:
    def test_doc_enrich_dedups_both_resume_files(self, tmp_path):
        rd = _run_dir(tmp_path)
        _write_jsonl(rd / "trace.kept.jsonl", [
            {"doc_id": "d1", "v": 1}, {"doc_id": "d1", "v": 2},
        ])
        _write_jsonl(rd / "enrichments.kept.jsonl", [
            {"doc_id": "d1", "phrases": ["old"]},
            {"doc_id": "d1", "phrases": ["new"]},
        ])
        counts = dedup_run_files(rd, "doc-enrich")
        assert counts["trace.kept.jsonl"] == (1, 1)
        assert counts["enrichments.kept.jsonl"] == (1, 1)
        assert _read_jsonl(rd / "enrichments.kept.jsonl")[0]["phrases"] == ["new"]

    def test_rerank_uses_pair_key(self, tmp_path):
        rd = _run_dir(tmp_path, stage="rerank")
        _write_jsonl(rd / "trace.kept.jsonl", [
            {"query_id": "q1", "doc_id": "d1", "v": 1},
            {"query_id": "q2", "doc_id": "d1", "v": 1},   # different pair — kept
            {"query_id": "q1", "doc_id": "d1", "v": 2},   # duplicate pair
        ])
        counts = dedup_run_files(rd, "rerank")
        assert counts["trace.kept.jsonl"] == (2, 1)


class TestCli:
    def test_cli_dedup_repairs_and_exits_zero(self, tmp_path, capsys):
        rd = _run_dir(tmp_path)
        _write_jsonl(rd / "trace.kept.jsonl", [
            {"doc_id": "d1", "v": 1}, {"doc_id": "d1", "v": 2},
        ])
        _write_jsonl(rd / "enrichments.kept.jsonl", [
            {"doc_id": "d1", "phrases": ["old"]},
            {"doc_id": "d1", "phrases": ["new"]},
        ])
        rc = main([
            "dedup", "--dataset", str(tmp_path / "cell"),
            "--run-name", "enrich-stable", "--stage", "doc-enrich",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "2 duplicate row(s) dropped" in out
        assert len(_read_jsonl(rd / "trace.kept.jsonl")) == 1

    def test_cli_dry_run_leaves_files_untouched(self, tmp_path, capsys):
        rd = _run_dir(tmp_path)
        _write_jsonl(rd / "trace.kept.jsonl", [
            {"doc_id": "d1", "v": 1}, {"doc_id": "d1", "v": 2},
        ])
        rc = main([
            "dedup", "--dataset", str(tmp_path / "cell"),
            "--run-name", "enrich-stable", "--stage", "doc-enrich",
            "--dry-run",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "would drop" in out and "Dry run" in out
        assert len(_read_jsonl(rd / "trace.kept.jsonl")) == 2

    def test_cli_missing_run_dir_skips(self, tmp_path, capsys):
        (tmp_path / "cell").mkdir()
        rc = main([
            "dedup", "--dataset", str(tmp_path / "cell"),
            "--run-name", "enrich-stable",
        ])
        assert rc == 0
        assert "skipping" in capsys.readouterr().out
