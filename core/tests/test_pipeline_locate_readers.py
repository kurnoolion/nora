"""Build-time readers find parse output the build no longer holds
(strand stage-graph, step 4 slice 2, design §10c).

One corpus is read twice: with every cell in the build's ``out/parse/``,
then with one cell moved into the stage store and named only by the
build's ``STAGES.json``. Every reader must answer the same.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.src.eval import golden_release_cli as grc
from core.src.pipeline.manifest import BuildManifest
from core.src.stores.history_build import build_history
from core.src.stores.trees import FileTreeStore, find_tree
from core.src.stores.trees_build import build_trees
from core.tests.test_stores_trees import _corpus
from sandbox.adapter.nora_to_beir import _load_trees

MOVED = ("mno-a", "Apr2031")


def _views(env: Path) -> dict:
    s = FileTreeStore(env)
    return {
        "cells": s.list_cells(),
        "docs": s.list_docs(),
        "cell_docs": s.list_docs(*MOVED),
        "req": [r["release"] for r in s.find_req("REQ_A_0001")],
        "tree": (find_tree(env, "docA", *MOVED) or Path()).name,
        "grc": grc.parse_cells(env),
        "texts": sorted(grc.cell_texts(env, *MOVED)),
        "adapter": sorted((t.get("plan_id"), len(t["requirements"])) for t in _load_trees(env)),
    }


@pytest.fixture()
def moved(tmp_path, monkeypatch):
    env, store = tmp_path / "build", tmp_path / "stages"
    _corpus(env)
    before = _views(env)
    entry = store / "parse" / MOVED[0] / MOVED[1] / "fpP"
    entry.parent.mkdir(parents=True)
    shutil.move(str(env / "out" / "parse" / MOVED[0] / MOVED[1]), str(entry))
    BuildManifest(env / "STAGES.json").record("parse", "/".join(MOVED), "fpP", origin="computed")
    monkeypatch.setenv("NORA_STAGES_ROOT", str(store))
    return env, store, before


def test_readers_answer_the_same_when_a_cell_is_only_in_the_store(moved):
    env, _, before = moved
    assert _views(env) == before
    assert before["cells"] and MOVED[1] in before["req"]


def test_without_the_stages_root_the_moved_cell_is_gone(moved, monkeypatch):
    env, _, before = moved
    monkeypatch.delenv("NORA_STAGES_ROOT")
    s = FileTreeStore(env)
    assert {"mno": MOVED[0], "release": MOVED[1]} not in s.list_cells()
    assert s.list_docs(*MOVED) == []


def test_trees_and_history_build_from_the_store(moved, tmp_path):
    env, _, _ = moved
    trees = build_trees(env, tmp_path / "trees")
    assert "/".join(MOVED) in trees["per_cell"]
    hist = build_history(env, tmp_path / "history")
    assert MOVED[1] in hist["per_mno"][MOVED[0]]["releases"]


# -- the debug and audit tools (slice 2b) -------------------------------------

def test_verify_req_recall_env_dir_finds_store_cells(tmp_path, monkeypatch):
    from sandbox.verify_req_recall import _cells_from_env
    env, store = tmp_path / "build", tmp_path / "stages"
    for stage in ("extract", "parse", "profile"):
        (env / "out" / stage / "mno-a" / "Jan2031").mkdir(parents=True)
    entry = store / "extract" / "mno-a" / "Apr2031" / "fpE"
    entry.mkdir(parents=True)
    BuildManifest(env / "STAGES.json").record("extract", "mno-a/Apr2031", "fpE", origin="computed")
    monkeypatch.setenv("NORA_STAGES_ROOT", str(store))
    got = {k: (e, p, pr) for k, e, p, pr in _cells_from_env(env, None)}
    assert got["mno-a/Jan2031"][0] == env / "out" / "extract" / "mno-a" / "Jan2031"
    assert got["mno-a/Jan2031"][2] == env / "out" / "profile" / "mno-a" / "Jan2031" / "profile.json"
    assert got["mno-a/Apr2031"][0] == entry
    assert got["mno-a/Apr2031"][1] == env / "out" / "parse" / "mno-a" / "Apr2031"   # absent: today's path


def test_tax_debug_reads_a_ledger_and_trees_from_the_store(moved, monkeypatch, capsys):
    import json
    from core.src.taxonomy import tax_debug
    env, store, _ = moved
    entry = store / "taxonomy" / MOVED[0] / MOVED[1] / "fpT"
    entry.mkdir(parents=True)
    (entry / "extraction_state.json").write_text(json.dumps(
        {"docs": {"docA_tree.json": {"status": "failed"}, "gone_tree.json": {"status": "failed"}}}))
    BuildManifest(env / "STAGES.json").record("taxonomy", "/".join(MOVED), "fpT", origin="computed")
    seen = []
    monkeypatch.setattr("core.src.parser.structural_parser.RequirementTree.load_json",
                        staticmethod(lambda p: seen.append(Path(p)) or object()))
    monkeypatch.setattr("core.src.taxonomy.extractor.split_tree_by_plan",
                        lambda tree: [type("S", (), {"plan_id": "PLAN_X"})()])
    units = list(tax_debug._failed_units(env))
    assert [k for k, _ in units] == [f"{MOVED[0]}/{MOVED[1]}/docA_tree.json"]
    assert seen == [store / "parse" / MOVED[0] / MOVED[1] / "fpP" / "docA_tree.json"]
    assert "SKIP mno-a/Apr2031/gone_tree.json: tree file missing" in capsys.readouterr().out


def test_parse_audit_reports_every_cell_including_the_store(moved, tmp_path, monkeypatch):
    import sys
    from core.src.parser import parse_audit
    env, _, _ = moved
    out = tmp_path / "audit"
    monkeypatch.setattr(sys, "argv", ["parse_audit", "--env-dir", str(env), "--out-dir", str(out)])
    parse_audit.main()
    got = sorted(p.relative_to(out).as_posix() for p in out.rglob("*_audit.csv"))
    assert f"{MOVED[0]}/{MOVED[1]}/docA_audit.csv" in got
    assert f"{MOVED[0]}/Jan2031/docA_audit.csv" in got
