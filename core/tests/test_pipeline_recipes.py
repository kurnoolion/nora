"""Recipes (strand stage-graph, step 3): validation, the committed recipes,
the shell interface, and the recipe's parameters in variant fingerprints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.src.pipeline import recipes as R
from core.src.pipeline.cells import Cell
from core.src.pipeline.runner import PipelineContext
from core.src.pipeline.stage_graph import STAGE_GRAPH, VARIANT, tier_of

REPO = Path(__file__).resolve().parents[2]


def _ok(**over) -> dict:
    d = {"name": "r1", "stages": ["taxonomy", "enrich"],
         "params": {"enrich": {"taxonomy": True}}}
    d.update(over)
    return d


def test_committed_recipes_are_valid():
    files = sorted((REPO / R.RECIPES_DIR).glob("*.json"))
    assert files
    for f in files:
        R.load(f, check_name=True)


def test_driver_and_reserved_stages_are_variant_stages():
    for s in R.RESERVED_STAGES:
        assert tier_of(s) == VARIANT
    # enrich runs in the SIRA lane, not the pipeline; taxonomy is a pipeline stage
    assert [s for s in R.DRIVER_STAGES if s in STAGE_GRAPH] == ["taxonomy"]
    assert tier_of("taxonomy") == VARIANT


@pytest.mark.parametrize("data, needle", [
    (_ok(stages=["parse"]), "corpus prefix"),
    (_ok(stages=["graph"]), "no driver yet"),
    (_ok(stages=[]), "non-empty"),
    (_ok(stages=["enrich", "enrich"]), "twice"),
    (_ok(params={"enrich": {"temperature": 0}}), "unknown"),
    (_ok(params={"enrich": {"generation": True}}), "integer"),
    (_ok(params={"taxonomy": {"generation": 0}}), "1 or more"),
    (_ok(params={"enrich": {"model": " "}}), "empty"),
    (_ok(stages=["enrich"], params={"enrich": {"taxonomy": True}}), "does not run taxonomy"),
    (_ok(stages=["enrich"], params={"taxonomy": {}}), "does not run"),
    (_ok(serve={"mode": "graph"}), "serve.mode"),
    (_ok(serve={"units": ["vectors"]}), "serve.units"),
    (_ok(extra=1), "unknown key"),
    (_ok(name="Bad Name"), "name"),
])
def test_invalid_recipes_are_refused(data, needle):
    with pytest.raises(R.RecipeError) as e:
        R.parse(data)
    assert e.value.code == "RCP-E002" and needle in str(e.value)


def test_name_must_match_the_file(tmp_path):
    f = tmp_path / "other.json"
    f.write_text(json.dumps(_ok()), encoding="utf-8")
    with pytest.raises(R.RecipeError, match="file name"):
        R.load(f, check_name=True)
    assert R.load(f).name == "r1"           # a frozen RECIPE.json keeps its name
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    with pytest.raises(R.RecipeError) as e:
        R.load(tmp_path / "bad.json")
    assert e.value.code == "RCP-E001"


def test_defaults_order_and_digest():
    r = R.parse(_ok(stages=["enrich", "taxonomy"], params={}))
    assert r.stages == ("taxonomy", "enrich")
    assert r.stage_params("taxonomy") == {"generation": 1}
    assert r.stage_params("graph") == {}
    assert r.enrich_uses_taxonomy                      # runs taxonomy, so enrich reads it
    assert not R.parse(_ok(stages=["enrich"], params={})).enrich_uses_taxonomy
    assert r.serve == {"mode": "sira", "units": ["cells", "trees", "history"]}
    noted = R.parse(_ok(stages=["enrich", "taxonomy"], params={}, note="why"))
    assert noted.digest == r.digest                    # a note is not identity
    bumped = R.parse(_ok(params={"taxonomy": {"generation": 2}}))
    assert bumped.digest != R.parse(_ok(params={"taxonomy": {"generation": 1}})).digest


def test_shell_interface(tmp_path, capsys):
    f = tmp_path / "r1.json"
    f.write_text(json.dumps(_ok(params={"enrich": {"taxonomy": True, "model": "m1", "prompt_scheme": "s1"}})))
    assert R.main(["check", str(f)]) == 0
    assert "recipe r1: stages=taxonomy,enrich enrich.taxonomy=1 scheme=s1" in capsys.readouterr().out
    for key, want in [("has.taxonomy", "1"), ("has.graph", "0"), ("enrich.model", "m1"),
                      ("taxonomy.model", ""), ("enrich.taxonomy", "1"), ("prompt_scheme", "s1"),
                      ("stages", "taxonomy enrich"), ("taxonomy.generation", "1")]:
        assert R.main(["get", str(f), key]) == 0
        assert capsys.readouterr().out.strip() == want, key
    assert R.main(["get", str(f), "nonsense"]) == 1
    assert R.main(["frobnicate"]) == 2


# -- the recipe in fingerprints -------------------------------------------------

CELL = Cell("MNO-A", "Feb2026")


def _ctx(tmp_path: Path, recipe: dict | None) -> PipelineContext:
    env = tmp_path / "b"
    (env / "out").mkdir(parents=True, exist_ok=True)
    if recipe is not None:
        (env / R.RECIPE_FILE).write_text(json.dumps(recipe), encoding="utf-8")
    return PipelineContext.standalone(env_dir=env)


def test_variant_fingerprints_carry_the_recipe(tmp_path):
    plain = _ctx(tmp_path / "a", None).fingerprints.current("taxonomy", CELL)
    g1 = _ctx(tmp_path / "b", _ok()).fingerprints
    g2 = _ctx(tmp_path / "c", _ok(params={"taxonomy": {"generation": 2}})).fingerprints
    assert g1.current("taxonomy", CELL) != plain
    assert g2.current("taxonomy", CELL) != g1.current("taxonomy", CELL)
    # corpus stages ignore the recipe; enrich parameters do not touch taxonomy
    assert g1.current("parse", CELL) == g2.current("parse", CELL)
    g3 = _ctx(tmp_path / "d", _ok(params={"enrich": {"taxonomy": True, "generation": 5}})).fingerprints
    assert g3.current("taxonomy", CELL) == g1.current("taxonomy", CELL)
    assert json.loads(g1.params("taxonomy")["recipe"]) == {"generation": 1}


def test_a_bad_frozen_recipe_fails_loud(tmp_path):
    ctx = _ctx(tmp_path, {"name": "x", "stages": ["parse"]})
    with pytest.raises(R.RecipeError):
        ctx.fingerprints.current("taxonomy", CELL)


def test_generic_prompt_param():
    from core.src.pipeline import recipes as r
    base = {"name": "x", "stages": ["enrich"], "params": {"enrich": {"prompt": "generic"}}}
    rec = r.parse(base)
    assert r.get(rec, "enrich.prompt") == "generic"
    assert r.get(r.parse({"name": "x", "stages": ["enrich"]}), "enrich.prompt") == "per-mno"
    with pytest.raises(r.RecipeError, match="one of per-mno, generic"):
        r.parse({**base, "params": {"enrich": {"prompt": "other"}}})
    with pytest.raises(r.RecipeError, match="no taxonomy block"):
        r.parse({"name": "x", "stages": ["taxonomy", "enrich"],
                 "params": {"enrich": {"prompt": "generic", "taxonomy": True}}})
    committed = r.load(r.recipe_path(".", "sira-generic"), check_name=True)
    assert committed.prompt_scheme == "scheme-v1" and not committed.enrich_uses_taxonomy


def test_generic_prompt_enters_the_enrich_fingerprint_as_such(tmp_path):
    """A generic-prompt recipe's enrich fingerprint names the generic
    prompt, not a per-MNO file a build may still publish."""
    from core.src.pipeline.cells import Cell
    from core.src.pipeline.runner import PipelineContext
    cell = Cell("MNOA", "Apr2031")
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "doc_requirement_MNOA_v02.txt").write_text("{requirements}")
    ctx = PipelineContext.standalone(env_dir=tmp_path, model_provider="mock")
    per_mno = ctx.fingerprints.externals("enrich", cell)["doc_prompt"]
    assert per_mno not in ("generic", "absent")
    import shutil
    shutil.copy(R.recipe_path(".", "sira-generic"), tmp_path / "RECIPE.json")
    ctx = PipelineContext.standalone(env_dir=tmp_path, model_provider="mock")
    assert ctx.fingerprints.externals("enrich", cell)["doc_prompt"] == "generic"
