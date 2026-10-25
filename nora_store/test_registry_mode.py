"""nora-store serving from the publish registry (strand corp-db-layer 4b,
publish-design.md §5): NORA_STORE_REGISTRY resolves the cell DBs, Trees
and History from the served release set; /registry/reload swaps only the
units whose file changed; /healthz reports registry_label and the per-unit
versions. bm25x is faked (the cell loader is replaced); the Trees and
History files are real builder output over synthetic trees.

Run from the repo root:  pytest nora_store/test_registry_mode.py
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import nora_store.service as svc
from core.src.stores import publish as pub
from core.src.stores.registry import registry_path
from core.src.stores.trees import db_name as trees_db_name
from core.tests.test_stores_publish import (
    MNO_A,
    MNO_B,
    R1,
    R2,
    _req,
    make_cell_db,
    make_cells,
    make_env,
    write_parse,
)
from nora_store.service import CellState
from nora_store.test_service_multi_cell import _FakeBM25

A1, A2, B1 = (MNO_A, R1), (MNO_A, R2), (MNO_B, R1)


def _fake_load_one_cell_db(db_path):
    """Stands in for the bm25x-backed loader: identity from the file's meta."""
    meta = pub.read_unit_meta(db_path)
    docs = ["req:x", "req:y"]
    st = CellState(
        cell=(meta["cell_mno"], meta["cell_release"]), bm25=_FakeBM25(len(docs)),
        doc_ids=docs, doc_id_to_idx={d: i for i, d in enumerate(docs)},
        corpus_by_id={d: {"title": d, "text": d} for d in docs}, max_df=100,
        engine="bm25x", db_meta=meta, db_path=str(db_path), composed=True)
    st.loaded_at = time.time()
    return st


@pytest.fixture()
def world(tmp_path, monkeypatch):
    env, cells, root, work = tmp_path / "env", tmp_path / "cells", tmp_path / "serve", tmp_path / "work"
    make_env(env)
    make_cells(cells, {A1: "fp-a1", A2: "fp-a2", B1: "fp-b1"})
    work.mkdir()
    applied: list[tuple] = []
    monkeypatch.setattr(svc, "_REGISTRY", str(registry_path(root)))
    monkeypatch.setattr(svc, "_RELEASE_SET", "")
    monkeypatch.setattr(svc, "_INDEX_MODE", "cell-db")
    monkeypatch.setattr(svc, "_ENGINE", "bm25x")
    monkeypatch.setattr(svc, "_DB_ROOT", "")
    monkeypatch.setattr(svc, "_ENV_DIR", "")
    monkeypatch.setattr(svc, "_TREES_ROOT", "")
    monkeypatch.setattr(svc, "_HISTORY_ROOT", "")
    monkeypatch.setattr(svc, "_OVERLAY_URL", "")
    monkeypatch.setattr(svc, "_OVERLAY_DB", str(tmp_path / "ovl.db"))
    monkeypatch.setattr(svc, "_CORR_ROOT", "")
    monkeypatch.setattr(svc, "_cells", {})
    monkeypatch.setattr(svc, "_label_cells", {})
    monkeypatch.setattr(svc, "_STORES", {})
    monkeypatch.setattr(svc, "_STORES_PATHS", {})
    monkeypatch.setattr(svc, "_cells_load_error", None)
    monkeypatch.setattr(svc, "_registry_label", "")
    monkeypatch.setattr(svc, "_registry_prompt_scheme", "")
    monkeypatch.setattr(svc, "_registry_versions", {})
    monkeypatch.setattr(svc, "_registry_units", None)
    monkeypatch.setattr(svc, "_QUERY_ENRICH_ENABLED", False)   # no prompts in the test env
    monkeypatch.setattr(svc, "_RERANK_ENABLED", False)
    monkeypatch.setattr(svc, "_load_one_cell_db", _fake_load_one_cell_db)
    monkeypatch.setattr(svc, "_apply_overlay_and_enrich", lambda st: applied.append(st.cell))
    return {"env": env, "cells": cells, "root": root, "work": work, "applied": applied,
            "client": TestClient(svc.app)}


def _publish(w, label, **kw):
    kw.setdefault("cells_src", str(w["cells"]))
    kw.setdefault("env_dir", str(w["env"]))
    kw.setdefault("work_dir", str(w["work"]))
    return pub.publish(w["root"], label, **kw)


