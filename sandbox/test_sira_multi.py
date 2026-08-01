"""Tests for the multi-MNO batch orchestrator (multi-mno-sira D-DRAFT-7).

The subprocess invocation of SIRA's run_pipeline.py is validated on the
work PC (no runnable SIRA clone here); these tests cover the pure parts:
command construction, cell selection, and the dry-run / continue-on-error
control flow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.sira_multi import (
    _parse_cell_list,
    build_pipeline_cmd,
    ensure_cell_data_config,
    run_cells,
)


def _make_cell(db_root: Path, dirname: str) -> None:
    raw = db_root / dirname / "raw"
    raw.mkdir(parents=True)
    (raw / "metadata.json").write_text("{}")


# ── ensure_cell_data_config ───────────────────────────────────────

def _seed_clone(tmp_path: Path, body: str = "name: nora\nsplit: test\nk_values: [10]\n") -> Path:
    cfg_dir = tmp_path / "scripts" / "configs" / "data"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "nora.yaml").write_text(body, encoding="utf-8")
    return tmp_path


def test_ensure_cell_data_config_writes_named_yaml(tmp_path):
    clone = _seed_clone(tmp_path)
    out = ensure_cell_data_config(clone, "VZW__Feb2026")
    assert out == clone / "scripts" / "configs" / "data" / "VZW__Feb2026.yaml"
    body = out.read_text(encoding="utf-8")
    assert "name: VZW__Feb2026" in body and "name: nora" not in body
    assert "split: test" in body and "k_values: [10]" in body  # other fields preserved


def test_ensure_cell_data_config_adds_name_when_absent(tmp_path):
    clone = _seed_clone(tmp_path, body="split: test\n")
    assert ensure_cell_data_config(clone, "TMO__Jan2026").read_text(
        encoding="utf-8"
    ).startswith("name: TMO__Jan2026\n")


def test_ensure_cell_data_config_missing_template_fails_loud(tmp_path):
    (tmp_path / "scripts" / "configs" / "data").mkdir(parents=True)  # no nora.yaml
    with pytest.raises(SystemExit, match="template not found"):
        ensure_cell_data_config(tmp_path, "VZW__Feb2026")


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


def test_build_cmd_run_name_when_set(tmp_path):
    cmd = build_pipeline_cmd(("VZW", "Feb2026"), tmp_path, run_name="enrich-stable")
    assert "+run_name=enrich-stable" in cmd          # pinned for resume


def test_build_cmd_no_run_name_when_unset(tmp_path):
    cmd = build_pipeline_cmd(("VZW", "Feb2026"), tmp_path)
    assert not any(c.startswith("+run_name") for c in cmd)   # default: run_pipeline's own name


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
    _seed_clone(tmp_path)  # clone == db_root here; provide the nora.yaml template
    _make_cell(tmp_path, "VZW__Feb2026")
    _make_cell(tmp_path, "TMO__Jan2026")

    class _FakeProc:
        # VZW fails (1), TMO succeeds (0) — verify both attempted
        def __init__(self, rc): self.returncode = rc

    calls = []

    def _fake_run(cmd, cwd=None, env=None):
        calls.append(cmd)
        rc = 1 if "data.name=VZW__Feb2026" in cmd else 0
        return _FakeProc(rc)

    monkeypatch.setattr("sandbox.sira_multi.subprocess.run", _fake_run)
    results = run_cells(tmp_path, tmp_path, dry_run=False)
    assert results[("VZW", "Feb2026")] == 1
    assert results[("TMO", "Jan2026")] == 0
    assert len(calls) == 2                            # both attempted despite VZW failure


# ── out-of-clone config root (read-only clone contract) ────────────


def test_ensure_cell_data_config_external_root(tmp_path):
    """config_root -> YAML lands OUTSIDE the clone; clone untouched."""
    from sandbox.sira_multi import cell_config_root, ensure_cell_data_config
    clone = tmp_path / "clone"
    cfg = clone / "scripts" / "configs" / "data"
    cfg.mkdir(parents=True)
    (cfg / "nora.yaml").write_text("name: nora\nsplit: test\n")
    db_root = tmp_path / "db"
    db_root.mkdir()
    root = cell_config_root(db_root)

    out = ensure_cell_data_config(clone, "GP__Feb2026", config_root=root)
    assert out == root / "data" / "GP__Feb2026.yaml"
    assert "name: GP__Feb2026" in out.read_text()
    # the clone's config dir gained nothing
    assert list(cfg.iterdir()) == [cfg / "nora.yaml"]


def test_cell_config_root_not_a_cell_dir(tmp_path):
    """.hydra-configs must never be mistaken for a <MNO>__<REL> cell."""
    from sandbox.sira_cells import parse_cell_dirname
    from sandbox.sira_multi import cell_config_root
    name = cell_config_root(tmp_path).name
    assert parse_cell_dirname(name) is None


def test_run_cells_passes_extra_config_dir_env(tmp_path, monkeypatch):
    """The child process gets SIRA_EXTRA_CONFIG_DIR pointing at db_root's
    config root (the patch reads it; the clone stays read-only)."""
    import subprocess as sp

    from sandbox.sira_multi import cell_config_root, run_cells

    clone = tmp_path / "clone"
    cfg = clone / "scripts" / "configs" / "data"
    cfg.mkdir(parents=True)
    (cfg / "nora.yaml").write_text("name: nora\n")
    db_root = tmp_path / "db"
    (db_root / "GP__Feb2026" / "raw").mkdir(parents=True)
    (db_root / "GP__Feb2026" / "raw" / "metadata.json").write_text("{}")

    seen = {}

    def fake_run(cmd, cwd=None, env=None):
        seen["env"] = env
        class P:
            returncode = 0
        return P()

    monkeypatch.setattr(sp, "run", fake_run)
    import sandbox.sira_multi as sm
    monkeypatch.setattr(sm, "subprocess", sp)

    run_cells(db_root, clone)
    expected = str(cell_config_root(db_root).resolve())
    assert seen["env"]["SIRA_EXTRA_CONFIG_DIR"] == expected
    # and the cell YAML was generated externally, not in the clone
    assert (cell_config_root(db_root) / "data" / "GP__Feb2026.yaml").is_file()
    assert not (cfg / "GP__Feb2026.yaml").exists()


# ── verify_cells / --verify mode ──────────────────────────────────

import json

from sandbox.sira_multi import main as multi_main, verify_cells


def _batch_row(**kw) -> str:
    row = {"batch_id": "b0", "plan": "PA", "n_reqs": 2, "status": "ok",
           "answered": 2, "missing": 0, "prompt_tokens_est": 100,
           "resp_tokens_est": 180, "closed_by": "end", "oversized": False,
           "attempt": 0, "ms": 5}
    row.update(kw)
    return json.dumps(row)


def _make_verified_cell(db_root: Path, dirname: str, run: str = "r1") -> Path:
    """A healthy enriched cell: corpus + kept traces + enrichments +
    one ok batch row."""
    _make_cell(db_root, dirname)
    ds = db_root / dirname
    with open(ds / "raw" / "corpus.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"_id": "R1", "title": "t", "text": "one"}) + "\n")
        f.write(json.dumps({"_id": "R2", "title": "t", "text": "two"}) + "\n")
    rd = ds / "runs" / "doc-enrich" / run
    rd.mkdir(parents=True)
    (rd / "trace.kept.jsonl").write_text(
        "".join(json.dumps({"doc_id": d, "status": "ok"}) + "\n"
                for d in ("R1", "R2")), encoding="utf-8")
    (rd / "enrichments.kept.jsonl").write_text(
        "".join(json.dumps({"doc_id": d, "phrases": ["p"]}) + "\n"
                for d in ("R1", "R2")), encoding="utf-8")
    (rd / "batches.jsonl").write_text(_batch_row() + "\n", encoding="utf-8")
    return ds


def test_verify_cells_all_pass(tmp_path, capsys):
    _make_verified_cell(tmp_path, "VZW__Feb2026")
    _make_verified_cell(tmp_path, "TMO__Jan2026")
    assert verify_cells(tmp_path, "r1") == 0
    out = capsys.readouterr().out
    assert "TMO__Jan2026/runs" in out and "VZW__Feb2026/runs" in out
    assert "verify summary: 2 cell(s) — 2 PASS" in out


def test_verify_cells_fail_propagates(tmp_path, capsys):
    _make_verified_cell(tmp_path, "VZW__Feb2026")
    ds = _make_verified_cell(tmp_path, "TMO__Jan2026")
    with open(ds / "runs" / "doc-enrich" / "r1" / "trace.kept.jsonl",
              "a", encoding="utf-8") as f:
        f.write('{"doc_id": "torn')
    assert verify_cells(tmp_path, "r1") == 1
    assert "1 FAIL, 1 PASS" in capsys.readouterr().out


def test_verify_cells_only_intersects_and_warns_missing(tmp_path, capsys):
    _make_verified_cell(tmp_path, "VZW__Feb2026")
    _make_verified_cell(tmp_path, "TMO__Jan2026")
    rc = verify_cells(tmp_path, "r1",
                      only=[("VZW", "Feb2026"), ("ATT", "Mar2026")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cell not found" in out and "ATT__Mar2026" in out
    assert "TMO__Jan2026/runs" not in out
    assert "verify summary: 1 cell(s) — 1 PASS" in out


def test_verify_cells_unknown_run_name_all_skipped(tmp_path, capsys):
    _make_verified_cell(tmp_path, "VZW__Feb2026")
    assert verify_cells(tmp_path, "nope") == 1
    out = capsys.readouterr().out
    assert "skipping" in out and "every cell was skipped" in out


def test_verify_cells_non_cell_dirs_invisible(tmp_path, capsys):
    _make_verified_cell(tmp_path, "VZW__Feb2026")
    # corpus but no metadata.json + non-cell name → not a cell
    legacy = tmp_path / "legacy-dataset" / "raw"
    legacy.mkdir(parents=True)
    (legacy / "corpus.jsonl").write_text("")
    assert verify_cells(tmp_path, "r1") == 0
    out = capsys.readouterr().out
    assert "legacy-dataset" not in out
    assert "verify summary: 1 cell(s) — 1 PASS" in out


def test_verify_cells_no_cells(tmp_path, capsys):
    assert verify_cells(tmp_path, "r1") == 1
    assert "no cells found" in capsys.readouterr().out


def test_main_verify_mode_no_clone_needed(tmp_path, capsys):
    _make_verified_cell(tmp_path, "VZW__Feb2026")
    rc = multi_main(["--db-root", str(tmp_path), "--verify",
                     "--run-name", "r1"])
    assert rc == 0
    assert "verify summary" in capsys.readouterr().out


def test_main_verify_requires_run_name(tmp_path):
    with pytest.raises(SystemExit, match="requires --run-name"):
        multi_main(["--db-root", str(tmp_path), "--verify"])


def test_main_verify_strict_fails_on_warn(tmp_path):
    ds = _make_verified_cell(tmp_path, "VZW__Feb2026")
    (ds / "runs" / "doc-enrich" / "r1" / "batches.jsonl").write_text(
        _batch_row(status="parse_error") + "\n", encoding="utf-8")
    argv = ["--db-root", str(tmp_path), "--verify", "--run-name", "r1"]
    assert multi_main(argv) == 0
    assert multi_main(argv + ["--strict"]) == 1
