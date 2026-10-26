"""Tests for core/src/stores/registry.py + publish.py (strand corp-db-layer
phase 4b; publish-design.md §7): fingerprint idempotence, release-set
completeness, staging atomicity, gated flips, rollback, GC retention and
registry-mode resolution in the open_* factories.

Synthetic data only: placeholder MNO names, fake release labels; cell DBs
are meta-only SQLite files (publish reads nothing else from them).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.src.pipeline.error_codes import PipelineError
from core.src.stores import publish as pub
from core.src.stores.history import open_history_store, read_meta as history_meta
from core.src.stores.registry import (
    REGISTRY_ENV,
    RELEASE_SET_ENV,
    Registry,
    history_fingerprint,
    registry_path,
    registry_store_paths,
)
from core.src.stores.trees import open_tree_store

MNO_A, MNO_B = "mno-a", "mno-b"
R1, R2 = "Jan2031", "Apr2031"


# ---------------------------------------------------------------------------
# fixtures

def _req(rid, parent="", text="", section=""):
    return {"req_id": rid, "section_number": section, "title": f"title {rid}",
            "text": text or f"body of {rid}", "parent_req_id": parent,
            "parent_section": "", "hierarchy_path": [], "is_requirement": True,
            "tables": [], "priority": "Mandatory", "applicability": ["Phone"]}


def _tree(plan, reqs):
    return {"mno": "", "release": "", "plan_id": plan, "plan_name": f"Plan {plan}",
            "version": "1", "requirements": reqs}


def write_parse(env: Path, mno: str, rel: str, doc: str, reqs: list) -> None:
    d = env / "out" / "parse" / mno / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{doc}_tree.json").write_text(json.dumps(_tree("PLAN_X", reqs)), encoding="utf-8")


def make_env(env: Path) -> None:
    write_parse(env, MNO_A, R1, "docA", [_req("REQ_A_1", section="1"), _req("REQ_A_2", "REQ_A_1")])
    write_parse(env, MNO_A, R2, "docA", [_req("REQ_A_1", section="1"), _req("REQ_A_2", "REQ_A_1", text="changed")])
    write_parse(env, MNO_B, R1, "docB", [_req("REQ_B_1", section="1")])


def make_cell_db(path: Path, mno: str, rel: str, fingerprint: str, schema: str = "2",
                 fpv: str = "v3") -> None:
    """A meta-only cell DB: all publish reads from a Cells unit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.executemany("INSERT INTO meta VALUES (?,?)", [
        ("schema_version", schema), ("cell_mno", mno), ("cell_release", rel),
        ("source_fingerprint", fingerprint), ("fingerprint_version", fpv)])
    con.execute("CREATE TABLE corpus(doc TEXT, req_id TEXT, title TEXT, text TEXT)")
    con.execute("INSERT INTO corpus VALUES ('d', 'r', 't', ?)", (fingerprint,))
    con.commit()
    con.close()


def make_cells(cells_dir: Path, fps: dict[tuple[str, str], str]) -> None:
    for (mno, rel), fp in fps.items():
        make_cell_db(cells_dir / f"cell-{mno}-{rel}.db", mno, rel, fp)


CELL_FPS = {(MNO_A, R1): "fp-a1", (MNO_A, R2): "fp-a2", (MNO_B, R1): "fp-b1"}


@pytest.fixture()
def world(tmp_path):
    env, cells, root = tmp_path / "env", tmp_path / "cells", tmp_path / "serve"
    make_env(env)
    make_cells(cells, CELL_FPS)
    return {"env": env, "cells": cells, "root": root, "work": tmp_path / "work"}


def do_publish(w, label, **kw):
    kw.setdefault("cells_src", str(w["cells"]))
    kw.setdefault("env_dir", str(w["env"]))
    kw.setdefault("work_dir", str(w["work"]))
    w["work"].mkdir(exist_ok=True)
    return pub.publish(w["root"], label, **kw)


