"""sira_lane command construction (docker-distro lane model)."""
from __future__ import annotations

import argparse
from pathlib import Path

from sandbox.sira_lane import build_commands


def _args(**kw):
    base = dict(env_dir=Path("/e"), db_root=Path("/d"),
                sira_clone=Path("sandbox/sira"), run_name="enrich-stable",
                only=None, wipe_stale_index=False, wipe_all_derived=False,
                enrich_doc_chunks=False, enrich_section_chunks=False,
                stages="prepare,bm25,enrich_corpus", dry_run=True,
                index="flat", overlay_root=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_two_steps_adapter_then_multi():
    cmds = build_commands(_args())
    assert "sandbox.adapter.nora_to_beir" in cmds[0]
    assert "sandbox.sira_multi" in cmds[1]
    assert "--multi-cell" in cmds[0]


def test_only_reaches_both_steps():
    cmds = build_commands(_args(only="MNOA__Feb2026"))
    assert cmds[0][cmds[0].index("--only") + 1] == "MNOA__Feb2026"
    assert cmds[1][cmds[1].index("--only") + 1] == "MNOA__Feb2026"


def test_wipe_all_takes_precedence_over_stale():
    cmds = build_commands(_args(wipe_all_derived=True, wipe_stale_index=True))
    assert "--wipe-all-derived" in cmds[0] and "--wipe-stale-index" not in cmds[0]


def test_coarse_chunk_flags_forwarded_to_multi_only():
    cmds = build_commands(_args(enrich_doc_chunks=True,
                                enrich_section_chunks=True))
    assert "--enrich-doc-chunks" in cmds[1]
    assert "--enrich-section-chunks" in cmds[1]
    assert "--enrich-doc-chunks" not in cmds[0]
    # default: neither flag appears
    default = build_commands(_args())
    assert "--enrich-doc-chunks" not in default[1]
    assert "--enrich-section-chunks" not in default[1]


def test_heal_targets_filters_by_only_and_existence(tmp_path):
    from sandbox.sira_lane import heal_targets
    for cell, has_run in (("MNOA__Feb2026", True), ("MNOB__Feb2026", True),
                          ("MNOC__Feb2026", False)):
        d = tmp_path / cell
        (d / "runs" / "doc-enrich" / "r1" if has_run else d).mkdir(parents=True)
    # all cells with the run dir
    assert [t.parents[2].name for t in heal_targets(tmp_path, "r1", None)] == \
        ["MNOA__Feb2026", "MNOB__Feb2026"]
    # --only restricts
    assert [t.parents[2].name
            for t in heal_targets(tmp_path, "r1", "MNOB__Feb2026")] == \
        ["MNOB__Feb2026"]
    # wrong run name / missing db_root -> empty
    assert heal_targets(tmp_path, "other-run", None) == []
    assert heal_targets(tmp_path / "absent", "r1", None) == []


def _repair_args(db_root, **kw):
    base = dict(env_dir=Path("/e"), db_root=db_root,
                sira_clone=Path("sandbox/sira"), run_name="r1",
                only=None, wipe_stale_index=False, wipe_all_derived=False,
                heal_torn=True, retry_failed=True, include_all_filtered=False,
                include_skipped=False, include_refused=False,
                stages="prepare,bm25,enrich_corpus", dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _seed_cell(db_root, cell):
    import json
    rd = db_root / cell / "runs" / "doc-enrich" / "r1"
    rd.mkdir(parents=True)
    (rd / "trace.kept.jsonl").write_text(
        json.dumps({"doc_id": "A", "status": "ok"}) + "\n"
        + '{"doc_id": "B"', encoding="utf-8")            # torn tail
    (rd / "enrichments.kept.jsonl").write_text(
        json.dumps({"doc_id": "A", "phrases": ["x"]}) + "\n", encoding="utf-8")
    (rd / "trace.failed.jsonl").write_text(
        json.dumps({"doc_id": "F", "status": "batch_error"}) + "\n",
        encoding="utf-8")
    return rd


def test_run_repairs_incremental_heals_and_retries(tmp_path, capsys):
    """Incremental mode (--wipe-stale-index keeps runs/): both repairs run —
    torn line dropped, recorded failure evicted for retry."""
    from sandbox.sira_lane import run_repairs
    rd = _seed_cell(tmp_path, "MNOA__Feb2026")
    run_repairs(_repair_args(tmp_path, wipe_stale_index=True))
    out = capsys.readouterr().out
    assert "healed MNOA__Feb2026/r1: 1 torn line(s)" in out
    assert "retry-failed MNOA__Feb2026/r1: evicted 1 doc(s)" in out
    assert '"doc_id": "B"' not in (rd / "trace.kept.jsonl").read_text()
    assert (rd / "trace.failed.jsonl").read_text() == ""


def test_run_repairs_skipped_under_full_wipe(tmp_path, capsys):
    """Full-rebuild mode: the adapter wipes runs/ anyway — repairs are
    superseded and must be skipped (files untouched, note printed)."""
    from sandbox.sira_lane import run_repairs
    rd = _seed_cell(tmp_path, "MNOA__Feb2026")
    before = (rd / "trace.kept.jsonl").read_text()
    run_repairs(_repair_args(tmp_path, wipe_all_derived=True))
    out = capsys.readouterr().out
    assert "skipping" in out and "--wipe-all-derived" in out
    assert (rd / "trace.kept.jsonl").read_text() == before
    assert (rd / "trace.failed.jsonl").read_text() != ""


def test_run_repairs_dry_run_lists_without_touching(tmp_path, capsys):
    from sandbox.sira_lane import run_repairs
    rd = _seed_cell(tmp_path, "MNOA__Feb2026")
    before = (rd / "trace.kept.jsonl").read_text()
    run_repairs(_repair_args(tmp_path, dry_run=True))
    out = capsys.readouterr().out
    assert "would heal-torn + retry-failed" in out
    assert (rd / "trace.kept.jsonl").read_text() == before


def test_run_repairs_noop_without_flags(tmp_path, capsys):
    from sandbox.sira_lane import run_repairs
    _seed_cell(tmp_path, "MNOA__Feb2026")
    run_repairs(_repair_args(tmp_path, heal_torn=False, retry_failed=False))
    assert capsys.readouterr().out == ""


def _main_argv(tmp_path, *extra):
    return ["--env-dir", str(tmp_path / "env"), "--db-root", str(tmp_path),
            "--dry-run", *extra]


def test_main_max_reqs_exports_env_var(tmp_path, monkeypatch, capsys):
    import os
    from sandbox.sira_lane import main
    # setenv (not delenv) so monkeypatch restores pre-test state even though
    # main() writes the key directly
    monkeypatch.setenv("NORA_SIRA_BATCH_MAX_REQS", "")
    assert main(_main_argv(tmp_path, "--max-reqs", "1")) == 0
    assert os.environ["NORA_SIRA_BATCH_MAX_REQS"] == "1"
    assert "single-req mode" in capsys.readouterr().out


def test_main_max_reqs_overrides_preset_env(tmp_path, monkeypatch):
    import os
    from sandbox.sira_lane import main
    monkeypatch.setenv("NORA_SIRA_BATCH_MAX_REQS", "35")
    assert main(_main_argv(tmp_path, "--max-reqs", "5")) == 0
    assert os.environ["NORA_SIRA_BATCH_MAX_REQS"] == "5"


def test_main_without_max_reqs_leaves_env_alone(tmp_path, monkeypatch):
    import os
    from sandbox.sira_lane import main
    monkeypatch.delenv("NORA_SIRA_BATCH_MAX_REQS", raising=False)
    assert main(_main_argv(tmp_path)) == 0
    assert "NORA_SIRA_BATCH_MAX_REQS" not in os.environ


def test_main_max_reqs_rejects_below_one(tmp_path):
    import pytest
    from sandbox.sira_lane import main
    with pytest.raises(SystemExit) as e:
        main(_main_argv(tmp_path, "--max-reqs", "0"))
    assert e.value.code == 2


def test_main_verify_runs_sweep_after_build(tmp_path, monkeypatch):
    import subprocess
    import sandbox.sira_multi as sm
    from sandbox.sira_lane import main
    calls = {}
    monkeypatch.setattr(subprocess, "call", lambda cmd: 0)
    monkeypatch.setattr(
        sm, "verify_cells",
        lambda db_root, run_name, only=None, **kw:
            calls.update(db_root=db_root, run_name=run_name, only=only) or 0)
    rc = main(["--env-dir", str(tmp_path / "env"), "--db-root", str(tmp_path),
               "--run-name", "r1", "--only", "VZW__Feb2026", "--verify"])
    assert rc == 0
    assert calls["run_name"] == "r1" and calls["only"] == [("VZW", "Feb2026")]


def test_main_verify_failure_propagates(tmp_path, monkeypatch, capsys):
    import subprocess
    import sandbox.sira_multi as sm
    from sandbox.sira_lane import main
    monkeypatch.setattr(subprocess, "call", lambda cmd: 0)
    monkeypatch.setattr(sm, "verify_cells", lambda *a, **kw: 1)
    rc = main(["--env-dir", str(tmp_path / "env"), "--db-root", str(tmp_path),
               "--run-name", "r1", "--verify"])
    assert rc == 1
    assert "verification FAILED" in capsys.readouterr().err


def test_main_verify_dry_run_prints_only(tmp_path, monkeypatch, capsys):
    import sandbox.sira_multi as sm
    from sandbox.sira_lane import main

    def _boom(*a, **kw):
        raise AssertionError("verify_cells must not run under --dry-run")

    monkeypatch.setattr(sm, "verify_cells", _boom)
    rc = main(_main_argv(tmp_path, "--verify"))
    assert rc == 0
    assert "would verify cells" in capsys.readouterr().out


# ── --index=cell-db dual-emit (corp-db-layer phase 3) ───────────────


def test_index_flag_default_is_flat_and_commands_unchanged():
    """Backward compatibility: the default --index=flat produces exactly
    the two legacy commands — the emit step must not appear anywhere in
    the command plan."""
    default = build_commands(_args())
    explicit_flat = build_commands(_args(index="flat"))
    with_db = build_commands(_args(index="cell-db"))
    assert default == explicit_flat == with_db  # emit is post-build, not a command
    assert all("cell_db_convert" not in " ".join(cmd) for cmd in default)


def test_emit_dry_run_prints_plan_only(tmp_path, capsys):
    from sandbox.sira_lane import run_cell_db_emit

    rc = run_cell_db_emit(_args(db_root=tmp_path, dry_run=True,
                                index="cell-db", only="mnoa__Apr2098"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "would emit cell DBs" in out
    assert "pinned run enrich-stable" in out
    assert not (tmp_path / "cell-dbs").exists()


def test_emit_bm25_only_build_uses_best_pointer(tmp_path, capsys):
    from sandbox.sira_lane import run_cell_db_emit

    rc = run_cell_db_emit(_args(db_root=tmp_path, dry_run=True,
                                index="cell-db", stages="prepare,bm25"))
    assert rc == 0
    assert "best-pointer enrichment" in capsys.readouterr().out


def test_emit_converts_built_cells(tmp_path, capsys):
    """Real dual-emit over a synthetic built cell: flat files untouched,
    cell DB lands under <db_root>/cell-dbs/ with the lane's run pinned."""
    pytest = __import__("pytest")
    bm25x = pytest.importorskip("bm25x")
    import hashlib
    import json
    import sqlite3

    from sandbox.sira_lane import run_cell_db_emit

    db_root = tmp_path
    base = db_root / "mnoa__Apr2098"
    (base / "raw").mkdir(parents=True)
    (base / "raw" / "metadata.json").write_text("{}", encoding="utf-8")
    docs = [("REQ-0", "emergency callback", "the device shall support emergency callback mode"),
            ("REQ-1", "registration", "the handset must register on the preferred network")]
    with open(base / "raw" / "corpus.jsonl", "w", encoding="utf-8") as f:
        for rid, t, x in docs:
            f.write(json.dumps({"_id": rid, "title": t, "text": x}) + "\n")
    (base / "index" / "best").mkdir(parents=True)
    b = bm25x.BM25()
    b.disable_auto_save()
    b.add([x for _, _, x in docs])
    b.save(str(base / "index" / "best"))
    run_dir = base / "runs" / "doc-enrich" / "enrich-stable"
    run_dir.mkdir(parents=True)
    with open(run_dir / "enrichments.kept.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"doc_id": "REQ-0", "phrases": ["cbm"]}) + "\n")

    flat_digest = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(base.rglob("*")) if p.is_file()
    }
    rc = run_cell_db_emit(_args(db_root=db_root, dry_run=False, index="cell-db"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "dual-emit" in out and "verify" in out

    # flat build byte-untouched
    assert flat_digest == {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(base.rglob("*")) if p.is_file()
    }
    db_path = db_root / "cell-dbs" / "cell-mnoa-Apr2098.db"
    assert db_path.exists()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    meta = dict(con.execute("SELECT key, value FROM meta"))
    con.close()
    assert meta["enrich_run"] == "enrich-stable"   # the lane's run, pinned
    assert meta["n_docs"] == "2"

    # re-run (the lane rebuilt): --force implied, no CNV-004
    assert run_cell_db_emit(_args(db_root=db_root, dry_run=False,
                                  index="cell-db")) == 0
