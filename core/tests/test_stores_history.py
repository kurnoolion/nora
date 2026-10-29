"""Tests for core/src/stores/history.py + history_build.py (strand
corp-db-layer S5): version intervals, change kinds, the six queries, the
builder CLI and backend selection.

Synthetic trees only — generic placeholder ids and release labels.

Lineage of mno-h / PLANA (PLANB never changes):

    Jan2031  sec 1 {R1, R2, R3}            sec 2 {R4, R5, R6}
    Apr2031  R1 text changed; R2 priority changed; R3 whitespace only;
             R4 moved to sec 1; R6 removed
    Jul2031  sec 2 renumbered to 3 (R5 moves with it); R6 re-added under
             sec 3; R3 renamed R3X (same content)
    Oct2031  sec 1 children reordered (R2 before R1)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.src.pipeline.error_codes import CODES
from core.src.stores import history_build, trees_build
from core.src.stores.history import (
    HISTORY_ROOT_ENV,
    HistoryStore,
    SqliteHistoryStore,
    node_key,
    open_history_store,
    read_meta,
    word_diff,
)

MNO = "mno-h"
RELS = ["Jan2031", "Apr2031", "Jul2031", "Oct2031"]


def _sec(rid, sec, title):
    return {"req_id": rid, "section_number": sec, "title": title, "text": "",
            "parent_req_id": "", "is_requirement": False, "priority": "",
            "applicability": []}


def _req(rid, sec, parent, text, priority="Mandatory", app=("Phone",), title=""):
    return {"req_id": rid, "section_number": sec, "title": title or f"title {rid}",
            "text": text, "parent_req_id": parent, "is_requirement": True,
            "priority": priority, "applicability": list(app)}


T1 = "The device shall retry after 30 s."
T1B = "The device shall retry after 31 s."
T2 = "The device shall log every error."
T3 = "The device shall keep  the “last” value."
T3_WS = "The device shall keep the \"last\"\nvalue. "
T4 = "Timer T1 shall be 10 s."
T5 = "Timer T2 shall be 20 s."
T6 = "Timer T3 shall be 40 s."


def _plana(rel: str) -> list[dict]:
    s1 = _sec("SEC_1", "1", "General")
    if rel == "Jan2031":
        return [s1,
                _req("R1", "1.1", "SEC_1", T1), _req("R2", "1.2", "SEC_1", T2),
                _req("R3", "1.3", "SEC_1", T3),
                _sec("SEC_2", "2", "Timers"),
                _req("R4", "2.1", "SEC_2", T4), _req("R5", "2.2", "SEC_2", T5),
                _req("R6", "2.3", "SEC_2", T6)]
    r1 = _req("R1", "1.1", "SEC_1", T1B)
    r2 = _req("R2", "1.2", "SEC_1", T2, priority="Optional")
    r4 = _req("R4", "1.4", "SEC_1", T4)
    if rel == "Apr2031":
        return [s1, r1, r2, _req("R3", "1.3", "SEC_1", T3_WS), r4,
                _sec("SEC_2", "2", "Timers"), _req("R5", "2.2", "SEC_2", T5)]
    tail = [_req("R3X", "1.3", "SEC_1", T3_WS, title="title R3"), r4,
            _sec("SEC_2", "3", "Timers"), _req("R5", "3.2", "SEC_2", T5),
            _req("R6", "3.3", "SEC_2", T6)]
    if rel == "Jul2031":
        return [s1, r1, r2, *tail]
    return [s1, r2, r1, *tail]                      # Oct2031: reorder


def _write(env: Path, rel: str, doc: str, plan: str, reqs: list[dict]) -> None:
    d = env / "out" / "parse" / MNO / rel
    d.mkdir(parents=True, exist_ok=True)
    tree = {"mno": MNO, "release": rel, "plan_id": plan,
            "plan_name": f"Plan {plan[-1]}", "version": rel, "requirements": reqs}
    (d / f"{doc}_tree.json").write_text(json.dumps(tree), encoding="utf-8")


def _corpus(env: Path) -> None:
    for rel in RELS:
        _write(env, rel, "docA", "PLANA", _plana(rel))
        _write(env, rel, "docB", "PLANB", [
            _sec("SEC_9", "9", "Other"), _req("R9", "9.1", "SEC_9", "Unchanged text.")])


@pytest.fixture()
def built(tmp_path):
    env, out = tmp_path / "env", tmp_path / "history"
    _corpus(env)
    assert history_build.main(["--env-dir", str(env), "--out", str(out)]) == 0
    return SqliteHistoryStore(out), env, out


def _kinds(summary: dict) -> dict[str, list[str]]:
    return {c["local_id"] or c["plan_id"]: c["change"] for c in summary["changes"]}


def test_protocol_and_releases(built):
    h, _, _ = built
    assert isinstance(h, HistoryStore)
    assert h.list_mnos() == [MNO]
    assert h.releases(MNO) == RELS
    assert h.releases("nope") == []


def test_step_jan_apr_changed_moved_removed_and_no_ops(built):
    h, _, _ = built
    s = h.diff_releases(MNO, "Jan2031", "Apr2031")
    assert _kinds(s) == {"R1": ["changed"], "R2": ["changed"],
                         "R4": ["moved"], "R6": ["removed"]}
    assert s["counts"] == {"added": 0, "removed": 1, "changed": 2, "moved": 1,
                           "renumbered": 0, "reordered": 0, "likely_renamed": 0}
    assert list(s["plans"]) == ["PLANA"]            # PLANB never descended
    secs = s["plans"]["PLANA"]["sections"]        # keyed by containing section
    assert secs["1"]["changed"] == 2 and secs["1"]["moved"] == 1 and secs["1"]["rows"] == 3
    assert secs["2"]["removed"] == 1 and secs["2"]["rows"] == 1


def test_step_apr_jul_renumber_rename_and_readd(built):
    h, _, _ = built
    s = h.diff_releases(MNO, "Apr2031", "Jul2031", diffs=True)
    k = _kinds(s)
    assert k["2"] == ["removed"] and k["3"] == ["added", "likely_renamed"]
    assert k["R3"] == ["removed"] and k["R3X"] == ["added", "likely_renamed"]
    assert k["R5"] == ["renumbered"] and k["R6"] == ["added"]
    assert len(k) == 6
    by_id = {c["local_id"]: c for c in s["changes"]}
    assert by_id["3"]["renamed_from"] == node_key("section", "PLANA", "2")
    assert by_id["R3X"]["renamed_from"] == node_key("requirement", "PLANA", "R3")
    assert s["counts"]["added"] == 3 and s["counts"]["removed"] == 2
    assert s["counts"]["likely_renamed"] == 2
    # an added node's diff is its whole content; a renumbered one has none
    assert {o["op"] for o in by_id["R3X"]["diff"]["text"]} == {"add"}
    assert by_id["R5"]["diff"] is None
    assert by_id["R5"]["renumber"] == "parent" and by_id["R5"]["renumbered_from"] == "2.2"


def test_step_jul_oct_reorder_and_latest_changes(built):
    h, _, _ = built
    s = h.latest_changes(MNO)
    assert (s["from"], s["to"]) == ("Jul2031", "Oct2031")
    assert _kinds(s) == {"1": ["reordered"]}
    assert s == h.diff_releases(MNO, "Jul2031", "Oct2031")


def test_diff_non_adjacent_and_self(built):
    h, _, _ = built
    k = _kinds(h.diff_releases(MNO, "Jan2031", "Oct2031"))
    assert k["R1"] == ["changed"] and k["R4"] == ["moved"] and k["R6"] == ["renumbered"]
    assert "1" not in k                              # children set changed too
    assert h.diff_releases(MNO, "Apr2031", "Apr2031")["changes"] == []
    assert h.diff_releases(MNO, "", "")["from"] == "Jan2031"
    with pytest.raises(ValueError):
        h.diff_releases(MNO, "Jan2031", "Dec2099")


def test_summary_ranks_containing_sections_by_rows(built):
    """Jan→Apr: R1, R2 changed and R4 moved into sec 1; R6 removed from
    sec 2. Sections aggregate by the row's containing section and rank by
    rows; kind tallies may overlap a row, so rows and renamed_pairs are
    reported beside them."""
    h, _, _ = built
    d = h.diff_releases(MNO, "Jan2031", "Apr2031")
    top = d["most_changed_sections"]
    assert [(t["plan_id"], t["section"], t["rows"]) for t in top[:2]] == \
        [("PLANA", "1", 3), ("PLANA", "2", 1)]
    assert top[0]["kinds"] == {"changed": 2, "moved": 1}
    assert top[1]["kinds"] == {"removed": 1}
    assert d["rows"] == len(d["changes"]) == 4
    assert d["renamed_pairs"] == 0
    assert d["plans"]["PLANA"]["rows"] == 4
    assert d["plans"]["PLANA"]["sections"]["1"]["rows"] == 3
    assert all(c["parent_section"] in {"1", "2"} for c in d["changes"])
    # Apr→Jul: SEC_2 renumbered 2→3 and R3 renamed R3X are two rename pairs,
    # each also counted as added + removed in the kind tallies
    j = h.diff_releases(MNO, "Apr2031", "Jul2031")
    assert j["renamed_pairs"] == 2
    assert j["counts"]["added"] >= j["renamed_pairs"]


def test_moved_needs_a_new_parent_renumbered_keeps_it(built):
    """Jan→Apr: R4 left SEC_2 for SEC_1 (moved, parent changed). Apr→Jul:
    R5 kept SEC_2 as parent while its number went 2.2 → 3.2 (renumbered,
    chapter). Jan→Oct: R6 is back under SEC_2 at 3.3 (renumbered, not
    moved)."""
    h, _, _ = built
    d = h.diff_releases(MNO, "Jan2031", "Apr2031")
    by_id = {c["local_id"]: c for c in d["changes"]}
    assert by_id["R4"]["change"] == ["moved"]
    assert by_id["R4"]["moved_from"] == "2.1" and "renumber" not in by_id["R4"]
    o = {c["local_id"]: c for c in h.diff_releases(MNO, "Jan2031", "Oct2031")["changes"]}
    assert o["R6"]["change"] == ["renumbered"] and o["R6"]["renumber"] == "parent"
    # req_history sees one node's intervals only, so a parent-key change
    # stays "moved" there; the diff has both releases and resolves it.
    assert [x["change"] for x in h.req_history(MNO, "PLANA", "R5")][1:] == [["moved"]]


def test_containing_section_prefers_tree_parent_then_outline_prefix():
    from core.src.stores.history import containing_section
    assert containing_section("section", "4.2", "", "") == "4.2"
    assert containing_section("plan", "P", "", "") == ""
    assert containing_section("requirement", "R1", "2.2.62", "2.2") == "2.2"   # tree parent is a section
    assert containing_section("requirement", "R1", "2.2.62", "") == "2.2"      # parent is a requirement / plan root
    assert containing_section("requirement", "R1", "5.1.39", "") == "5.1"
    assert containing_section("requirement", "R1", "2", "") == ""              # top-level number: plan level
    assert containing_section("requirement", "R1", "", "") == ""
    # nested requirements roll up: nearest real section node, else two levels
    secs = ["5", "5.1", "2", "2.2", "2.2.52"]
    assert containing_section("requirement", "R1", "5.1.39.4", "", secs) == "5.1"
    assert containing_section("requirement", "R1", "2.2.52.3", "", secs) == "2.2.52"
    assert containing_section("requirement", "R1", "1.15.16.2", "", secs) == "1.15"
    assert containing_section("requirement", "R1", "1.11", "", secs) == "1"


def test_pairs_keep_absent_plans_out_of_the_rows():
    from core.src.stores.history import _pairs, node_key, text_fp
    pk, rk, px, rx = (node_key("plan", "PLANA"), node_key("requirement", "PLANA", "R1"),
                      node_key("plan", "PLANX"), node_key("requirement", "PLANX", "RX"))
    fa, fb, fx, fpa, fpx = (text_fp("t", "old"), text_fp("t", "new"), text_fp("t", "gone"),
                            text_fp("Plan A", ""), text_fp("Plan X", ""))
    node = lambda key, kind, plan, lid, fp, sec="": {"key": key, "kind": kind, "plan_id": plan, "local_id": lid,
                                                     "text_fp": fp, "attrs_json": "{}",
                                                     "location_json": '{"section": "%s"}' % sec}
    sa = {pk: node(pk, "plan", "PLANA", "PLANA", fpa), rk: node(rk, "requirement", "PLANA", "R1", fa, "1.1"),
          px: node(px, "plan", "PLANX", "PLANX", fpx), rx: node(rx, "requirement", "PLANX", "RX", fx, "1.1")}
    sb = {pk: node(pk, "plan", "PLANA", "PLANA", fpa), rk: node(rk, "requirement", "PLANA", "R1", fb, "1.1")}
    changes = [
        {"key": rk, "node": "requirement", "plan_id": "PLANA", "local_id": "R1", "change": ["changed", "renumbered"],
         "renumber": "sibling", "renumbered_from": "1.2"},
        {"key": px, "node": "plan", "plan_id": "PLANX", "local_id": "PLANX", "change": ["removed"]},
        {"key": rx, "node": "requirement", "plan_id": "PLANX", "local_id": "RX", "change": ["removed"]},
    ]
    texts = {fa: ("t", "old"), fb: ("t", "new"), fx: ("t", "gone"), fpa: ("Plan A", ""), fpx: ("Plan X", "")}
    out = _pairs("m", "Jan2031", "Apr2031", changes, {"PLANA": (sa, sb), "PLANX": (sa, sb)}, texts)
    assert list(out["plans"]) == ["PLANA"]                      # PLANX only under absent_plans
    assert out["absent_plans"] == {"PLANX": {"absent_in": "Apr2031"}}
    assert out["counts"] == {"changed": 1, "added": 0, "removed": 0, "renamed": 0,
                             "renumbered": 1, "moved": 0, "plans": 1, "absent_plans": 1}
    assert out["plans"]["PLANA"]["changed"][0]["kinds"] == ["renumbered"]


def test_summary_separates_absent_plans():
    from core.src.stores.history import _summary, node_key
    rows = [
        {"key": node_key("plan", "PLANX"), "node": "plan", "plan_id": "PLANX", "local_id": "PLANX",
         "req_id": "", "section": "", "parent_section": "", "change": ["removed"]},
        {"key": node_key("requirement", "PLANX", "RX"), "node": "requirement", "plan_id": "PLANX",
         "local_id": "RX", "req_id": "RX", "section": "1.1", "parent_section": "1", "change": ["removed"]},
        {"key": node_key("requirement", "PLANA", "R1"), "node": "requirement", "plan_id": "PLANA",
         "local_id": "R1", "req_id": "R1", "section": "1.1", "parent_section": "1", "change": ["changed"]},
    ]
    s = _summary("mno-h", "Jan2031", "Apr2031", rows)
    assert s["absent_plans"] == {"PLANX": {"absent_in": "Apr2031", "rows": 2}}
    assert s["counts"]["removed"] == 2 and s["edits"]["removed"] == 0
    assert s["edits"]["changed"] == 1 and s["rows"] == 3
    assert s["plans"]["PLANX"]["present"] == {"from": True, "to": False}
    assert s["plans"]["PLANA"]["present"] == {"from": True, "to": True}
    assert [t["plan_id"] for t in s["most_changed_sections"]] == ["PLANA"]   # edit rows only


def test_changed_pairs_payload(built):
    """Jan→Apr: R1 text and R2 priority changed (pairs), R4 moved to sec 1,
    R6 removed; a whitespace-only edit (R3) is no pair. Apr→Jul: R3→R3X
    and SEC_2→3 are rename pairs, R5 renumbered under its renamed parent,
    R6 added."""
    h, _, _ = built
    d = h.changed_pairs(MNO, "Jan2031", "Apr2031")
    assert (d["from"], d["to"]) == ("Jan2031", "Apr2031") and list(d["plans"]) == ["PLANA"]
    p = d["plans"]["PLANA"]
    assert p["plan_name"] == "Plan A" and p["present"] == {"from": True, "to": True}
    assert [c["req_id"] for c in p["changed"]] == ["R1", "R2"]
    r1, r2 = p["changed"]
    assert r1["old"]["text"] == T1 and r1["new"]["text"] == T1B and r1["kinds"] == []
    assert r2["old"]["priority"] == "Mandatory" and r2["new"]["priority"] == "Optional"
    assert r2["old"]["text"] == r2["new"]["text"] == T2
    assert [m["req_id"] for m in p["moved"]] == ["R4"] and p["moved"][0]["from"] == "2.1"
    assert [r["req_id"] for r in p["removed"]] == ["R6"] and p["removed"][0]["text"] == T6
    assert p["added"] == [] and p["renamed"] == [] and p["renumbered"] == []
    assert d["counts"] == {"changed": 2, "added": 0, "removed": 1, "renamed": 0,
                           "renumbered": 0, "moved": 1, "plans": 1, "absent_plans": 0,
                           "requirements_from": 7, "requirements_to": 6}
    assert p["moved"][0]["from_parent"] and p["moved"][0]["to_parent"]   # containing sections named
    j = h.changed_pairs(MNO, "Apr2031", "Jul2031")["plans"]["PLANA"]
    assert sorted((r["kind"], r["old_id"], r["new_id"]) for r in j["renamed"]) == \
        [("requirement", "R3", "R3X"), ("section", "2", "3")]
    assert [r["req_id"] for r in j["renumbered"]] == ["R5"] and j["renumbered"][0]["detail"] == "parent"
    assert [r["req_id"] for r in j["added"]] == ["R6"]
    assert j["removed"] == [] and j["changed"] == []           # R3 is the removed half of a rename
    with pytest.raises(ValueError):
        h.changed_pairs(MNO, "Jan2031", "Dec2099")
    assert h.changed_pairs(MNO, "Apr2031", "Apr2031")["counts"]["plans"] == 0


def test_req_history_text_change_with_word_diff(built):
    h, _, _ = built
    vs = h.req_history(MNO, "PLANA", "R1")
    assert [(v["valid_from"], v["valid_to"]) for v in vs] == [
        ("Jan2031", "Jan2031"), ("Apr2031", "")]
    assert vs[0]["change"] == ["added"] and vs[1]["change"] == ["changed"]
    assert vs[1]["diff"]["title"] is None
    assert [o for o in vs[1]["diff"]["text"] if o["op"] != "eq"] == [
        {"op": "del", "text": "30"}, {"op": "add", "text": "31"}]
    assert vs[1]["text"] == T1B and vs[1]["location"] == {
        "parent": node_key("section", "PLANA", "1"), "section": "1.1"}


def test_priority_change_is_node_change_with_same_text(built):
    h, _, _ = built
    vs = h.req_history(MNO, "PLANA", "R2")
    assert [v["priority"] for v in vs] == ["Mandatory", "Optional"]
    assert vs[1]["change"] == ["changed"] and vs[1]["diff"] is None
    assert h.last_changed(MNO, "PLANA", "R2") == "Apr2031"


def test_whitespace_only_change_is_no_change(built):
    h, _, _ = built
    vs = h.req_history(MNO, "PLANA", "R3")
    assert len(vs) == 1 and vs[0]["valid_to"] == "Apr2031"   # then renamed
    assert h.last_changed(MNO, "PLANA", "R3") == "Jan2031"


def test_move_is_location_only(built):
    h, _, _ = built
    vs = h.req_history(MNO, "PLANA", "R4")
    assert [v["change"] for v in vs] == [["added"], ["moved"]]
    assert vs[0]["node_fp"] == vs[1]["node_fp"]
    assert h.last_changed(MNO, "PLANA", "R4") == "Jan2031"


def test_removed_and_readded(built):
    h, _, _ = built
    vs = h.req_history(MNO, "PLANA", "R6")
    assert [(v["valid_from"], v["valid_to"]) for v in vs] == [
        ("Jan2031", "Jan2031"), ("Jul2031", "")]
    assert vs[1]["change"] == ["readded", "moved"]
    assert h.last_changed(MNO, "PLANA", "R6") == "Jan2031"


def test_last_changed_and_plan_last_changed(built):
    h, _, _ = built
    assert h.last_changed(MNO, "PLANA", "R1") == "Apr2031"
    assert h.last_changed(MNO, "PLANB", "R9") == "Jan2031"
    assert h.last_changed(MNO, "PLANA", "NOPE") is None
    assert h.plan_last_changed(MNO, "PLANA") == "Oct2031"
    assert h.plan_last_changed(MNO, "PLANB") == "Jan2031"
    assert h.plan_last_changed("nope", "PLANA") is None


def test_plan_history_steps_and_bounds(built):
    h, _, _ = built
    steps = h.plan_history(MNO, "PLANA")
    assert [(s["from"], s["to"]) for s in steps] == [
        ("Jan2031", "Apr2031"), ("Apr2031", "Jul2031"), ("Jul2031", "Oct2031")]
    r1 = next(c for c in steps[0]["changes"] if c["local_id"] == "R1")
    assert r1["diff"]["text"] is not None
    assert h.plan_history(MNO, "PLANB") == []
    mid = h.plan_history(MNO, "PLANA", "Apr2031", "Jul2031")
    assert [(s["from"], s["to"]) for s in mid] == [("Apr2031", "Jul2031")]
    assert len(h.req_history(MNO, "PLANA", "R1", from_rel="Apr2031")) == 1
    assert h.req_history(MNO, "PLANA", "R1", to_rel="Jan2031")[0]["text"] == T1
    with pytest.raises(ValueError):
        h.req_history(MNO, "PLANA", "R1", from_rel="Dec2099")


def test_content_dedup_and_version_rows(built):
    _, _, out = built
    con = sqlite3.connect(str(out / f"history-{MNO}.db"))
    try:
        n6 = con.execute("SELECT COUNT(*) FROM content WHERE text = ?", (T6,)).fetchone()[0]
        n9 = con.execute(
            "SELECT COUNT(*) FROM version WHERE node_key = ?",
            (node_key("requirement", "PLANB", "R9"),)).fetchone()[0]
        n3 = con.execute("SELECT COUNT(*) FROM content WHERE title = 'title R3'"
                         ).fetchone()[0]
    finally:
        con.close()
    assert n6 == 1                       # removed + re-added: one content row
    assert n9 == 1                       # unchanged across four releases
    assert n3 == 1                       # whitespace-only edit: no new content


def test_meta_and_counts(built):
    _, _, out = built
    meta = read_meta(out / f"history-{MNO}.db")
    assert meta["schema_version"] == "1" and meta["fingerprint_version"] == "v3"
    assert meta["mno"] == MNO and json.loads(meta["releases"]) == RELS
    assert meta["n_plans"] == "2" and meta["n_sections"] == "4"
    assert meta["n_requirements"] == "8"
    assert set(json.loads(meta["source"])) == set(RELS)
    assert all(json.loads(meta["source"]).values())


def test_builder_refusals_and_codes(tmp_path, built, capsys):
    _, env, out = built
    for code in ("HIS-E001", "HIS-E002", "HIS-E003", "HIS-E004", "HIS-E005"):
        assert code in CODES
    assert history_build.main(["--env-dir", str(env), "--out", str(out)]) == 1
    assert "HIS-E002" in capsys.readouterr().err
    assert history_build.main(["--env-dir", str(env), "--out", str(out),
                               "--mno", "nope", "--force"]) == 1
    assert "HIS-E003" in capsys.readouterr().err
    assert history_build.main(["--env-dir", str(tmp_path / "empty"),
                               "--out", str(out)]) == 1
    assert "HIS-E001" in capsys.readouterr().err
    assert history_build.main(["--env-dir", str(env), "--out", str(out),
                               "--force"]) == 0
    printed = capsys.readouterr().out
    assert "Timer" not in printed and "device" not in printed


def test_sqlite_trees_source_builds_the_same(tmp_path, built):
    h, env, _ = built
    trees = tmp_path / "trees"
    assert trees_build.main(["--env-dir", str(env), "--out", str(trees)]) == 0
    out2 = tmp_path / "history2"
    assert history_build.main(["--env-dir", str(env), "--trees-root", str(trees),
                               "--out", str(out2), "--mno", MNO]) == 0
    h2 = SqliteHistoryStore(out2)
    for a, b in zip(RELS, RELS[1:]):
        assert h.diff_releases(MNO, a, b, True) == h2.diff_releases(MNO, a, b, True)
    assert json.loads(read_meta(out2 / f"history-{MNO}.db")["source"]) == \
        json.loads(read_meta(Path(h.root) / f"history-{MNO}.db")["source"])


def test_open_history_store(tmp_path, monkeypatch):
    monkeypatch.delenv(HISTORY_ROOT_ENV, raising=False)
    assert open_history_store() is None
    monkeypatch.setenv(HISTORY_ROOT_ENV, str(tmp_path))
    st = open_history_store()
    assert isinstance(st, SqliteHistoryStore) and st.list_mnos() == []
    assert isinstance(open_history_store(tmp_path / "x"), SqliteHistoryStore)


def test_word_diff():
    assert word_diff("a b c", "a x c") == [
        {"op": "eq", "text": "a"}, {"op": "del", "text": "b"},
        {"op": "add", "text": "x"}, {"op": "eq", "text": "c"}]
    assert word_diff("", "") == []


def test_change_rows_carry_req_id_and_the_route_takes_a_key(built):
    """Field finding: a change row's `key` passed as req_id to
    /req/last-changed answered 200 with no release. Rows now carry
    `req_id`; the route also accepts `key=`."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.src.stores.api import history_router
    from core.src.stores.history import REQUIREMENT
    h, _, _ = built
    rels = h.releases(MNO)
    rows = [r for a, b in zip(rels, rels[1:])
            for r in h.diff_releases(MNO, a, b)["changes"] if r["node"] == REQUIREMENT]
    assert rows and all(r["req_id"] == r["local_id"] for r in rows)
    app = FastAPI(); app.include_router(history_router(h))
    c = TestClient(app)
    row = rows[0]
    by_id = c.get("/stores/history/v1/req/last-changed",
                  params={"mno": MNO, "plan_id": row["plan_id"], "req_id": row["req_id"]}).json()
    by_key = c.get("/stores/history/v1/req/last-changed",
                   params={"mno": MNO, "key": row["key"]}).json()
    assert by_id == by_key and by_id
    assert c.get("/stores/history/v1/req/last-changed",
                 params={"mno": MNO, "key": "not json"}).status_code == 422


def test_builder_release_filter_and_docs_per_release(tmp_path, built, capsys):
    """A stray parse directory must not become a release: --exclude drops
    it, --release keeps an explicit list, and the report shows the
    documents per release so a two-document stub stands out."""
    _, env, _ = built
    out = tmp_path / "h2"
    assert history_build.main(["--env-dir", str(env), "--out", str(out),
                               "--exclude", RELS[0]]) == 0
    printed = capsys.readouterr().out
    assert SqliteHistoryStore(out).releases(MNO) == RELS[1:]
    assert f"{RELS[1]} (2 docs)" in printed and f"excluded: {RELS[0]}" in printed
    assert history_build.main(["--env-dir", str(env), "--out", str(out), "--force",
                               "--release", f"{RELS[1]},{RELS[2]}"]) == 0
    assert SqliteHistoryStore(out).releases(MNO) == [RELS[1], RELS[2]]
    assert history_build.main(["--env-dir", str(env), "--out", str(out), "--force",
                               "--release", "Nope2031"]) == 1
    assert "HIS-E007" in capsys.readouterr().err