def files(root: Path, store: str) -> list[str]:
    d = root / store
    return sorted(p.name for p in d.glob("*.db")) if d.is_dir() else []


def members(root: Path, label: str) -> dict[str, int]:
    return {f"{m.store}/{m.unit}": m.version for m in Registry(registry_path(root)).members(label)}


# ---------------------------------------------------------------------------
# publish + stage

def test_first_publish_stages_every_unit_and_builds_history(world):
    out = do_publish(world, "set-1", base="none")
    root = world["root"]
    assert out["stores"] == {"cells": {"new": 3, "reused": 0},
                             "trees": {"new": 3, "reused": 0},
                             "history": {"new": 2, "reused": 0}}
    assert out["history_rebuilt"] == [MNO_A, MNO_B]
    assert out["warnings"] == []
    assert files(root, "cells") == [f"cell-{MNO_A}-{R2}-v1.db", f"cell-{MNO_A}-{R1}-v1.db",
                                    f"cell-{MNO_B}-{R1}-v1.db"]
    assert files(root, "trees") == [f"trees-{MNO_A}-{R2}-v1.db", f"trees-{MNO_A}-{R1}-v1.db",
                                    f"trees-{MNO_B}-{R1}-v1.db"]
    assert files(root, "history") == [f"history-{MNO_A}-v1.db", f"history-{MNO_B}-v1.db"]
    assert not (root / "cells" / ".staging").exists() or not list((root / "cells" / ".staging").iterdir())
    assert out["staged_bytes"] > 0

    reg = Registry(registry_path(root))
    ms = {m.key: m for m in reg.members("set-1")}
    assert len(ms) == 8
    # sha256 + bytes recorded as placed
    for m in ms.values():
        sha, n = pub.sha256_file(root / m.file)
        assert (sha, n) == (m.sha256, m.bytes)
    # Cells identity = the cell DB's source_fingerprint, schema recorded
    assert ms[("cells", f"{MNO_A}__{R1}")].fingerprint == "fp-a1"
    assert ms[("cells", f"{MNO_A}__{R1}")].schema_version == "2"
    # History identity = hash of its Trees members' fingerprints, and the
    # built DB's meta.source carries exactly those
    trees_fps = {R1: ms[("trees", f"{MNO_A}__{R1}")].fingerprint,
                 R2: ms[("trees", f"{MNO_A}__{R2}")].fingerprint}
    h = ms[("history", MNO_A)]
    assert h.fingerprint == history_fingerprint(trees_fps)
    assert json.loads(history_meta(root / h.file)["source"]) == trees_fps
    # nothing active yet; set recorded with code_version attribution
    assert reg.active() == []
    assert reg.release_set("set-1")["created_at"]


def test_canonical_form_version_is_part_of_unit_identity(world):
    """Same source, same schema, hashes under a newer canonical form: a new
    Cells version, not a reuse (the file's fingerprint columns differ)."""
    from core.src.stores.registry import Registry, registry_path
    make_cell_db(world["cells"] / f"cell-{MNO_A}-{R1}.db", MNO_A, R1, "fp-a1", fpv="v2")
    do_publish(world, "set-1", base="none")
    make_cell_db(world["cells"] / f"cell-{MNO_A}-{R1}.db", MNO_A, R1, "fp-a1", fpv="v3")
    out = do_publish(world, "set-2")
    assert f"cells/{MNO_A}__{R1}@v2" in out["new_units"]
    reg = Registry(registry_path(world["root"]))
    rows = reg.versions("cells", f"{MNO_A}__{R1}")
    assert [(r.version, r.fingerprint_version) for r in rows] == [(1, "v2"), (2, "v3")]
    assert reg.find_version("cells", f"{MNO_A}__{R1}", "fp-a1", "2", fingerprint_version="v2").version == 1
    assert reg.find_version("cells", f"{MNO_A}__{R1}", "fp-a1", "2").version == 2   # any version: newest


