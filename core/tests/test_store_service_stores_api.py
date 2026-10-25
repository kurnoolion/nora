"""The store routes mounted on the service app (strand corp-db-layer S6):
the Cells routes answer from the loaded `_cells` state, /healthz reports
each hosted store's backend, and the Overlay routes write through the
service's own backend. bm25x is faked (synthetic cells)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import core.src.store_service.service as svc
from core.src.store_service.service import CellState
from core.tests.test_store_service_service_multi_cell import _FakeBM25

GP_J = ("GP", "Jan2031")
GP_A = ("GP", "Apr2031")


def _cell(cellkey, docs, composed=True):
    doc_ids = [d[0] for d in docs]
    st = CellState(
        cell=cellkey, bm25=_FakeBM25(len(docs)), doc_ids=doc_ids,
        doc_id_to_idx={d: i for i, d in enumerate(doc_ids)},
        corpus_by_id={d[0]: {"title": d[1], "text": d[2]} for d in docs},
        max_df=100, engine="bm25x", composed=composed,
        db_meta={"schema_version": "2", "enrich_run": "run-1"},
    )
    st.llm_words = {"req:a": ["handover", "retry"], "req:b": ["roaming"]}
    st.effective_words = {"req:a": ["retry", "t3402"]}
    st.held_by_id = {"req:b": [{"direction": "add", "word": "roaming", "origin": {"release": "Jan2031"}}]}
    st.suppressed_ids = {"req:c"}
    st.fingerprints = {"req:a": "fp-a"}
    st.loaded_at = 1.0
    st.overlay_digest = "d1"
    st.overlay_applied = {"removes": 1, "adds": 1, "suppressed": 1, "held": 1}
    return st


@pytest.fixture()
def client(monkeypatch, tmp_path):
    cells = {GP_J: _cell(GP_J, [("req:a", "A", "x"), ("req:b", "B", "y"), ("req:c", "C", "z")]),
             GP_A: _cell(GP_A, [("req:a", "A", "x2")])}
    monkeypatch.setattr(svc, "_cells", cells)
    monkeypatch.setattr(svc, "_label_cells", {})
    monkeypatch.setattr(svc, "_OVERLAY_URL", "")
    monkeypatch.setattr(svc, "_OVERLAY_DB", str(tmp_path / "ovl.db"))
    monkeypatch.setattr(svc, "_CORR_ROOT", "")
    monkeypatch.setattr(svc, "_STORES", {})
    monkeypatch.setattr(svc, "_ENV_DIR", "")
    monkeypatch.setattr(svc, "_TREES_ROOT", "")
    monkeypatch.setattr(svc, "_HISTORY_ROOT", "")
    return TestClient(svc.app)


def test_cells_routes_mirror_state(client):
    st = svc._cells[GP_J]
    cells = client.get("/stores/cells/v1/cells").json()
    assert [(c["mno"], c["release"], c["requirements"]) for c in cells] == \
        [("GP", "Apr2031", 1), ("GP", "Jan2031", 3)]
    assert cells[1] == {"mno": "GP", "release": "Jan2031", "requirements": 3,
                        "loaded_at": 1.0, "engine": "bm25x", "composed": True,
                        "schema_version": "2"}
    meta = client.get("/stores/cells/v1/cells/GP/Jan2031/meta").json()
    assert meta["db_meta"] == st.db_meta and meta["overlay_digest"] == "d1"
    assert meta["corrections"] == st.overlay_applied and meta["fingerprints"] == 1
    rows = client.get("/stores/cells/v1/cells/GP/Jan2031/rows").json()
    assert rows == [{"req_id": "req:a", "title": "A", "text": "x", "fingerprint": "fp-a"},
                    {"req_id": "req:b", "title": "B", "text": "y", "fingerprint": ""},
                    {"req_id": "req:c", "title": "C", "text": "z", "fingerprint": ""}]
    one = client.get("/stores/cells/v1/cells/GP/Jan2031/rows",
                     params=[("req_id", "req:b"), ("req_id", "nope")]).json()
    assert one == rows[1:2]
    assert client.get("/stores/cells/v1/cells/GP/Jan2031/phrases").json() == st.llm_words
    assert client.get("/stores/cells/v1/cells/GP/Jan2031/phrases",
                      params={"req_id": "req:c"}).json() == {"req:c": []}
    eff = client.get("/stores/cells/v1/cells/GP/Jan2031/effective").json()
    assert eff == {"req:a": {"effective": ["retry", "t3402"], "held": [], "suppressed": False},
                   "req:b": {"effective": [], "held": st.held_by_id["req:b"], "suppressed": False},
                   "req:c": {"effective": [], "held": [], "suppressed": True}}
    assert client.get("/stores/cells/v1/cells/GP/Jan2099/meta").status_code == 404
    # no index blobs are served
    assert client.get("/stores/cells/v1/cells/GP/Jan2031/index").status_code == 404


def test_healthz_reports_store_backends(client):
    body = client.get("/healthz").json()
    assert body["stores"] == {"overlay": "local", "trees": "off", "history": "off"}
    assert body["overlay_authority"] is True
    assert client.get("/stores/trees/v1/cells").status_code == 404
    assert client.get("/stores/history/v1/mnos").status_code == 404


def test_edit_route_validates_its_body(client):
    post = lambda body: client.post("/stores/overlay/v1/edit", json=body)  # noqa: E731
    assert post({"op": "explode"}).status_code == 400          # unknown store op
    assert post({"op": "edit", "args": []}).status_code == 400  # args not an object
    r = post({"op": "edit", "args": {"mno": "GP", "req_id": "req:a", "op": "explode"}})
    assert r.status_code == 400 and "unknown op" in r.json()["detail"]
    r = post({"op": "edit", "args": {"mno": "GP", "req_id": "req:a", "op": "add",
                                     "words": ["w"]}})
    assert r.status_code == 200 and r.json()["add"][0]["word"] == "w"


def test_edit_route_runs_the_op(client, tmp_path, monkeypatch):
    from core.src.stores.overlay import SqliteOverlayStore
    from core.src.stores.clients import HttpOverlayStore
    from core.src.web.enrich_overlay_store import EnrichOverlayStore
    web = EnrichOverlayStore("", backend=HttpOverlayStore("http://testserver", client=client))
    entry = web.edit("GP", "req:a", "remove", words=["handover"], label="c1",
                     by="expert", origin_release="Jan2031", fingerprint="fp-a")
    assert entry["remove"][0]["word"] == "handover"
    local = SqliteOverlayStore(tmp_path / "ovl.db", read_only=True)
    assert local.get_overlay("GP") == web.get_overlay("GP")
    assert web.overlay_digest("GP", "c1") == svc._overlay_digest(
        {"req:a": local.get_entry("GP", "req:a")})
    # peer mode: this service is not the authority → 409
    monkeypatch.setattr(svc, "_OVERLAY_URL", "http://example.test:8040")
    assert client.get("/healthz").json()["overlay_authority"] is False
    r = client.put("/stores/overlay/v1/labels/accepted", json={"labels": ["c1"]})
    assert r.status_code == 409 and "authority" in r.json()["detail"]
    r = client.post("/stores/overlay/v1/edit", json={"op": "delete_label", "label": "c1"})
    assert r.status_code == 409
