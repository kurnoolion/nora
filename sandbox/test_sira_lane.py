"""sira_lane command construction (docker-distro lane model)."""
from __future__ import annotations

import argparse
from pathlib import Path

from sandbox.sira_lane import build_commands


def _args(**kw):
    base = dict(env_dir=Path("/e"), db_root=Path("/d"),
                sira_clone=Path("sandbox/sira"), run_name="enrich-stable",
                only=None, wipe_stale_index=False, wipe_all_derived=False,
                stages="prepare,bm25,enrich_corpus", dry_run=True)
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