def test_registry_adds_the_fingerprint_version_column_to_an_older_file(tmp_path):
    """A registry written before the column exists gains it on ensure();
    rows read back with the empty default."""
    import sqlite3
    from core.src.stores.registry import _SCHEMA, Registry, SCHEMA_VERSION
    path = tmp_path / "registry.db"
    con = sqlite3.connect(path)
    for stmt in _SCHEMA.replace("  fingerprint_version TEXT NOT NULL DEFAULT '',\n", "").split(";"):
        if stmt.strip():
            con.execute(stmt)
    con.execute("INSERT INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    con.execute("INSERT INTO store_version(store, unit, version, fingerprint, file, sha256, bytes, "
                "schema_version, staged_at) VALUES ('cells','m__r',1,'fp','cells/x.db','s',1,'2','t')")
    con.commit(); con.close()
    assert "fingerprint_version" not in {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(store_version)")}
    Registry(path, read_only=False).ensure()
    assert "fingerprint_version" in {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(store_version)")}
    sv = Registry(path).find_version("cells", "m__r", "fp", "2")
    assert sv is not None and sv.fingerprint_version == ""
    assert Registry(path).find_version("cells", "m__r", "fp", "2", fingerprint_version="v3") is None


def test_republish_same_sources_reuses_everything(world):
    do_publish(world, "set-1", base="none")
    before = {s: files(world["root"], s) for s in ("cells", "trees", "history")}
    out = do_publish(world, "set-2", base="none")
    assert out["stores"] == {"cells": {"new": 0, "reused": 3},
                             "trees": {"new": 0, "reused": 3},
                             "history": {"new": 0, "reused": 2}}
    assert out["history_rebuilt"] == []
    assert {s: files(world["root"], s) for s in before} == before
    assert members(world["root"], "set-2") == members(world["root"], "set-1")
    assert len(Registry(registry_path(world["root"])).versions()) == 8


def test_changed_cell_gets_v2_and_only_that(world):
    do_publish(world, "set-1", base="none")
    make_cell_db(world["cells"] / f"cell-{MNO_A}-{R1}.db", MNO_A, R1, "fp-a1-NEW")
    out = do_publish(world, "set-2", base="none")
    assert out["stores"]["cells"] == {"new": 1, "reused": 2}
    assert out["stores"]["trees"] == {"new": 0, "reused": 3}
    assert out["stores"]["history"] == {"new": 0, "reused": 2}
    ms = members(world["root"], "set-2")
    assert ms[f"cells/{MNO_A}__{R1}"] == 2
    assert f"cell-{MNO_A}-{R1}-v2.db" in files(world["root"], "cells")
    assert f"cell-{MNO_A}-{R1}-v1.db" in files(world["root"], "cells")   # v1 stays


def test_same_source_newer_schema_is_new_content(world):
    do_publish(world, "set-1", base="none")
    make_cell_db(world["cells"] / f"cell-{MNO_B}-{R1}.db", MNO_B, R1, "fp-b1", schema="3")
    out = do_publish(world, "set-2", base="none")
    assert out["stores"]["cells"] == {"new": 1, "reused": 2}
    assert members(world["root"], "set-2")[f"cells/{MNO_B}__{R1}"] == 2


def test_changed_trees_rebuild_history_for_that_mno_only(world):
    do_publish(world, "set-1", base="none")
    write_parse(world["env"], MNO_A, R2, "docA",
                [_req("REQ_A_1", section="1"), _req("REQ_A_3", "REQ_A_1")])
    out = do_publish(world, "set-2", base="none")
    assert out["stores"]["trees"] == {"new": 1, "reused": 2}
    assert out["stores"]["history"] == {"new": 1, "reused": 1}
    assert out["history_rebuilt"] == [MNO_A]
    ms = members(world["root"], "set-2")
    assert ms[f"trees/{MNO_A}__{R2}"] == 2 and ms[f"trees/{MNO_A}__{R1}"] == 1
    assert ms[f"history/{MNO_A}"] == 2 and ms[f"history/{MNO_B}"] == 1
    # the rebuilt History sees the new requirement in its latest release
    reg = Registry(registry_path(world["root"]))
    h = next(m for m in reg.members("set-2") if m.key == ("history", MNO_A))
    hist = open_history_store("", url="") if False else None  # noqa: F841 (explicit paths below)
    from core.src.stores.history import SqliteHistoryStore
    st = SqliteHistoryStore(db_paths=[world["root"] / h.file])
    assert st.releases(MNO_A) == [R1, R2]
    assert st.req_history(MNO_A, "PLAN_X", "REQ_A_3")[-1]["valid_from"] == R2


def test_partial_publish_inherits_the_rest_from_the_base_set(world):
    do_publish(world, "set-1", base="none")
    pub.gate(world["root"], "set-1", "manual", "waived", note="first")
    pub.flip(world["root"], "set-1")
    make_cell_db(world["cells"] / f"cell-{MNO_B}-{R1}.db", MNO_B, R1, "fp-b1-NEW")
    # only one cell named; Trees/History not supplied at all
    out = do_publish(world, "set-2", cells_filter=[f"{MNO_B}__{R1}"], env_dir="")
    assert out["base"] == "set-1"
    assert out["stores"] == {"cells": {"new": 1, "reused": 2},
                             "trees": {"new": 0, "reused": 3},
                             "history": {"new": 0, "reused": 2}}
    ms = members(world["root"], "set-2")
    assert len(ms) == 8 and ms[f"cells/{MNO_B}__{R1}"] == 2


def test_duplicate_label_and_unknown_base_are_refused(world):
    do_publish(world, "set-1", base="none")
    with pytest.raises(PipelineError) as ei:
        do_publish(world, "set-1", base="none")
    assert ei.value.code == "PUB-E001"
    with pytest.raises(PipelineError) as ei:
        do_publish(world, "set-2", base="nope")
    assert ei.value.code == "PUB-E006"


def test_unpaired_units_are_warned_not_refused(world):
    # parse output for a release that has no SIRA cell (and vice versa)
    write_parse(world["env"], MNO_B, R2, "docB", [_req("REQ_B_9", section="1")])
    (world["cells"] / f"cell-{MNO_B}-{R1}.db").unlink()
    out = do_publish(world, "set-1", base="none")
    assert out["warnings"] == [
        f"trees/{MNO_B}__{R2}: no cells unit in the set (parse output without a SIRA cell)",
        f"trees/{MNO_B}__{R1}: no cells unit in the set (parse output without a SIRA cell)"]
    assert out["stores"]["cells"] == {"new": 2, "reused": 0}
    assert out["stores"]["trees"] == {"new": 4, "reused": 0}
    make_cell_db(world["cells"] / f"cell-{MNO_B}-{R1}.db", MNO_B, R1, "fp-b1")
    out = do_publish(world, "set-2", base="none", env_dir="")
    assert out["warnings"] == [f"cells/{MNO_A}__{R2}: no trees unit in the set",
                               f"cells/{MNO_A}__{R1}: no trees unit in the set",
                               f"cells/{MNO_B}__{R1}: no trees unit in the set"]


def test_nothing_to_publish(world):
    with pytest.raises(PipelineError) as ei:
        pub.publish(world["root"], "empty", base="none")
    assert ei.value.code == "PUB-E003"


def test_dry_run_writes_nothing(world):
    out = do_publish(world, "set-1", base="none", dry_run=True)
    assert out["dry_run"] is True
    assert out["stores"]["cells"]["new"] == 3 and out["history_rebuilt"] == [MNO_A, MNO_B]
    assert not (world["root"] / "cells").exists()
    assert Registry(registry_path(world["root"])).release_sets() == []


class _FailingTransfer(pub.LocalTransfer):
    """Fails on the N-th put — a crash mid-stage."""

    def __init__(self, root, fail_at):
        super().__init__(root)
        self.n, self.fail_at = 0, fail_at

    def put(self, src, rel):
        self.n += 1
        if self.n == self.fail_at:
            raise OSError("disk full")
        return super().put(src, rel)


def test_failed_stage_leaves_no_visible_unit(world):
    world["work"].mkdir()
    with pytest.raises(OSError):
        pub.publish(world["root"], "set-1", cells_src=str(world["cells"]),
                    env_dir=str(world["env"]), base="none", work_dir=str(world["work"]),
                    transfer=_FailingTransfer(world["root"], fail_at=3))
    root = world["root"]
    assert files(root, "cells") == [] and files(root, "trees") == [] and files(root, "history") == []
    reg = Registry(registry_path(root))
    assert reg.release_sets() == [] and reg.versions() == []
    # the registry is usable afterwards
    do_publish(world, "set-1", base="none")
    assert len(members(root, "set-1")) == 8


def test_unit_source_needs_identity(tmp_path):
    bad = tmp_path / "cell-x-y.db"
    con = sqlite3.connect(bad)
    con.execute("CREATE TABLE meta(key TEXT, value TEXT)")
    con.commit(); con.close()
    with pytest.raises(PipelineError) as ei:
        pub.unit_source("cells", bad)
    assert ei.value.code == "PUB-E002"
    with pytest.raises(PipelineError) as ei:
        pub.unit_source("cells", tmp_path / "missing.db")
    assert ei.value.code == "PUB-E002"


# ---------------------------------------------------------------------------
# gate / flip / rollback

def test_flip_refused_without_gate_then_allowed(world):
    do_publish(world, "set-1", base="none")
    root = world["root"]
    with pytest.raises(PipelineError) as ei:
        pub.flip(root, "set-1")
    assert ei.value.code == "PUB-E004" and "no gate run" in ei.value.message
    pub.gate(root, "set-1", "golden-stage1", "pass", run_ref="run-7")
    res = pub.flip(root, "set-1")
    assert res["from_label"] == "" and res["label"] == "set-1"
    assert len(res["changed"]) == 8 and res["removed"] == []
    reg = Registry(registry_path(root))
    assert reg.active_label() == "set-1"
    assert {(r["store"], r["unit"]): r["version"] for r in reg.active()} == \
        {m.key: m.version for m in reg.members("set-1")}
    f = reg.flips()[0]
    assert (f["label"], f["kind"], f["mode"], f["from_label"]) == ("set-1", "flip", "staged", "")


def test_gate_verdict_latest_per_kind(world):
    do_publish(world, "set-1", base="none")
    root = world["root"]
    reg = Registry(registry_path(root))
    pub.gate(root, "set-1", "golden-stage1", "fail")
    assert reg.gate_verdict("set-1") == (False, {"golden-stage1": "fail"})
    pub.gate(root, "set-1", "golden-stage1", "pass")          # rerun passes
    assert reg.gate_verdict("set-1") == (True, {"golden-stage1": "pass"})
    pub.gate(root, "set-1", "golden-stage2", "fail")          # another kind fails
    ok, detail = reg.gate_verdict("set-1")
    assert not ok and detail == {"golden-stage1": "pass", "golden-stage2": "fail"}
    with pytest.raises(PipelineError) as ei:
        pub.flip(root, "set-1")
    assert "golden-stage2=fail" in ei.value.message
    with pytest.raises(PipelineError) as ei:
        pub.gate(root, "nope", "manual", "pass")
    assert ei.value.code == "PUB-E006"


def test_force_flip_records_waived_manual_gate(world):
    do_publish(world, "set-1", base="none")
    root = world["root"]
    with pytest.raises(PipelineError) as ei:
        pub.flip(root, "set-1", force=True)                   # no reason
    assert ei.value.code == "PUB-E004"
    pub.flip(root, "set-1", force=True, reason="hotfix, eval pending")
    reg = Registry(registry_path(root))
    gates = reg.gates("set-1")
    assert [(g["kind"], g["verdict"], g["note"]) for g in gates] == \
        [("manual", "waived", "hotfix, eval pending")]
    assert reg.flips()[0]["mode"] == "override"


def test_flip_and_rollback_are_one_transaction_each(world, monkeypatch):
    do_publish(world, "set-1", base="none")
    root = world["root"]
    pub.gate(root, "set-1", "manual", "waived")
    pub.flip(root, "set-1")
    make_cell_db(world["cells"] / f"cell-{MNO_A}-{R1}.db", MNO_A, R1, "fp-a1-NEW")
    do_publish(world, "set-2")
    pub.gate(root, "set-2", "golden-stage1", "pass")
    res = pub.flip(root, "set-2")
    assert res["from_label"] == "set-1" and res["changed"] == [f"cells/{MNO_A}__{R1}"]
    reg = Registry(registry_path(root))
    assert reg.active_label() == "set-2"

    # a crash between the active rows and the flip row is not observable:
    # make the flip-row insert fail and check active is untouched
    real_connect = Registry._connect

    def broken(self):
        con = real_connect(self)

        class _Con:
            def __getattr__(s, name):
                return getattr(con, name)

            def execute(s, sql, *a):
                if sql.lstrip().startswith("INSERT INTO flip"):
                    raise sqlite3.OperationalError("simulated crash")
                return con.execute(sql, *a)
        return _Con()

    monkeypatch.setattr(Registry, "_connect", broken)
    with pytest.raises(sqlite3.OperationalError):
        reg.flip("set-1", kind="rollback", mode="expedited")
    monkeypatch.setattr(Registry, "_connect", real_connect)
    assert reg.active_label() == "set-2"
    assert len(reg.flips()) == 2

    # the real rollback
    res = pub.rollback(root, "set-1")
    assert res["kind"] == "rollback" and res["from_label"] == "set-2"
    assert reg.active_label() == "set-1"
    assert reg.flips()[0]["mode"] == "expedited"
    assert {r["unit"]: r["version"] for r in reg.active() if r["store"] == "cells"}[f"{MNO_A}__{R1}"] == 1


def test_rollback_target_must_have_served(world):
    do_publish(world, "set-1", base="none")
    with pytest.raises(PipelineError) as ei:
        pub.rollback(world["root"], "set-1")
    assert ei.value.code == "PUB-E009"
    pub.rollback(world["root"], "set-1", force=True)
    assert Registry(registry_path(world["root"])).active_label() == "set-1"
    with pytest.raises(PipelineError) as ei:
        pub.flip(world["root"], "nope")
    assert ei.value.code == "PUB-E006"


def test_flip_refuses_when_a_member_file_is_missing(world):
    do_publish(world, "set-1", base="none")
    root = world["root"]
    pub.gate(root, "set-1", "manual", "waived")
    (root / "trees" / f"trees-{MNO_B}-{R1}-v1.db").unlink()
    with pytest.raises(PipelineError) as ei:
        pub.flip(root, "set-1")
    assert ei.value.code == "PUB-E005"
    assert Registry(registry_path(root)).active() == []


def test_verify_serving_compares_healthz_with_registry(world, monkeypatch):
    do_publish(world, "set-1", base="none")
    root = world["root"]
    pub.gate(root, "set-1", "manual", "waived")
    reg = Registry(registry_path(root))
    want = {}
    for m in reg.members("set-1"):
        want.setdefault(m.store, {})[m.unit] = {"version": m.version, "fingerprint": m.fingerprint}
    calls = []

    def fake_http(method, url, timeout=60.0):
        calls.append((method, url))
        if url.endswith("/registry/reload"):
            return {"changed": {}}
        return {"registry_label": fake_http.label, "registry_versions": fake_http.versions,
                "data_fingerprint": "abc123"}
    fake_http.label, fake_http.versions = "set-1", want
    monkeypatch.setattr(pub, "_http_json", fake_http)

    res = pub.flip(root, "set-1", store_url="http://127.0.0.1:1/")
    assert res["verify"] == {"registry_label": "set-1", "units": 8, "data_fingerprint": "abc123"}
    assert calls == [("POST", "http://127.0.0.1:1/registry/reload"),
                     ("GET", "http://127.0.0.1:1/healthz")]

    fake_http.label = "other"
    with pytest.raises(PipelineError) as ei:
        pub.rollback(root, "set-1", store_url="http://127.0.0.1:1")
    assert ei.value.code == "PUB-E008" and "registry_label=other" in ei.value.message
    fake_http.label = "set-1"
    fake_http.versions = {**want, "cells": {**want["cells"], f"{MNO_A}__{R1}": {"version": 9}}}
    with pytest.raises(PipelineError) as ei:
        pub.rollback(root, "set-1", store_url="http://127.0.0.1:1")
    assert "1 wrong" in ei.value.message


# ---------------------------------------------------------------------------
# gc

def test_gc_keeps_active_plus_n_and_flip_targets(world):
    root = world["root"]
    do_publish(world, "set-1", base="none")
    for i, fp in ((2, "fp-a1-v2"), (3, "fp-a1-v3"), (4, "fp-a1-v4")):
        make_cell_db(world["cells"] / f"cell-{MNO_A}-{R1}.db", MNO_A, R1, fp)
        do_publish(world, f"set-{i}", base="none")
    pub.gate(root, "set-3", "manual", "waived")
    pub.flip(root, "set-3")
    cell = f"cells/{MNO_A}__{R1}"
    before = files(root, "cells")
    assert len(before) == 6                                   # 4 versions of one cell + 2

    plan = pub.gc(root, keep=1, dry_run=True)
    assert plan["dry_run"] and files(root, "cells") == before
    # protected: active (set-3), last 1 set (set-4), last 1 flip (set-3)
    assert plan["protected_labels"] == ["set-3", "set-4"]
    assert plan["removed"] == [f"{cell}@v1", f"{cell}@v2"]

    out = pub.gc(root, keep=1)
    assert out["removed"] == [f"{cell}@v1", f"{cell}@v2"] and out["freed_bytes"] > 0
    assert f"cell-{MNO_A}-{R1}-v1.db" not in files(root, "cells")
    assert f"cell-{MNO_A}-{R1}-v2.db" not in files(root, "cells")
    assert f"cell-{MNO_A}-{R1}-v3.db" in files(root, "cells")
    assert f"cell-{MNO_A}-{R1}-v4.db" in files(root, "cells")
    reg = Registry(registry_path(root))
    v1 = reg.get_version("cells", f"{MNO_A}__{R1}", 1)
    assert v1 is not None and v1.removed_at                   # row stays
    assert len(reg.members("set-1")) == 8                     # provenance intact
    # a removed version is not reused by a later publish of the same content
    make_cell_db(world["cells"] / f"cell-{MNO_A}-{R1}.db", MNO_A, R1, "fp-a1")
    out = do_publish(world, "set-5", base="none")
    assert members(root, "set-5")[cell] == 5
    # flipping to a GC'd set is refused
    with pytest.raises(PipelineError) as ei:
        pub.flip(root, "set-1", force=True, reason="x")
    assert ei.value.code == "PUB-E005"
    # orphan report: a file no row knows
    (root / "cells" / f"cell-{MNO_B}-{R2}-v9.db").write_bytes(b"x")
    assert pub.gc(root, keep=1, dry_run=True)["orphan_files"] == [f"cells/cell-{MNO_B}-{R2}-v9.db"]


# ---------------------------------------------------------------------------
# serving-side resolution

def test_factories_resolve_from_the_registry(world, monkeypatch):
    root = world["root"]
    do_publish(world, "set-1", base="none")
    monkeypatch.setenv(REGISTRY_ENV, str(registry_path(root)))
    monkeypatch.delenv(RELEASE_SET_ENV, raising=False)
    monkeypatch.delenv("NORA_TREES_ROOT", raising=False)
    monkeypatch.delenv("NORA_HISTORY_ROOT", raising=False)
    # nothing active yet → empty stores, not an error
    assert open_tree_store(world["env"], url="").list_cells() == []
    assert open_history_store(url="").list_mnos() == []
    # a staged set by name
    monkeypatch.setenv(RELEASE_SET_ENV, "set-1")
    ts = open_tree_store(world["env"], url="")
    assert [(c["mno"], c["release"]) for c in ts.list_cells()] == \
        [(MNO_A, R1), (MNO_A, R2), (MNO_B, R1)]
    assert open_history_store(url="").list_mnos() == [MNO_A, MNO_B]
    assert len(registry_store_paths("cells")) == 3
    # active after a flip, env var cleared
    monkeypatch.delenv(RELEASE_SET_ENV)
    pub.gate(root, "set-1", "manual", "waived")
    pub.flip(root, "set-1")
    assert len(open_tree_store(world["env"], url="").list_cells()) == 3
    # an explicit root still wins over the registry
    monkeypatch.setenv("NORA_TREES_ROOT", str(world["work"]))
    assert open_tree_store(world["env"], url="").list_cells() == []
    # missing registry: the factory reports, the host decides
    monkeypatch.setenv(REGISTRY_ENV, str(root / "nope.db"))
    monkeypatch.delenv("NORA_TREES_ROOT")
    from core.src.stores.registry import RegistryError
    with pytest.raises(RegistryError) as ei:
        open_tree_store(world["env"], url="")
    assert ei.value.code == "REG-E001"


# ---------------------------------------------------------------------------
# CLI

def test_cli_round_trip(world, capsys):
    root = str(world["root"])
    work = str(world["work"]); world["work"].mkdir()
    base = ["--serve-root", root]
    assert pub.main(["publish", *base, "--label", "set-1", "--cells-src", str(world["cells"]),
                     "--env-dir", str(world["env"]), "--base", "none", "--work-dir", work,
                     "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert pub.main(["publish", *base, "--label", "set-1", "--cells-src", str(world["cells"]),
                     "--env-dir", str(world["env"]), "--base", "none", "--work-dir", work,
                     "--note", "first set"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stores"]["cells"]["new"] == 3
    assert pub.main(["flip", *base, "--label", "set-1"]) == 1
    assert "[PUB-E004]" in capsys.readouterr().err
    assert pub.main(["gate", *base, "--label", "set-1", "--kind", "golden-stage1",
                     "--verdict", "pass", "--run-ref", "run-1"]) == 0
    capsys.readouterr()
    assert pub.main(["flip", *base, "--label", "set-1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["label"] == "set-1" and out["rollback"].endswith("--to <label>")
    assert pub.main(["status", *base, "--label", "set-1"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["active_label"] == "set-1" and len(st["members"]) == 8
    assert st["release_sets"][0]["gate_ok"] is True and st["release_sets"][0]["served_before"]
    assert pub.main(["rollback", *base, "--to", "set-2"]) == 1
    assert "[PUB-E006]" in capsys.readouterr().err
    assert pub.main(["gc", *base, "--keep", "2", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["removed"] == []
    assert pub.main(["status", "--serve-root", str(world["work"])]) == 1
    assert "[REG-E001]" in capsys.readouterr().err
    assert all(not any(c in o for c in ("body of", "title ")) for o in [json.dumps(st), json.dumps(out)])