def _flip(w, label):
    pub.gate(w["root"], label, "manual", "waived")
    return pub.flip(w["root"], label)


def _versions(hz, store):
    return {u: v["version"] for u, v in hz["registry_versions"][store].items()}


def test_serves_the_active_set(world):
    _publish(world, "set-1", base="none")
    _flip(world, "set-1")
    svc._load_cells()
    assert sorted(svc._cells) == [A2, A1, B1]
    assert svc._cells[A1].db_path.endswith(f"cells/cell-{MNO_A}-{R1}-v1.db")
    assert sorted(world["applied"]) == [A2, A1, B1]          # composed → Overlay folded in
    hz = world["client"].get("/healthz").json()
    assert hz["ok"] is True and hz["mode"] == "multi-cell" and hz["index_mode"] == "cell-db"
    assert hz["registry_label"] == "set-1" and hz["serve_label"] == "set-1"
    assert hz["registry_release_set"] == ""
    assert _versions(hz, "cells") == {f"{MNO_A}__{R1}": 1, f"{MNO_A}__{R2}": 1, f"{MNO_B}__{R1}": 1}
    assert _versions(hz, "trees") == _versions(hz, "cells")
    assert _versions(hz, "history") == {MNO_A: 1, MNO_B: 1}
    assert hz["registry_versions"]["cells"][f"{MNO_A}__{R1}"]["fingerprint"] == "fp-a1"
    # identity keys come from the cell DBs' meta, as in cell-db mode
    assert hz["data_fingerprint_cells"] == {f"{MNO_A}__{R1}": "fp-a1", f"{MNO_A}__{R2}": "fp-a2",
                                            f"{MNO_B}__{R1}": "fp-b1"}
    assert hz["stores"] == {"overlay": "local", "trees": "local", "history": "local"}
    cells = world["client"].get("/stores/trees/v1/cells").json()
    assert [(c["mno"], c["release"]) for c in cells] == [A1, A2, B1]
    assert world["client"].get("/stores/history/v1/mnos").json() == [MNO_A, MNO_B]


def test_reload_swaps_only_changed_units(world):
    _publish(world, "set-1", base="none")
    _flip(world, "set-1")
    svc._load_cells()
    world["applied"].clear()
    loaded_at = {ck: st.loaded_at for ck, st in svc._cells.items()}

    # set-2: one cell's content changed, one MNO's trees changed
    make_cell_db(world["cells"] / f"cell-{MNO_A}-{R1}.db", MNO_A, R1, "fp-a1-NEW")
    write_parse(world["env"], MNO_B, R1, "docB", [_req("REQ_B_1", section="1"), _req("REQ_B_2", "REQ_B_1")])
    out = _publish(world, "set-2")
    assert out["history_rebuilt"] == [MNO_B]
    _flip(world, "set-2")

    # nothing changes until the reload
    assert world["client"].get("/healthz").json()["registry_label"] == "set-1"
    r = world["client"].post("/registry/reload")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["registry_label"] == "set-2" and body["errors"] == [] and body["removed"] == []
    assert body["changed"] == {"cells": [f"{MNO_A}__{R1}"], "trees": [f"{MNO_B}__{R1}"],
                               "history": [MNO_B]}
    assert svc._cells[A1].db_path.endswith("-v2.db")
    assert svc._cells[A2].loaded_at == loaded_at[A2] and svc._cells[B1].loaded_at == loaded_at[B1]
    assert world["applied"] == [A1]                         # recomposed the changed cell only
    hz = world["client"].get("/healthz").json()
    assert _versions(hz, "cells")[f"{MNO_A}__{R1}"] == 2 and _versions(hz, "trees")[f"{MNO_B}__{R1}"] == 2
    assert _versions(hz, "history") == {MNO_A: 1, MNO_B: 2}
    assert hz["data_fingerprint_cells"][f"{MNO_A}__{R1}"] == "fp-a1-NEW"
    # the reopened Trees store serves the new file
    assert any(n["req_id"] == "REQ_B_2" for n in
               world["client"].get("/stores/trees/v1/find", params={"req_id": "REQ_B_2"}).json())

    # a no-op reload changes nothing
    body = world["client"].post("/registry/reload").json()
    assert body["changed"] == {"cells": [], "trees": [], "history": []}

    # rollback → reload restores v1
    pub.rollback(world["root"], "set-1")
    body = world["client"].post("/registry/reload").json()
    assert body["registry_label"] == "set-1"
    assert body["changed"]["cells"] == [f"{MNO_A}__{R1}"] and svc._cells[A1].db_path.endswith("-v1.db")
    assert _versions(world["client"].get("/healthz").json(), "history") == {MNO_A: 1, MNO_B: 1}


