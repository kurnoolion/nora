"""Tests for the multi-MNO batch orchestrator (multi-mno-sira D-DRAFT-7).

The subprocess invocation of SIRA's run_pipeline.py is validated on the
work PC (no runnable SIRA clone here); these tests cover the pure parts:
command construction, cell selection, and the dry-run / continue-on-error
control flow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.sira_multi import _parse_cell_list, build_pipeline_cmd, run_cells


def _make_cell(db_root: Path, dirname: str) -> None:
    raw = db_root / dirname / "raw"
    raw.mkdir(parents=True)
    (raw / "metadata.json").write_text("{}")


# ── build_pipeline_cmd ────────────────────────────────────────────

def test_build_cmd_basic(tmp_path):
    cmd = build_pipeline_cmd(("VZW", "Feb2026"), tmp_path, python_exe="py")
    assert cmd[:2] == ["py", "scripts/run_pipeline.py"]
    assert "data=nora" in cmd
    assert "data.name=VZW__Feb2026" in cmd          # D-DRAFT-6 override
    assert f"db_root={tmp_path.resolve()}" in cmd    # absolute, cwd-independent
    assert "enrich=nora" in cmd and "rerank=nora" in cmd


def test_build_cmd_no_sglang_port_when_unset(tmp_path):
    cmd = build_pipeline_cmd(("VZW", "Feb2026"), tmp_path)
    assert not any(c.startswith("sglang.port=") for c in cmd)


def test_build_cmd_sglang_port_when_set(tmp_path):
    cmd = build_pipeline_cmd(("VZW", "Feb2026"), tmp_path, sglang_port=8030)
    assert "sglang.port=8030" in cmd


def test_build_cmd_stages(tmp_path):
    cmd = build_pipeline_cmd(
        ("VZW", "Feb2026"), tmp_path, stages=["prepare", "bm25", "enrich_corpus"])
    assert "stages=[prepare,bm25,enrich_corpus]" in cmd


# ── _parse_cell_list ──────────────────────────────────────────────

def test_parse_cell_list_ok():
    assert _parse_cell_list("VZW__Feb2026, TMO__Jan2026") == [
        ("VZW", "Feb2026"), ("TMO", "Jan2026"),
    ]


def test_parse_cell_list_rejects_bad_name():
    with pytest.raises(SystemExit, match="not a valid cell name"):
        _parse_cell_list("VZW__OA-baseline")


# ── run_cells (dry-run + selection) ───────────────────────────────

def test_run_cells_dry_run_all(tmp_path, capsys):
    _make_cell(tmp_path, "VZW__Feb2026")
    _make_cell(tmp_path, "TMO__Jan2026")
    results = run_cells(tmp_path, tmp_path, dry_run=True)
    assert set(results) == {("VZW", "Feb2026"), ("TMO", "Jan2026")}
    assert all(rc == 0 for rc in results.values())
    out = capsys.readouterr().out
    assert "data.name=VZW__Feb2026" in out          # command printed
    assert "data.name=TMO__Jan2026" in out


def test_run_cells_only_subset(tmp_path):
    _make_cell(tmp_path, "VZW__Feb2026")
    _make_cell(tmp_path, "TMO__Jan2026")
    results = run_cells(
        tmp_path, tmp_path, only=[("VZW", "Feb2026")], dry_run=True)
    assert set(results) == {("VZW", "Feb2026")}     # TMO excluded


def test_run_cells_no_cells(tmp_path, capsys):
    results = run_cells(tmp_path, tmp_path, dry_run=True)
    assert results == {}
    assert "no cells found" in capsys.readouterr().out


def test_run_cells_continue_on_error(tmp_path, monkeypatch):
    _make_cell(tmp_path, "VZW__Feb2026")
    _make_cell(tmp_path, "TMO__Jan2026")

    class _FakeProc:
        # VZW fails (1), TMO succeeds (0) — verify both attempted
        def __init__(self, rc): self.returncode = rc

    calls = []

    def _fake_run(cmd, cwd=None):
        calls.append(cmd)
        rc = 1 if "data.name=VZW__Feb2026" in cmd else 0
        return _FakeProc(rc)

    monkeypatch.setattr("sandbox.sira_multi.subprocess.run", _fake_run)
    results = run_cells(tmp_path, tmp_path, dry_run=False)
    assert results[("VZW", "Feb2026")] == 1
    assert results[("TMO", "Jan2026")] == 0
    assert len(calls) == 2                            # both attempted despite VZW failure
