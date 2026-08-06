"""Tests for the shared requirement-tree loader (web/req_tree.py).

Synthetic parse trees only — generic placeholder ids (NFR-8).
"""

from __future__ import annotations

import json
from pathlib import Path

from core.src.web.req_tree import (
    build_tree_hierarchy,
    find_req,
    find_tree,
    list_cells,
    list_docs,
    load_tree,
    load_tree_flat,
    plans_for_mno,
    reqs_for_plan,
)


def _write_tree(env_dir: Path, mno: str, rel: str, doc_id: str, tree: dict):
    d = env_dir / "out" / "parse" / mno / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{doc_id}_tree.json").write_text(json.dumps(tree), encoding="utf-8")


def _corpus(env_dir: Path):
    # Single-plan doc in two releases of mno-a.
    _write_tree(env_dir, "mno-a", "Jan2026", "docA", {
        "plan_id": "PLAN_X",
        "requirements": [
            {"req_id": "REQ_FOO_0001", "title": "root"},
            {"req_id": "REQ_FOO_0002", "parent_req_id": "REQ_FOO_0001"},
        ],
    })
    _write_tree(env_dir, "mno-a", "Apr2026", "docA", {
        "plan_id": "PLAN_X",
        "requirements": [{"req_id": "REQ_FOO_0001"}],
    })
    # Multi-plan doc (empty tree-level plan, per-req plan_ids), one release.
    _write_tree(env_dir, "mno-a", "Apr2026", "docB", {
        "plan_id": "",
        "requirements": [
            {"req_id": "REQ_BAR_0001", "plan_id": "PLAN_Y"},
            {"req_id": "REQ_BAR_0002", "plan_id": "PLAN_Z"},
            {"req_id": "", "plan_id": ""},  # structural artefact
        ],
    })
    # Second MNO.
    _write_tree(env_dir, "mno-b", "Jan2026", "docC", {
        "plan_id": "PLAN_Q",
        "requirements": [{"req_id": "REQ_BAZ_0001"}],
    })


def test_list_cells_chronological(tmp_path: Path):
    _corpus(tmp_path)
    assert list_cells(tmp_path) == [
        {"mno": "mno-a", "release": "Jan2026"},
        {"mno": "mno-a", "release": "Apr2026"},
        {"mno": "mno-b", "release": "Jan2026"},
    ]


def test_list_docs_scoped_and_global(tmp_path: Path):
    _corpus(tmp_path)
    assert list_docs(tmp_path, "mno-a", "Apr2026") == ["docA", "docB"]
    assert list_docs(tmp_path) == ["docA", "docA", "docB", "docC"]
    assert list_docs(tmp_path, "mno-a", "Jul2026") == []


def test_find_tree_cell_flat_and_recursive(tmp_path: Path):
    _corpus(tmp_path)
    p = find_tree(tmp_path, "docA", "mno-a", "Jan2026")
    assert p is not None and "Jan2026" in str(p)
    # Legacy flat file wins the unqualified lookup.
    flat = tmp_path / "out" / "parse" / "docL_tree.json"
    flat.write_text(json.dumps({"requirements": []}), encoding="utf-8")
    assert find_tree(tmp_path, "docL") == flat
    # Unqualified falls back to recursive search.
    assert find_tree(tmp_path, "docC") is not None
    assert find_tree(tmp_path, "nope") is None


def test_load_tree_flat_and_malformed(tmp_path: Path):
    _corpus(tmp_path)
    reqs = load_tree_flat(tmp_path, "docA", "mno-a", "Jan2026")
    assert [r["req_id"] for r in reqs] == ["REQ_FOO_0001", "REQ_FOO_0002"]
    bad = tmp_path / "out" / "parse" / "docX_tree.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_tree_flat(tmp_path, "docX") == []


def test_build_tree_hierarchy_nests_and_skips_blank_ids(tmp_path: Path):
    _corpus(tmp_path)
    tree = build_tree_hierarchy(load_tree_flat(tmp_path, "docA", "mno-a", "Jan2026"))
    assert len(tree) == 1
    assert tree[0]["req_id"] == "REQ_FOO_0001"
    assert tree[0]["child_nodes"][0]["req_id"] == "REQ_FOO_0002"


def test_plans_for_mno_union_with_release_filter(tmp_path: Path):
    _corpus(tmp_path)
    plans = plans_for_mno(tmp_path, "mno-a")
    assert plans == {
        "PLAN_X": ["Jan2026", "Apr2026"],
        "PLAN_Y": ["Apr2026"],
        "PLAN_Z": ["Apr2026"],
    }
    assert plans_for_mno(tmp_path, "mno-b") == {"PLAN_Q": ["Jan2026"]}


def test_find_req_scoped_to_cell(tmp_path: Path):
    _corpus(tmp_path)
    # Unqualified: found in both mno-a releases.
    assert len(find_req(tmp_path, "REQ_FOO_0001")) == 2
    # Qualified: only the named cell is scanned.
    scoped = find_req(tmp_path, "REQ_FOO_0001", "mno-a", "Jan2026")
    assert [(m["mno"], m["release"]) for m in scoped] == [("mno-a", "Jan2026")]
    assert find_req(tmp_path, "REQ_FOO_0001", "mno-b", "Jan2026") == []
    assert find_req(tmp_path, "REQ_FOO_0001", "mno-a", "Jul2026") == []


def test_load_tree_cache_hits_and_invalidates(tmp_path: Path):
    _corpus(tmp_path)
    t1 = load_tree(tmp_path, "docA", "mno-a", "Jan2026")
    t2 = load_tree(tmp_path, "docA", "mno-a", "Jan2026")
    assert t2 is t1  # served from cache, not re-parsed
    # A rewrite (different size => stat change regardless of mtime
    # granularity) invalidates the entry.
    _write_tree(tmp_path, "mno-a", "Jan2026", "docA", {
        "plan_id": "PLAN_X",
        "requirements": [{"req_id": "REQ_FOO_0009", "title": "rewritten"}],
    })
    t3 = load_tree(tmp_path, "docA", "mno-a", "Jan2026")
    assert t3 is not t1
    assert t3["requirements"][0]["req_id"] == "REQ_FOO_0009"


def test_reqs_for_plan_per_req_fallback_rule(tmp_path: Path):
    _corpus(tmp_path)
    # Tree-level plan: every req of docA belongs to PLAN_X.
    rows = reqs_for_plan(tmp_path, "mno-a", "Jan2026", "PLAN_X")
    assert [(r["req_id"], r["doc_id"]) for r in rows] == [
        ("REQ_FOO_0001", "docA"), ("REQ_FOO_0002", "docA"),
    ]
    # Per-req plan wins in the multi-plan doc.
    rows = reqs_for_plan(tmp_path, "mno-a", "Apr2026", "PLAN_Y")
    assert [r["req_id"] for r in rows] == ["REQ_BAR_0001"]
    assert reqs_for_plan(tmp_path, "mno-a", "Apr2026", "PLAN_NOPE") == []