def test_reload_reports_unopened_stores_as_unchanged(world):
    """Trees and History open lazily; a reload before anything queried
    them must not list every unit as changed (field-found: a flip's
    "0 changed" check read it as change)."""
    _publish(world, "set-1", base="none")
    _flip(world, "set-1")
    svc._load_cells()                                        # no healthz, no store opened
    assert "trees" not in svc._STORES and "trees" not in svc._STORES_PATHS
    body = world["client"].post("/registry/reload").json()
    assert body["changed"] == {"cells": [], "trees": [], "history": []}
    body = world["client"].post("/registry/reload").json()
    assert body["changed"] == {"cells": [], "trees": [], "history": []}
    # the lazy open serves the current set, and a real change is still reported
    assert len(world["client"].get("/stores/trees/v1/cells").json()) == 3
    write_parse(world["env"], MNO_B, R1, "docB", [_req("REQ_B_1", section="1"), _req("REQ_B_2", "REQ_B_1")])
    _publish(world, "set-2")
    _flip(world, "set-2")
    body = world["client"].post("/registry/reload").json()
    # Trees was opened above, so its change is reported; History never was
    assert body["changed"] == {"cells": [], "trees": [f"{MNO_B}__{R1}"], "history": []}
    assert world["client"].get("/stores/history/v1/mnos").json() == [MNO_A, MNO_B]
    body = world["client"].post("/registry/reload").json()
    assert body["changed"] == {"cells": [], "trees": [], "history": []}


def test_reload_drops_cells_that_left_the_set(world):
    _publish(world, "set-1", base="none")
    _flip(world, "set-1")
    svc._load_cells()
    svc._label_cells[(A2, "lbl")] = svc._cells[A2]
    # a set built from scratch with one cell only
    _publish(world, "set-3", base="none", cells_filter=[f"{MNO_B}__{R1}"])
    _flip(world, "set-3")
    body = world["client"].post("/registry/reload").json()
    assert body["removed"] == [f"{MNO_A}__{R2}", f"{MNO_A}__{R1}"]
    assert body["changed"]["cells"] == [] and body["cells"] == [f"{MNO_B}__{R1}"]
    assert sorted(svc._cells) == [B1] and svc._label_cells == {}
    hz = world["client"].get("/healthz").json()
    assert list(hz["registry_versions"]["cells"]) == [f"{MNO_B}__{R1}"]
    assert [(c["mno"], c["release"]) for c in world["client"].get("/stores/trees/v1/cells").json()] == [B1]


def test_release_set_mode_serves_a_staged_set(world, monkeypatch):
    _publish(world, "set-1", base="none")
    _flip(world, "set-1")
    make_cell_db(world["cells"] / f"cell-{MNO_A}-{R1}.db", MNO_A, R1, "fp-a1-STAGED")
    _publish(world, "set-2")                                # staged, not flipped
    monkeypatch.setattr(svc, "_RELEASE_SET", "set-2")
    svc._load_cells()
    assert svc._cells[A1].db_path.endswith("-v2.db")
    hz = world["client"].get("/healthz").json()
    assert hz["registry_label"] == "set-2" and hz["registry_release_set"] == "set-2"
    assert hz["data_fingerprint_cells"][f"{MNO_A}__{R1}"] == "fp-a1-STAGED"
    body = world["client"].post("/registry/reload").json()
    assert body["release_set"] == "set-2" and body["changed"]["cells"] == []


def test_registry_errors_are_reported_not_fatal(world, monkeypatch):
    monkeypatch.setattr(svc, "_REGISTRY", str(world["root"] / "missing" / "registry.db"))
    svc._load_cells()
    assert svc._cells == {} and "registry" in (svc._cells_load_error or "")
    hz = world["client"].get("/healthz").json()
    assert hz["ok"] is False and hz["registry_label"] == ""
    assert hz["stores"]["trees"] == "local"                 # empty store, not a crash
    assert world["client"].post("/registry/reload").status_code == 503
    monkeypatch.setattr(svc, "_REGISTRY", "")
    assert world["client"].post("/registry/reload").status_code == 404
    # a registry whose active set is empty: the service says so
    _publish(world, "set-1", base="none")
    monkeypatch.setattr(svc, "_REGISTRY", str(registry_path(world["root"])))
    svc._cells_load_error = None
    svc._load_cells()
    assert "no cells" in (svc._cells_load_error or "")


