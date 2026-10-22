"""Tests for core/src/stores/trees.py + trees_build.py (strand corp-db-layer
S4): File/SQLite backend parity for every TreeStore method, traversal,
exact load_tree round-trip, the builder CLI and backend selection.

Synthetic trees only — generic placeholder ids and release labels.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.src.models.fingerprint import text_fp
from core.src.pipeline.error_codes import CODES
from core.src.stores import trees_build
from core.src.stores.trees import (
    TREES_ROOT_ENV,
    FileTreeStore,
    SqliteTreeStore,
    TreeStore,
    open_tree_store,
    read_meta,
    tree_layout,
)
from core.src.web import req_tree


def _req(rid, parent="", title="", text="", **kw):
    r = {"req_id": rid, "section_number": kw.pop("section_number", ""),
         "title": title or f"title {rid}", "text": text or f"body of {rid}",
         "parent_req_id": parent, "parent_section": kw.pop("parent_section", ""),
         "hierarchy_path": [], "is_requirement": bool(rid), "tables": []}
    r.update(kw)
    return r


def _write(env: Path, mno: str, rel: str, doc: str, tree: dict) -> None:
    d = env / "out" / "parse" / mno / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{doc}_tree.json").write_text(json.dumps(tree, indent=2), encoding="utf-8")


def _tree(plan_id, plan_name, reqs, **extra):
    # `requirements` deliberately not last: key order must survive.
    return {"mno": "mno-a", "release": "", "plan_id": plan_id,
            "plan_name": plan_name, "version": "1", "detection_mode": "heading",
            "requirements": reqs,
            "definitions_map": {"ABC": "a b c"}, "build_context_max_chars": 0,
            **extra}


def _corpus(env: Path) -> None:
    # Nested chain 1 > 2 > 3 > 4, a sibling, a table-anchored req.
    _write(env, "mno-a", "Jan2031", "docA", _tree("PLAN_X", "Plan Ex", [
        _req("REQ_A_0001", section_number="1"),
        _req("REQ_A_0002", "REQ_A_0001", section_number="1.1"),
        _req("REQ_A_0003", "REQ_A_0002", section_number="1.1.1",
             tables=[{"headers": ["k", "v"], "rows": [["a", "1"]]}]),
        _req("REQ_A_0004", "REQ_A_0003"),
        _req("REQ_A_0005", "REQ_A_0001", section_number="1.2"),
        _req("REQ_A_0006", section_number="", parent_section="1.2"),
        _req("", title="structural", text="heading only"),
    ]))
    # Multi-plan doc in the same cell: secondary plan -> empty plan_name.
    _write(env, "mno-a", "Jan2031", "docB", _tree("PLAN_Y", "Whole Doc", [
        _req("REQ_B_0001", plan_id="PLAN_Y"),
        _req("REQ_B_0002", "REQ_B_0001", plan_id="PLAN_Z"),
        _req("Req_Mixed_0001", plan_id="PLAN_Z"),
    ]))
    # Same ids in a later release; one body changed.
    _write(env, "mno-a", "Apr2031", "docA", _tree("PLAN_X", "Plan Ex", [
        _req("REQ_A_0001", section_number="1"),
        _req("REQ_A_0002", "REQ_A_0001", text="changed body"),
    ]))
    # Second MNO; same title/text as a mno-a req (fingerprint across cells).
    _write(env, "mno-b", "Jan2031", "docC", _tree("PLAN_Q", "", [
        _req("REQ_C_0001", title="title REQ_A_0001", text="body of REQ_A_0001"),
    ]))


@pytest.fixture()
def stores(tmp_path):
    env, out = tmp_path / "env", tmp_path / "trees"
    _corpus(env)
    assert trees_build.main(["--env-dir", str(env), "--out", str(out)]) == 0
    return FileTreeStore(env), SqliteTreeStore(out), env, out


CELLS = (("mno-a", "Jan2031"), ("mno-a", "Apr2031"), ("mno-b", "Jan2031"))
IDS = ("REQ_A_0001", "REQ_A_0002", "REQ_A_0003", "REQ_A_0004", "REQ_A_0006",
       "REQ_B_0001", "REQ_B_0002", "Req_Mixed_0001", "REQ_C_0001", "", "NOPE")


def test_both_backends_satisfy_protocol(stores):
    f, s, _, _ = stores
    assert isinstance(f, TreeStore) and isinstance(s, TreeStore)


def test_parity_every_method(stores):
    f, s, _, _ = stores
    assert f.list_cells() == s.list_cells() == [
        {"mno": "mno-a", "release": "Jan2031"},
        {"mno": "mno-a", "release": "Apr2031"},
        {"mno": "mno-b", "release": "Jan2031"},
    ]
    assert f.list_docs() == s.list_docs() == ["docA", "docA", "docB", "docC"]
    for mno in ("mno-a", "mno-b", "mno-none"):
        assert f.plans_for_mno(mno) == s.plans_for_mno(mno)
    for mno, rel in CELLS + (("mno-a", "Jul2031"),):
        assert f.list_docs(mno, rel) == s.list_docs(mno, rel)
        for doc in ("docA", "docB", "docC", "nope"):
            assert f.load_tree(doc, mno, rel) == s.load_tree(doc, mno, rel)
        for plan in ("PLAN_X", "PLAN_Y", "PLAN_Z", "PLAN_Q", "", "PLAN_NONE"):
            assert f.reqs_for_plan(mno, rel, plan) == s.reqs_for_plan(mno, rel, plan)
        for rid in IDS:
            for q in (rid, rid.lower()):
                for ic in (False, True):
                    assert f.find_req(q, mno, rel, ic) == s.find_req(q, mno, rel, ic)
            for op in ("get_node", "children", "subtree", "ancestors"):
                assert getattr(f, op)(mno, rel, rid) == getattr(s, op)(mno, rel, rid)
    for rid in IDS:
        assert f.find_req(rid) == s.find_req(rid)
        assert f.find_req(rid.lower(), ignore_case=True) == \
            s.find_req(rid.lower(), ignore_case=True)
    fp = text_fp("title REQ_A_0001", "body of REQ_A_0001")
    for mno in ("", "mno-a", "mno-b"):
        assert f.find_by_fingerprint(fp, mno) == s.find_by_fingerprint(fp, mno)


def test_load_tree_exact_round_trip(stores):
    f, s, _, _ = stores
    a, b = f.load_tree("docA", "mno-a", "Jan2031"), s.load_tree("docA", "mno-a", "Jan2031")
    assert a == b
    assert list(a) == list(b)                       # key order too
    assert [r["req_id"] for r in a["requirements"]] == \
        [r["req_id"] for r in b["requirements"]]
    assert json.dumps(a) == json.dumps(b)


def test_find_req_contracts(stores):
    _, s, _, _ = stores
    rows = s.find_req("REQ_A_0001")
    assert [(r["mno"], r["release"]) for r in rows] == [
        ("mno-a", "Jan2031"), ("mno-a", "Apr2031")]     # duplicate across releases
    assert rows[0]["plan_name"] == "Plan Ex"
    sec = s.find_req("REQ_B_0002", "mno-a", "Jan2031")[0]
    assert sec["plan"] == "PLAN_Z" and sec["plan_name"] == ""
    prim = s.find_req("REQ_B_0001", "mno-a", "Jan2031")[0]
    assert prim["plan_name"] == "Whole Doc"
    empty = s.find_req("REQ_A_0006")[0]
    assert empty["section_number"] == "" and empty["parent_section"] == "1.2"
    assert s.find_req("req_mixed_0001") == []
    assert s.find_req("req_mixed_0001", ignore_case=True)[0]["req_id"] == "Req_Mixed_0001"


@pytest.mark.parametrize("backend", [0, 1])
def test_traversal(stores, backend):
    st = stores[backend]
    cell = ("mno-a", "Jan2031")
    ids = lambda rows: [r["req_id"] for r in rows]  # noqa: E731
    assert st.get_node(*cell, "REQ_A_0003")["doc_id"] == "docA"
    assert st.get_node(*cell, "NOPE") is None
    assert ids(st.children(*cell, "REQ_A_0001")) == ["REQ_A_0002", "REQ_A_0005"]
    assert ids(st.subtree(*cell, "REQ_A_0001")) == [
        "REQ_A_0001", "REQ_A_0002", "REQ_A_0003", "REQ_A_0004", "REQ_A_0005"]
    assert ids(st.subtree(*cell, "REQ_A_0003")) == ["REQ_A_0003", "REQ_A_0004"]
    assert ids(st.ancestors(*cell, "REQ_A_0004")) == [
        "REQ_A_0001", "REQ_A_0002", "REQ_A_0003"]
    assert st.ancestors(*cell, "REQ_A_0001") == []
    assert st.subtree(*cell, "REQ_A_0006")[0]["req_id"] == "REQ_A_0006"
    # cross-doc parent ids do not link: hierarchy is per document
    assert ids(st.children(*cell, "REQ_B_0001")) == ["REQ_B_0002"]


@pytest.mark.parametrize("backend", [0, 1])
def test_find_by_fingerprint_across_cells(stores, backend):
    st = stores[backend]
    fp = text_fp("title REQ_A_0001", "body of REQ_A_0001")
    rows = st.find_by_fingerprint(fp)
    assert [(r["mno"], r["release"], r["req_id"]) for r in rows] == [
        ("mno-a", "Jan2031", "REQ_A_0001"), ("mno-a", "Apr2031", "REQ_A_0001"),
        ("mno-b", "Jan2031", "REQ_C_0001")]
    assert set(rows[0]) == set(st.find_req("REQ_A_0001")[0])
    assert len(st.find_by_fingerprint(fp, "mno-b")) == 1
    changed = text_fp("title REQ_A_0002", "changed body")
    assert [r["release"] for r in st.find_by_fingerprint(changed)] == ["Apr2031"]


def test_tree_layout_rules():
    reqs = [
        {"req_id": "A"}, {"req_id": "B", "parent_req_id": "A"},
        {"req_id": "C", "parent_req_id": "Z"},              # absent parent: root
        {"req_id": "D", "parent_req_id": "E"},              # cycle D <-> E
        {"req_id": "E", "parent_req_id": "D"},
        {"req_id": "", "parent_req_id": "A"},               # blank id: isolated
        {"req_id": "F", "parent_req_id": "F"},              # self parent: root
    ]
    lay = tree_layout(reqs)
    assert [p for p, _, _ in lay] == [None, 0, None, None, 3, None, None]
    assert lay[1][1].startswith(lay[0][1] + ".") and lay[1][2] == 1
    assert len({path for _, path, _ in lay}) == len(reqs)


def test_db_schema_and_meta(stores):
    _, _, env, out = stores
    db = out / "trees-mno-a-Jan2031.db"
    meta = read_meta(db)
    assert meta["schema_version"] == "1" and meta["cell"] == "mno-a__Jan2031"
    assert meta["n_docs"] == "2" and meta["n_nodes"] == "10"
    assert len(meta["source_fingerprint"]) == 64 and meta["created_at"]
    con = sqlite3.connect(db)
    try:
        paths = [p for (p,) in con.execute(
            "SELECT path FROM node WHERE doc_id='docA' ORDER BY seq")]
        tree_json = json.loads(con.execute(
            "SELECT tree_json FROM doc WHERE doc_id='docA'").fetchone()[0])
    finally:
        con.close()
    assert paths[:4] == ["000001", "000001.000001", "000001.000001.000001",
                         "000001.000001.000001.000001"]
    assert "requirements" not in tree_json
    assert not list(out.glob("*.tmp"))


class TestBuilder:
    def test_refuses_existing_without_force(self, stores, capsys):
        _, _, env, out = stores
        capsys.readouterr()
        assert trees_build.main(["--env-dir", str(env), "--out", str(out)]) == 1
        assert "TRS-E002" in capsys.readouterr().err
        assert trees_build.main(["--env-dir", str(env), "--out", str(out),
                                 "--force"]) == 0
        out_text = capsys.readouterr().out
        assert "built 3 cell DB(s): 4 docs, 13 nodes" in out_text
        assert "body of" not in out_text and "title" not in out_text

    def test_cells_filter_and_unknown_cell(self, tmp_path, capsys):
        env, out = tmp_path / "env", tmp_path / "trees"
        _corpus(env)
        assert trees_build.main(["--env-dir", str(env), "--out", str(out),
                                 "--cells", "mno-b__Jan2031"]) == 0
        assert [p.name for p in out.glob("*.db")] == ["trees-mno-b-Jan2031.db"]
        assert trees_build.main(["--env-dir", str(env), "--out", str(out),
                                 "--cells", "mno-b__Apr2031"]) == 1
        assert "TRS-E003" in capsys.readouterr().err

    def test_no_parse_trees(self, tmp_path, capsys):
        assert trees_build.main(["--env-dir", str(tmp_path), "--out",
                                 str(tmp_path / "o")]) == 1
        assert "TRS-E001" in capsys.readouterr().err

    def test_malformed_tree(self, tmp_path, capsys):
        env = tmp_path / "env"
        _corpus(env)
        bad = env / "out" / "parse" / "mno-b" / "Jan2031" / "docD_tree.json"
        bad.write_text("{not json", encoding="utf-8")
        assert trees_build.main(["--env-dir", str(env), "--out",
                                 str(tmp_path / "o")]) == 1
        assert "TRS-E004" in capsys.readouterr().err

    def test_verify_mismatch_removes_temp(self, tmp_path, capsys):
        env, out = tmp_path / "env", tmp_path / "trees"
        _corpus(env)
        # A nested tree file is listed by the file backend but not built.
        nested = env / "out" / "parse" / "mno-b" / "Jan2031" / "sub"
        nested.mkdir()
        (nested / "docN_tree.json").write_text(
            json.dumps(_tree("P", "", [])), encoding="utf-8")
        assert trees_build.main(["--env-dir", str(env), "--out", str(out),
                                 "--cells", "mno-b__Jan2031"]) == 1
        assert "TRS-E005" in capsys.readouterr().err
        assert list(out.iterdir()) == []

    def test_codes_registered(self):
        for n in range(1, 7):
            assert f"TRS-E00{n}" in CODES


class TestOpenAndWrapper:
    def test_open_tree_store_selection(self, tmp_path, monkeypatch):
        monkeypatch.delenv(TREES_ROOT_ENV, raising=False)
        assert isinstance(open_tree_store(tmp_path), FileTreeStore)
        assert isinstance(open_tree_store(tmp_path, tmp_path / "t"), SqliteTreeStore)
        monkeypatch.setenv(TREES_ROOT_ENV, str(tmp_path / "t"))
        assert isinstance(open_tree_store(tmp_path), SqliteTreeStore)
        monkeypatch.setenv(TREES_ROOT_ENV, "  ")
        assert isinstance(open_tree_store(tmp_path), FileTreeStore)

    def test_req_tree_delegates_to_sqlite_when_root_set(self, stores, monkeypatch):
        f, _, env, out = stores
        monkeypatch.delenv(TREES_ROOT_ENV, raising=False)
        file_rows = req_tree.find_req(env, "REQ_A_0002")
        assert isinstance(req_tree.tree_store(env), FileTreeStore)
        monkeypatch.setenv(TREES_ROOT_ENV, str(out))
        assert isinstance(req_tree.tree_store(env), SqliteTreeStore)
        assert req_tree.find_req(env, "REQ_A_0002") == file_rows
        assert req_tree.plans_for_mno(env, "mno-a") == f.plans_for_mno("mno-a")
        assert req_tree.load_tree_flat(env, "docB", "mno-a", "Jan2031") == \
            f.load_tree("docB", "mno-a", "Jan2031")["requirements"]

    def test_missing_root_reads_empty(self, tmp_path):
        s = SqliteTreeStore(tmp_path / "absent")
        assert s.list_cells() == [] and s.find_req("REQ_A_0001") == []
        assert s.load_tree("docA") == {}
