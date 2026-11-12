"""Per-cell generations at the corpus root (strand stage-graph, step 3,
slice 3): how a served cell is redone."""

from __future__ import annotations

import json

import pytest

from core.src.pipeline import generations as G
from core.src.pipeline.cells import Cell
from core.src.pipeline.runner import PipelineContext

CELL, OTHER = Cell("MNO-A", "Apr2031"), Cell("MNO-A", "Jan2031")


def test_default_bump_and_show(tmp_path, capsys):
    assert G.cell_generation(None, "MNO-A/Apr2031") == 1
    assert G.cell_generation(tmp_path, "MNO-A/Apr2031") == 1
    assert G.bump(tmp_path, "MNO-A/Apr2031", by="u", note="n") == 2
    assert G.bump(tmp_path, "MNO-A/Apr2031") == 3
    assert G.cell_generation(tmp_path, "MNO-A/Apr2031") == 3
    assert G.cell_generation(tmp_path, "MNO-A/Jan2031") == 1
    data = json.loads((tmp_path / G.GENERATIONS_FILE).read_text())
    assert data["cells"]["MNO-A/Apr2031"]["generation"] == 3
    assert not list(tmp_path.glob(".generations.json.*"))
    assert G.main(["show", str(tmp_path)]) == 0
    assert "MNO-A/Apr2031 generation=3" in capsys.readouterr().out


def test_bad_cell_and_bad_file(tmp_path):
    with pytest.raises(ValueError):
        G.bump(tmp_path, "not a cell")
    (tmp_path / G.GENERATIONS_FILE).write_text("[]")
    with pytest.raises(ValueError):
        G.cell_generation(tmp_path, "MNO-A/Apr2031")
    assert G.main(["bump", str(tmp_path), "MNO-A/Apr2031"]) == 1


def _ctx(tmp_path, corpus):
    env = tmp_path / "b"
    (env / "out").mkdir(parents=True, exist_ok=True)
    ctx = PipelineContext.standalone(env_dir=env)
    ctx.corpus_dir = corpus
    return ctx


def test_a_bump_moves_only_that_cell_s_variant_fingerprints(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    fps = _ctx(tmp_path, corpus).fingerprints
    before = {(s, c): fps.current(s, c) for s in ("parse", "taxonomy", "enrich") for c in (CELL, OTHER)}
    G.bump(corpus, "MNO-A/Apr2031")
    after = {(s, c): fps.current(s, c) for s in ("parse", "taxonomy", "enrich") for c in (CELL, OTHER)}
    assert after[("taxonomy", CELL)] != before[("taxonomy", CELL)]
    assert after[("enrich", CELL)] != before[("enrich", CELL)]
    assert after[("parse", CELL)] == before[("parse", CELL)]          # corpus stages never see it
    for s in ("parse", "taxonomy", "enrich"):
        assert after[(s, OTHER)] == before[(s, OTHER)]
    assert fps.externals("taxonomy", CELL)["cell_generation"] == "2"
    assert "cell_generation" not in fps.externals("taxonomy", OTHER)  # generation 1 adds nothing