def test_store_started_before_first_flip_comes_up_whole_on_reload(world, monkeypatch):
    """Field-found on the one-time move to the registry: a store started
    with no active set must still load its prompts and must not report
    the legacy corpus error; the first reload brings the cells in and
    leaves healthz whole."""
    loads: list[str] = []

    def fake_load_prompts(base):
        loads.append(str(base))
        svc._query_prompt_template = "Q {query}"
        svc._rerank_prompt_template = "R {query} {document}"
    monkeypatch.setattr(svc, "_load_prompts", fake_load_prompts)
    monkeypatch.setattr(svc, "_query_prompt_template", "")
    monkeypatch.setattr(svc, "_rerank_prompt_template", "")
    monkeypatch.setattr(svc, "_load_error", None)
    monkeypatch.setattr(svc, "_QUERY_ENRICH_ENABLED", True)
    monkeypatch.setattr(svc, "_RERANK_ENABLED", True)
    monkeypatch.setattr(svc, "_RERANK_BACKEND", "shim")
    monkeypatch.setenv("NORA_SIRA_PROMPTS_FROM", str(world["work"]))
    _publish(world, "set-1", base="none")                  # staged, nothing active

    svc._startup()
    assert svc._cells == {} and loads == [str(world["work"])]   # prompts loaded anyway
    assert svc._load_error is None                              # no legacy corpus error
    hz = world["client"].get("/healthz").json()
    assert hz["missing_prompts"] == [] and hz["query_prompt_loaded"] is True
    assert hz["ok"] is False and "no cells" in hz["cells_load_error"]

    # the same situation when prompts were NOT loaded at startup (older
    # image): the first reload repairs it
    monkeypatch.setattr(svc, "_query_prompt_template", "")
    monkeypatch.setattr(svc, "_rerank_prompt_template", "")
    monkeypatch.setattr(svc, "_load_error", "corpus.jsonl not found")
    _flip(world, "set-1")
    body = world["client"].post("/registry/reload").json()
    assert len(body["changed"]["cells"]) == 3
    assert body["prompts_loaded"] is True and body["missing_prompts"] == []
    assert len(loads) == 2
    hz = world["client"].get("/healthz").json()
    assert hz["ok"] is True and hz["load_error"] is None and hz["cells_load_error"] is None
    assert hz["registry_label"] == "set-1" and len(hz["cells"]) == 3
    # a later reload does not reload prompts
    world["client"].post("/registry/reload")
    assert len(loads) == 2


def test_explicit_trees_root_wins_over_registry(world, monkeypatch, tmp_path):
    _publish(world, "set-1", base="none")
    _flip(world, "set-1")
    other = tmp_path / "other-trees"
    other.mkdir()
    monkeypatch.setattr(svc, "_TREES_ROOT", str(other))
    svc._load_cells()
    assert world["client"].get("/stores/trees/v1/cells").json() == []
    assert trees_db_name(MNO_A, R1) not in [p.name for p in other.iterdir()]
    assert world["client"].get("/stores/history/v1/mnos").json() == [MNO_A, MNO_B]


def test_prompt_scheme_comes_from_the_served_release_set(world, monkeypatch):
    monkeypatch.delenv("SIRA_PROMPT_SCHEME", raising=False)
    _publish(world, "set-1", base="none", prompt_scheme="scheme-one")
    _flip(world, "set-1")
    svc._load_cells()
    hz = world["client"].get("/healthz").json()
    assert hz["sira_prompt_scheme"] == "scheme-one"
    make_cell_db(world["cells"] / f"cell-{MNO_A}-{R1}.db", MNO_A, R1, "fp-a1-2")
    _publish(world, "set-2", prompt_scheme="scheme-two")
    _flip(world, "set-2")
    world["client"].post("/registry/reload")
    assert world["client"].get("/healthz").json()["sira_prompt_scheme"] == "scheme-two"
    monkeypatch.setenv("SIRA_PROMPT_SCHEME", "env-wins")      # env override stays first
    assert world["client"].get("/healthz").json()["sira_prompt_scheme"] == "env-wins"
