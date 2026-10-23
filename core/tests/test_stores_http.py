"""Tests for the store HTTP clients + nora-store's store routes (strand
corp-db-layer S6): every protocol method answers the same over
`/stores/<store>/v1` as the local backend, Overlay writes round-trip
through the server's `edit` route, the factories pick the client from
`NORA_<STORE>_URL`, and a dead service surfaces as `StoreUnavailable`.

Routers are mounted on a minimal FastAPI app over injected local stores
(no bm25x needed); the clients talk to it through the in-process
TestClient. Synthetic data only — generic ids and release labels.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.src.stores.overlay_ops as ops
from core.src.stores import history_build, trees_build
from core.src.stores.clients import (
    HttpHistoryStore,
    HttpOverlayStore,
    HttpTreeStore,
    store_backend_kind,
)
from core.src.stores.errors import StoreUnavailable
from core.src.stores.history import (
    HistoryStore,
    SqliteHistoryStore,
    open_history_store,
)
from core.src.stores.overlay import (
    JsonOverlayStore,
    OverlayReadOnlyError,
    OverlayStore,
    SqliteOverlayStore,
    open_overlay_store,
)
from core.src.stores.trees import FileTreeStore, SqliteTreeStore, TreeStore, open_tree_store
from core.src.web.enrich_overlay_store import EnrichOverlayStore
from core.tests.test_stores_history import MNO as H_MNO
from core.tests.test_stores_history import RELS as H_RELS
from core.tests.test_stores_history import _corpus as history_corpus
from core.tests.test_stores_overlay import _FixedClock
from core.tests.test_stores_trees import CELLS, IDS
from core.tests.test_stores_trees import _corpus as trees_corpus
from core.src.stores import api, serve


def _app(**stores) -> TestClient:
    """A minimal app hosting the generic routers over the given local
    stores (the way nora-store mounts them: getters, lazily resolved)."""
    app = FastAPI()
    app.include_router(api.trees_router(lambda: stores.get("trees")))
    app.include_router(api.history_router(lambda: stores.get("history")))
    app.include_router(api.overlay_router(
        lambda: stores.get("overlay"),
        lambda: api.is_overlay_authority(stores.get("overlay"))))
    return TestClient(app)


# ── trees ───────────────────────────────────────────────────────────


@pytest.fixture()
def trees(tmp_path):
    env, out = tmp_path / "env", tmp_path / "trees"
    trees_corpus(env)
    assert trees_build.main(["--env-dir", str(env), "--out", str(out)]) == 0
    local = SqliteTreeStore(out)
    http = HttpTreeStore("http://testserver", client=_app(trees=local))
    return local, http, env


def test_trees_parity_every_method(trees):
    s, h, _ = trees
    assert isinstance(h, TreeStore)
    assert s.list_cells() == h.list_cells()
    assert s.list_docs() == h.list_docs()
    for mno in ("mno-a", "mno-b", "mno-none"):
        assert s.plans_for_mno(mno) == h.plans_for_mno(mno)
    for mno, rel in CELLS + (("mno-a", "Jul2031"),):
        assert s.list_docs(mno, rel) == h.list_docs(mno, rel)
        for doc in ("docA", "docB", "nope"):
            assert s.load_tree(doc, mno, rel) == h.load_tree(doc, mno, rel)
        for plan in ("PLAN_X", "PLAN_Z", "", "PLAN_NONE"):
            assert s.reqs_for_plan(mno, rel, plan) == h.reqs_for_plan(mno, rel, plan)
        for rid in IDS:
            for ic in (False, True):
                assert s.find_req(rid.lower(), mno, rel, ic) == \
                    h.find_req(rid.lower(), mno, rel, ic)
            for op in ("get_node", "children", "subtree", "ancestors"):
                assert getattr(s, op)(mno, rel, rid) == getattr(h, op)(mno, rel, rid)
    for rid in IDS:
        assert s.find_req(rid) == h.find_req(rid)
    row = s.find_req("REQ_C_0001")[0]
    from core.src.models.fingerprint import text_fp
    fp = text_fp(row["title"], row["text"])
    for mno in ("", "mno-a", "mno-b"):
        assert s.find_by_fingerprint(fp, mno) == h.find_by_fingerprint(fp, mno)


def test_trees_file_backend_served_too(trees):
    """The server may host the parse-tree file backend (NORA_ENV_DIR)."""
    _, _, env = trees
    f = FileTreeStore(env)
    h = HttpTreeStore("http://testserver", client=_app(trees=f))
    assert h.list_cells() == f.list_cells()
    assert h.get_node("mno-a", "Jan2031", "REQ_A_0002") == \
        f.get_node("mno-a", "Jan2031", "REQ_A_0002")


# ── history ─────────────────────────────────────────────────────────


@pytest.fixture()
def history(tmp_path):
    env, out = tmp_path / "env", tmp_path / "history"
    history_corpus(env)
    assert history_build.main(["--env-dir", str(env), "--out", str(out)]) == 0
    local = SqliteHistoryStore(out)
    return local, HttpHistoryStore("http://testserver", client=_app(history=local))


def test_history_parity_every_method(history):
    s, h = history
    assert isinstance(h, HistoryStore)
    assert s.list_mnos() == h.list_mnos()
    for mno in (H_MNO, "mno-none"):
        assert s.releases(mno) == h.releases(mno)
        for plan in ("PLANA", "PLANB", "PLAN_NONE"):
            for rid in ("R1", "R3", "R3X", "R9", "nope"):
                assert s.req_history(mno, plan, rid) == h.req_history(mno, plan, rid)
                assert s.req_history(mno, plan, rid, "Apr2031", "Jul2031") == \
                    h.req_history(mno, plan, rid, "Apr2031", "Jul2031")
                assert s.last_changed(mno, plan, rid) == h.last_changed(mno, plan, rid)
            assert s.plan_history(mno, plan) == h.plan_history(mno, plan)
            assert s.plan_last_changed(mno, plan) == h.plan_last_changed(mno, plan)
        for diffs in (False, True):
            assert s.latest_changes(mno, diffs) == h.latest_changes(mno, diffs)
    for a, b in (("Jan2031", "Oct2031"), ("", ""), ("Jul2031", "Jul2031")):
        for diffs in (False, True):
            assert s.diff_releases(H_MNO, a, b, diffs) == h.diff_releases(H_MNO, a, b, diffs)


def test_history_errors_cross_the_wire(history):
    s, h = history
    with pytest.raises(ValueError):
        s.diff_releases(H_MNO, "Jan2099", "")
    with pytest.raises(ValueError):            # 400 → ValueError
        h.diff_releases(H_MNO, "Jan2099", "")
    with pytest.raises(ValueError):
        h.diff_releases("mno-none", "", "")
    none = HttpHistoryStore("http://testserver", client=_app())
    with pytest.raises(StoreUnavailable):      # no history root → 404
        none.list_mnos()


# ── overlay ─────────────────────────────────────────────────────────


@pytest.fixture()
def overlay(tmp_path, monkeypatch):
    """(server-side SQLite backend, HTTP-backed web store, JSON-backed
    local web store) under one frozen clock, so the authority and the
    local path stamp identically."""
    monkeypatch.setattr(ops, "datetime", _FixedClock)
    server = SqliteOverlayStore(tmp_path / "ovl.db")
    http = HttpOverlayStore("http://testserver", client=_app(overlay=server))
    web_http = EnrichOverlayStore("", backend=http)
    web_local = EnrichOverlayStore(tmp_path / "corr")
    assert web_http.remote and not web_local.remote
    return server, web_http, web_local


def _stamp(**kw):
    kw.setdefault("label", "c1")
    kw.setdefault("reason", {"category": "too-generic", "note": "n"})
    kw.setdefault("by", "expert")
    kw.setdefault("origin_release", "Jan2031")
    return kw


def _assert_overlay_parity(a: EnrichOverlayStore, b: EnrichOverlayStore):
    assert a.list_mnos() == b.list_mnos()
    for mno in ("GP", "XB", "none"):
        assert a.get_overlay(mno) == b.get_overlay(mno)
        for rid in ("R1", "R2", "R3", "nope"):
            assert a.get_entry(mno, rid) == b.get_entry(mno, rid)
        for label in ("", "c1", "c2"):
            assert a.overlay_digest(mno, label) == b.overlay_digest(mno, label)
    assert a.accepted_labels() == b.accepted_labels()
    assert a.disabled_labels() == b.disabled_labels()
    assert a.label_counts() == b.label_counts()
    assert a.label_req_counts(["GP"]) == b.label_req_counts(["GP"])
    assert a.reason_categories() == b.reason_categories()


def test_overlay_edits_round_trip_through_the_server(overlay):
    server, web_http, web_local = overlay
    assert isinstance(web_http.backend, OverlayStore)
    _assert_overlay_parity(web_http, web_local)
    steps = [
        ("GP", "R1", "remove", dict(words=["handover", "retry"], fingerprint="fp1")),
        ("GP", "R1", "add", dict(words=["t3402", "t3410", "t3402"])),
        ("GP", "R1", "remove", dict(words=["handover"], label="c2")),
        ("GP", "R2", "suppress", dict()),
        ("XB", "R3", "add", dict(words=["roaming"], label="")),
        ("GP", "R1", "reaffirm", dict(pairs=[{"direction": "remove", "word": "retry"}],
                                      origin_release="Apr2031", fingerprint="fp2")),
        ("GP", "R1", "discard", dict(pairs=[{"direction": "add", "word": "t3410"}])),
        ("GP", "R2", "unsuppress", dict()),
        ("GP", "R1", "unremove", dict(words=["handover"])),
    ]
    for mno, rid, op, kw in steps:
        r1 = web_http.edit(mno, rid, op, **_stamp(**kw))
        r2 = web_local.edit(mno, rid, op, **_stamp(**kw))
        assert r1 == r2, op
        _assert_overlay_parity(web_http, web_local)
    assert web_http.set_label_merged("c1", True) == web_local.set_label_merged("c1", True)
    assert web_http.set_label_disabled("c2", True) == web_local.set_label_disabled("c2", True)
    assert web_http.add_reason_category("new-cat") == web_local.add_reason_category("new-cat")
    _assert_overlay_parity(web_http, web_local)
    assert web_http.delete_label("c2") == web_local.delete_label("c2") == 0  # already undone
    assert web_http.delete_label("c1") == web_local.delete_label("c1") > 0
    assert web_http.set_label_merged("c1", False) == web_local.set_label_merged("c1", False)
    _assert_overlay_parity(web_http, web_local)
    # the server's own backend holds exactly what the client reads
    assert server.get_overlay("GP") == web_http.get_overlay("GP")
    assert server.list_mnos() == ["GP", "XB"]
    assert server.version("GP") > 0 and web_http.overlay_mtime("GP") == server.version("GP")


def test_overlay_raw_writes_and_unknown_ops(overlay):
    server, web_http, _ = overlay
    h = web_http.backend
    entry = {"add": [{"word": "w", "label": "", "reason": {}, "by": "", "at": "",
                      "origin": {"release": ""}}]}
    h.put_entry("GP", "R9", entry)
    assert server.get_entry("GP", "R9") == h.get_entry("GP", "R9")
    h.put_entry("GP", "R9", None)
    assert h.get_entry("GP", "R9") is None
    h.set_accepted_labels({"b", "a"})
    h.set_disabled_labels({"z"})
    h.set_reason_categories(["x", "y"])
    assert (h.accepted_labels(), h.disabled_labels(), h.reason_categories()) == \
        (server.accepted_labels(), server.disabled_labels(), server.reason_categories()) == \
        ({"a", "b"}, {"z"}, ["x", "y"])
    with pytest.raises(ValueError):
        h.run_op("nope", {})
    with pytest.raises(ValueError):
        web_http.edit("GP", "R1", "explode")
    with pytest.raises(ValueError):           # the server validates the op too
        h.run_op("edit", {"mno": "GP", "req_id": "R1", "op": "explode"})


def test_overlay_write_refused_when_not_the_authority(tmp_path):
    # peer read: the server's overlay store is itself an HTTP client
    authority = SqliteOverlayStore(tmp_path / "a.db")
    peer_client = HttpOverlayStore("http://testserver", client=_app(overlay=authority),
                                   read_only=True)
    via_peer = HttpOverlayStore("http://testserver", client=_app(overlay=peer_client))
    authority.put_entry("GP", "R1", {"add": [{"word": "w"}]})
    assert via_peer.get_overlay("GP") == authority.get_overlay("GP")
    assert via_peer.digest("GP") == peer_client.digest("GP")
    with pytest.raises(OverlayReadOnlyError):
        via_peer.put_entry("GP", "R2", {"add": [{"word": "x"}]})
    with pytest.raises(OverlayReadOnlyError):
        EnrichOverlayStore("", backend=via_peer).edit("GP", "R2", "add", words=["x"])
    assert not api.is_overlay_authority(peer_client)
    assert api.is_overlay_authority(authority)
    # a read-only local open is not an authority either
    ro = SqliteOverlayStore(tmp_path / "a.db", read_only=True)
    assert not api.is_overlay_authority(ro)
    with pytest.raises(OverlayReadOnlyError):
        HttpOverlayStore("http://testserver", client=_app(overlay=ro)).set_accepted_labels(set())
    # a client opened read-only refuses locally, before any request
    with pytest.raises(OverlayReadOnlyError):
        peer_client.put_entry("GP", "R3", None)
    with pytest.raises(OverlayReadOnlyError):
        with peer_client.transaction():
            pass


# ── factories, kinds, unavailability ───────────────────────────────


def test_factories_pick_the_client_from_env(tmp_path, monkeypatch):
    assert isinstance(open_overlay_store(tmp_path), JsonOverlayStore)
    assert isinstance(open_tree_store(tmp_path), FileTreeStore)
    assert open_history_store() is None
    monkeypatch.setenv("NORA_OVERLAY_URL", "http://example.test:8040/")
    monkeypatch.setenv("NORA_TREES_URL", "http://example.test:8040")
    monkeypatch.setenv("NORA_HISTORY_URL", "http://example.test:8040")
    o, t, h = open_overlay_store(tmp_path), open_tree_store(tmp_path), open_history_store()
    assert isinstance(o, HttpOverlayStore) and o.base_url == "http://example.test:8040"
    assert isinstance(t, HttpTreeStore) and isinstance(h, HttpHistoryStore)
    assert (store_backend_kind(o), store_backend_kind(t), store_backend_kind(None)) == \
        ("http", "http", "off")
    assert store_backend_kind(JsonOverlayStore(tmp_path)) == "local"
    # url="" ignores the env var (the service's own local open)
    assert isinstance(open_overlay_store(tmp_path, url=""), JsonOverlayStore)
    assert isinstance(open_tree_store(tmp_path, url=""), FileTreeStore)
    assert open_history_store(url="") is None
    # EnrichOverlayStore is enabled with a URL and no corrections root
    assert EnrichOverlayStore("").remote


def test_dead_service_is_store_unavailable():
    # an unroutable address, short timeout: connection error → StoreUnavailable
    t = HttpTreeStore("http://127.0.0.1:9", timeout=0.5)
    with pytest.raises(StoreUnavailable):
        t.list_cells()
    o = HttpOverlayStore("http://127.0.0.1:9", timeout=0.5)
    with pytest.raises(StoreUnavailable):
        o.get_overlay("GP")
    # the web store does not mask it: callers decide how to degrade
    with pytest.raises(StoreUnavailable):
        EnrichOverlayStore("", backend=o).label_counts()


def test_web_tree_store_keys_on_url(tmp_path, monkeypatch):
    from core.src.web import req_tree
    a = req_tree.tree_store(tmp_path)
    assert isinstance(a, FileTreeStore) and req_tree.tree_store(tmp_path) is a
    monkeypatch.setenv("NORA_TREES_URL", "http://example.test:8040")
    b = req_tree.tree_store(tmp_path)
    assert isinstance(b, HttpTreeStore) and b is not a


# ── standalone host (core.src.stores.serve) ────────────────────────


def test_standalone_trees_only_host_has_parity(trees, monkeypatch):
    s, _, env = trees
    monkeypatch.setenv("NORA_TREES_ROOT", str(s.root))
    monkeypatch.setenv("NORA_ENV_DIR", str(env))
    app = serve.build_app(["trees"])                 # opened from the env
    c = TestClient(app)
    assert c.get("/healthz").json() == {"ok": True, "hosted": ["trees"],
                                        "stores": {"trees": "local"}}
    assert c.get("/stores/overlay/v1/mnos").status_code == 404   # not hosted
    assert c.get("/stores/history/v1/mnos").status_code == 404
    h = HttpTreeStore("http://testserver", client=c)
    assert h.list_cells() == s.list_cells()
    for mno, rel in CELLS:
        assert h.list_docs(mno, rel) == s.list_docs(mno, rel)
        for rid in IDS:
            assert h.find_req(rid, mno, rel, True) == s.find_req(rid, mno, rel, True)
            for op in ("get_node", "children", "subtree", "ancestors"):
                assert getattr(h, op)(mno, rel, rid) == getattr(s, op)(mno, rel, rid)
    assert h.plans_for_mno("mno-a") == s.plans_for_mno("mno-a")
    assert h.load_tree("docA", "mno-a", "Jan2031") == s.load_tree("docA", "mno-a", "Jan2031")
    with pytest.raises(ValueError):
        serve.build_app(["cells"])                   # cells stay with nora-store


def test_standalone_overlay_host_not_writable_answers_409(tmp_path):
    local = SqliteOverlayStore(tmp_path / "ovl.db")
    local.put_entry("GP", "R1", {"add": [{"word": "w"}]})
    c = TestClient(serve.build_app({"overlay": local}, overlay_writable=False))
    assert c.get("/healthz").json() == {"ok": True, "hosted": ["overlay"],
                                        "stores": {"overlay": "local"},
                                        "overlay_authority": False}
    h = HttpOverlayStore("http://testserver", client=c)
    assert h.get_overlay("GP") == local.get_overlay("GP")
    assert c.get("/stores/trees/v1/cells").status_code == 404
    r = c.put("/stores/overlay/v1/labels/accepted", json={"labels": ["x"]})
    assert r.status_code == 409 and "authority" in r.json()["detail"]
    with pytest.raises(OverlayReadOnlyError):
        h.run_op("delete_label", {"label": "x"})
    assert local.get_overlay("GP") == {"R1": {"add": [{"word": "w"}]}}
    # the same host, writable (the authority), takes the edit
    w = HttpOverlayStore("http://testserver",
                         client=TestClient(serve.build_app({"overlay": local})))
    assert EnrichOverlayStore("", backend=w).delete_label("") == 1
    assert local.get_overlay("GP") == {}
