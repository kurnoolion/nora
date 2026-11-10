"""Enrich as a recorded, driver-side stage (strand stage-graph, step 3, slice 2).

The SIRA lane runs enrichment; `stamp_driver_stage` records its
fingerprint per completed cell. Pins: the spec sits outside the pipeline's
order, the taxonomy input follows the recipe, the externals and parameters
move the fingerprint, the stamp's cell discovery and idempotence, and that
enrich never goes to the stage store.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.src.pipeline import stage_graph as sg
from core.src.pipeline.cells import Cell
from core.src.pipeline.manifest import MANIFEST_NAME
from core.src.pipeline.runner import PipelineContext
from core.src.pipeline.stages import stamp_driver_stage

CELL = Cell("MNO-A", "Feb2026")


def _build(root: Path, recipe: dict | None = None, cells=(CELL,), prompt: str = "P1") -> PipelineContext:
    env = root / "b"
    for c in cells:
        meta = env / "out" / "sira" / c.mno / c.release / "raw" / "metadata.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text("{}")
    (env / "prompts").mkdir(parents=True, exist_ok=True)
    (env / "prompts" / "doc_requirement_MNO-A_v01.txt").write_text(prompt)
    if recipe is not None:
        (env / "RECIPE.json").write_text(json.dumps(recipe))
    ctx = PipelineContext.standalone(env_dir=env)
    ctx.enrich_model, ctx.sira_ref = "m1", "ref1"
    return ctx


def _manifest(ctx) -> dict:
    return json.loads((ctx.env_dir / MANIFEST_NAME).read_text())["stages"]


TAX = {"name": "t", "stages": ["taxonomy", "enrich"], "params": {"enrich": {"taxonomy": True}}}
PLAIN = {"name": "p", "stages": ["enrich"]}


def test_enrich_is_a_driver_stage_outside_the_pipeline_order():
    assert "enrich" not in sg.STAGE_GRAPH and "enrich" not in sg.STAGE_ORDER
    spec = sg.spec_of("enrich")
    assert spec.driver and spec.per_cell and sg.tier_of("enrich") == sg.VARIANT
    assert spec.optional_inputs == ("taxonomy",)
    assert sg.validate_graph() == []


def test_taxonomy_input_follows_the_recipe(tmp_path):
    none = _build(tmp_path / "a").fingerprints
    tax = _build(tmp_path / "b", TAX).fingerprints
    plain = _build(tmp_path / "c", PLAIN).fingerprints
    assert set(none.input_fps("enrich", CELL)) == {"parse", "taxonomy"}
    assert set(tax.input_fps("enrich", CELL)) == {"parse", "taxonomy"}
    assert set(plain.input_fps("enrich", CELL)) == {"parse"}
    off = _build(tmp_path / "d", {"name": "o", "stages": ["taxonomy", "enrich"],
                                  "params": {"enrich": {"taxonomy": False}}}).fingerprints
    assert set(off.input_fps("enrich", CELL)) == {"parse"}


def test_prompt_model_revision_and_generation_move_the_fingerprint(tmp_path):
    base = _build(tmp_path / "a", TAX).fingerprints.current("enrich", CELL)
    assert _build(tmp_path / "b", TAX, prompt="P2").fingerprints.current("enrich", CELL) != base
    ctx = _build(tmp_path / "c", TAX)
    ctx.enrich_model = "m2"
    assert ctx.fingerprints.current("enrich", CELL) != base
    ctx = _build(tmp_path / "d", TAX)
    ctx.sira_ref = "ref2"
    assert ctx.fingerprints.current("enrich", CELL) != base
    bumped = dict(TAX, params={"enrich": {"taxonomy": True, "generation": 2}})
    assert _build(tmp_path / "e", bumped).fingerprints.current("enrich", CELL) != base
    # the same inputs in another build give the same fingerprint
    assert _build(tmp_path / "f", TAX).fingerprints.current("enrich", CELL) == base


def test_doc_prompt_follows_the_lane_s_rule(tmp_path):
    from sandbox.enrich_batching import resolve_doc_prompt
    ctx = _build(tmp_path, TAX)
    d = ctx.env_dir / "prompts"
    for name in ("doc_requirement_MNO-A_v02.txt", "doc_requirement_MNO-A_v10.txt", "doc_requirement_MNO-B_v99.txt"):
        (d / name).write_text(name)
    chosen = Path(resolve_doc_prompt(str(d), "MNO-A")).name
    before = ctx.fingerprints.current("enrich", CELL)
    (d / chosen).write_text("edited")
    assert ctx.fingerprints.current("enrich", CELL) != before
    other = sorted(p for p in d.glob("doc_requirement_MNO-A_*.txt") if p.name != chosen)[0]
    after = ctx.fingerprints.current("enrich", CELL)
    other.write_text("edited too")
    assert ctx.fingerprints.current("enrich", CELL) == after


def test_stamp_records_completed_cells_once(tmp_path):
    other = Cell("MNO-A", "Jan2026")
    ctx = _build(tmp_path, TAX, cells=(CELL, other))
    (ctx.env_dir / "out" / "sira" / "MNO-B" / "Mar2026").mkdir(parents=True)   # not complete
    r = stamp_driver_stage(ctx, "enrich")
    assert r.ok and r.stats == {"cells": 2, "stamped": 2, "unchanged": 0}
    assert any(w.startswith("PIP-W002") and "parse" in w for w in r.warnings)
    m = _manifest(ctx)["enrich"]
    assert set(m) == {"MNO-A/Feb2026", "MNO-A/Jan2026"} and m["MNO-A/Feb2026"]["origin"] == "computed"
    r = stamp_driver_stage(ctx, "enrich")
    assert r.stats == {"cells": 2, "stamped": 0, "unchanged": 2}
    ctx.scope_releases = ["Jan2026"]
    ctx.enrich_model = "m2"
    r = stamp_driver_stage(ctx, "enrich")
    assert r.stats == {"cells": 1, "stamped": 1, "unchanged": 0}


def test_stamp_refuses_without_cells_or_for_an_unknown_stage(tmp_path):
    ctx = _build(tmp_path, TAX, cells=())
    r = stamp_driver_stage(ctx, "enrich")
    assert r.status == "FAIL" and r.error_code == "PIP-E002"
    assert stamp_driver_stage(ctx, "graph").error_code == "PIP-E001"


def test_enrich_never_goes_to_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("NORA_CODE_VERSION", "abc1234")
    ctx = _build(tmp_path, TAX)
    ctx.stages_root = tmp_path / "stages"
    assert not ctx.fingerprints.in_store("enrich")
    stamp_driver_stage(ctx, "enrich")
    assert not (tmp_path / "stages").exists()


def test_cli_stamps(tmp_path, monkeypatch, capsys):
    ctx = _build(tmp_path, TAX)
    import sys
    from core.src.pipeline import run_cli
    monkeypatch.setenv("NORA_SIRA_ENRICH_LLM_MODEL", "m9")
    monkeypatch.setattr(sys, "argv", ["run_cli", "--env-dir", str(ctx.env_dir), "--stamp", "enrich"])
    with pytest.raises(SystemExit) as e:
        run_cli.main()
    assert e.value.code == 0
    assert "STAMP enrich: WARN" in capsys.readouterr().out
    assert _manifest(ctx)["enrich"]["MNO-A/Feb2026"]["params"]["enrich_model"] == "m9"
