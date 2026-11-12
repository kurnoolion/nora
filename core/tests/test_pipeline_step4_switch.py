"""Stage graph step 4, slice 5: the build keeps no copy of a stored output.

A clean stored output moves from the build's cell directory into the stage
store; every later reader finds it through the build's manifest. The
taxonomy stage, whose per-plan reuse lived in the build's cell directory,
starts a new fingerprint from the newest matching store entry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.src.pipeline import fingerprints as fpm
from core.src.pipeline.cells import Cell
from core.src.pipeline.generations import bump
from core.src.pipeline.manifest import BuildManifest
from core.src.pipeline.runner import PipelineContext
from core.src.pipeline.stage_store import StageStore
from core.src.pipeline.stages import run_parse, run_taxonomy
from core.tests.test_pipeline_stage_store import CELL, _build, _corpus, _manifest, _prefix
from core.tests.test_stages_per_cell import _ScriptedProvider, _seed_plan_tree

TCELL = Cell("MNO-A", "Apr2031")
KEY = f"{TCELL.mno}/{TCELL.release}"


# -- the corpus prefix ---------------------------------------------------------

def test_a_rerun_in_the_same_build_reuses_and_keeps_its_origin(tmp_path):
    corpus, stages = _corpus(tmp_path), tmp_path / "stages"
    a = _build(tmp_path, "build-a", corpus, stages)
    _prefix(a)
    p, r, _ = _prefix(a)
    assert p.stats["reused"] == 1 and r.stats["docs"] == 0
    for st in ("profile", "parse", "resolve"):
        assert _manifest(a)[st][f"{CELL.mno}/{CELL.release}"]["origin"] == "computed"
        assert not a.stage_output(st, CELL).exists()


def test_leftovers_of_a_degraded_run_give_way_to_the_stored_output(tmp_path):
    corpus, stages = _corpus(tmp_path), tmp_path / "stages"
    _prefix(_build(tmp_path, "build-a", corpus, stages))
    b = _build(tmp_path, "build-b", corpus, stages)
    from core.src.pipeline.stages import run_profile
    run_profile(b)
    stale = b.stage_output("parse", CELL)
    stale.mkdir(parents=True)
    (stale / "half_tree.json").write_text("{}")         # a degraded run's remains
    assert run_parse(b).stats["docs"] == 0
    assert not stale.exists()
    assert b.stage_input("parse", CELL).is_relative_to(stages)


# -- taxonomy ------------------------------------------------------------------

@pytest.fixture()
def tax(tmp_path, monkeypatch):
    """A versioned image (taxonomy is stored) and a scripted model."""
    real = fpm.code_version
    monkeypatch.setattr(fpm, "code_version", lambda s: "img-1" if s == "taxonomy" else real(s))
    provider = _ScriptedProvider()
    corpus, stages = tmp_path / "corpus", tmp_path / "stages"
    corpus.mkdir()

    def env(name: str, plans: dict[str, str], parse_fp: str) -> PipelineContext:
        e = tmp_path / name
        for plan, text in plans.items():
            _seed_plan_tree(e, TCELL.mno, TCELL.release, plan)
            p = e / "out" / "parse" / TCELL.mno / TCELL.release / f"{plan}_tree.json"
            tree = json.loads(p.read_text())
            tree["requirements"][0]["text"] = text
            p.write_text(json.dumps(tree))
        BuildManifest(e / "STAGES.json").record("parse", KEY, parse_fp, origin="computed")
        ctx = PipelineContext.standalone(env_dir=e, model_provider="mock")
        ctx.corpus_dir, ctx.stages_root = corpus, stages
        monkeypatch.setattr(ctx, "create_llm_provider", lambda *a, **k: provider)
        return ctx

    return env, provider, corpus, stages


def _asked(provider) -> list[str]:
    return sorted(p for p in ("PLAN1", "PLAN2") for q in provider.prompts if f"Plan ID: {p}" in q)


def test_taxonomy_moves_to_the_store_and_is_reused(tax):
    env, provider, _, stages = tax
    a = env("a", {"PLAN1": "one", "PLAN2": "two"}, "parse-1")
    assert run_taxonomy(a).stats["source"] == "derived"
    assert not a.stage_output("taxonomy", TCELL).exists()
    held = a.stage_input("taxonomy", TCELL)
    assert held.is_relative_to(stages) and (held / "extraction_state.json").is_file()
    assert (a.stage_output("taxonomy") / "taxonomy.json").is_file()   # the build-level file stays
    provider.prompts.clear()
    assert run_taxonomy(a).stats["source"] == "cache" and provider.prompts == []


def test_a_new_fingerprint_starts_from_the_matching_store_entry(tax):
    env, provider, _, stages = tax
    run_taxonomy(env("a", {"PLAN1": "one", "PLAN2": "two"}, "parse-1"))
    provider.prompts.clear()
    b = env("b", {"PLAN1": "one", "PLAN2": "two, revised"}, "parse-2")
    r = run_taxonomy(b)
    assert r.status == "OK" and _asked(provider) == ["PLAN2"]
    assert len(StageStore(stages).entries("taxonomy", TCELL)) == 2


def test_a_redo_starts_empty(tax):
    env, provider, corpus, _ = tax
    run_taxonomy(env("a", {"PLAN1": "one", "PLAN2": "two"}, "parse-1"))
    provider.prompts.clear()
    bump(corpus, KEY)
    bump(corpus, KEY)
    b = env("b", {"PLAN1": "one", "PLAN2": "two, revised"}, "parse-2")
    run_taxonomy(b)
    assert _asked(provider) == ["PLAN1", "PLAN2"]


def test_a_run_leaves_no_empty_stage_dirs_in_the_build(tmp_path):
    """After the stored stages run, their cells live in the store and the
    build keeps no empty <stage>/<MNO>/<release> skeleton."""
    from core.src.pipeline.runner import PipelineRunner
    corpus, stages = _corpus(tmp_path), tmp_path / "stages"
    a = _build(tmp_path, "build-a", corpus, stages)
    results = PipelineRunner(a).run(["profile", "parse", "resolve"])
    assert all(r.ok for r in results), [(r.stage, r.error_message) for r in results]
    for st in ("profile", "parse", "resolve"):
        assert not (a.env_dir / "out" / st).exists(), st
        assert a.stage_input(st, CELL).is_relative_to(stages)
    # a second run reuses everything and still leaves nothing behind
    PipelineRunner(a).run(["profile", "parse", "resolve"])
    assert not (a.env_dir / "out" / "parse").exists()


def test_tidy_keeps_a_degraded_cell_and_build_held_stages(tmp_path):
    corpus, stages = _corpus(tmp_path), tmp_path / "stages"
    a = _build(tmp_path, "build-a", corpus, stages)
    held = a.stage_output("parse", CELL)
    held.mkdir(parents=True)
    (held / "partial_tree.json").write_text("{}")          # a degraded cell's remains
    (a.stage_output("parse") / "MNO-X" / "Oct2031").mkdir(parents=True)
    assert a.fingerprints.tidy("parse") == 2               # the empty cell and its MNO dir
    assert (held / "partial_tree.json").exists()
    (a.stage_output("graph") / "sub").mkdir(parents=True)
    assert a.fingerprints.tidy("graph") == 0               # not a stored stage
