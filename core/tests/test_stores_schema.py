"""Schema-evolution tests for the SQLite stores (strand corp-db-layer S6;
store-split-design §9): a reader opens a file with an unknown extra column
and one missing an additive column and reads the same data; a file with an
unsupported `schema_version` is refused with a clear, coded error.

Synthetic data only — generic ids and release labels.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from core.src.stores import history_build, trees_build
from core.src.stores.errors import SchemaVersionError
from core.src.stores.history import SqliteHistoryStore
from core.src.stores.overlay import SqliteOverlayStore
from core.src.stores.trees import SqliteTreeStore
from core.tests.test_stores_history import MNO as H_MNO
from core.tests.test_stores_history import _corpus as history_corpus
from core.tests.test_stores_trees import _corpus as trees_corpus

pytestmark = pytest.mark.skipif(
    sqlite3.sqlite_version_info < (3, 35), reason="needs DROP COLUMN (SQLite 3.35)")


def _alter(path: Path, *stmts: str) -> None:
    con = sqlite3.connect(str(path))
    try:
        for s in stmts:
            con.execute(s)
        con.commit()
    finally:
        con.close()


def _variants(src: Path, add: str, drop: str, bump: str) -> tuple[Path, Path, Path]:
    """Copies of `src`: one with an unknown column, one missing an
    additive column, one with a foreign schema_version."""
    out = []
    for name, stmt in (("extra", add), ("missing", drop), ("bumped", bump)):
        d = src.parent / name
        d.mkdir()
        p = d / src.name
        shutil.copy(src, p)
        _alter(p, stmt)
        out.append(d)
    return tuple(out)


def test_trees_tolerance_and_refusal(tmp_path):
    env, out = tmp_path / "env", tmp_path / "trees"
    trees_corpus(env)
    assert trees_build.main(["--env-dir", str(env), "--out", str(out)]) == 0
    src = sorted(out.glob("*.db"))[0]
    base = SqliteTreeStore(db_paths=[src])
    extra, missing, bumped = _variants(
        src,
        "ALTER TABLE node ADD COLUMN future_field TEXT NOT NULL DEFAULT 'x'",
        "ALTER TABLE node DROP COLUMN is_requirement",       # nullable, unread
        "UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    for root in (extra, missing):
        st = SqliteTreeStore(root)
        assert st.list_cells() == base.list_cells()
        mno, rel = st.list_cells()[0].values()
        for doc in st.list_docs(mno, rel):
            assert st.load_tree(doc, mno, rel) == base.load_tree(doc, mno, rel)
        assert st.find_req("REQ_A_0002", mno, rel) == base.find_req("REQ_A_0002", mno, rel)
        assert st.subtree(mno, rel, "REQ_A_0001") == base.subtree(mno, rel, "REQ_A_0001")
    with pytest.raises(SchemaVersionError) as exc:
        SqliteTreeStore(bumped).list_cells()
    assert "TRS-E007" in str(exc.value) and "'99'" in str(exc.value)
    assert "trees_build" in str(exc.value)


def test_history_tolerance_and_refusal(tmp_path):
    env, out = tmp_path / "env", tmp_path / "history"
    history_corpus(env)
    assert history_build.main(["--env-dir", str(env), "--out", str(out)]) == 0
    src = sorted(out.glob("*.db"))[0]
    base = SqliteHistoryStore(db_paths=[src])
    extra, missing, bumped = _variants(
        src,
        "ALTER TABLE version ADD COLUMN future_field TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE node DROP COLUMN parent_key",           # written, never read
        "UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    for root in (extra, missing):
        st = SqliteHistoryStore(root)
        assert st.list_mnos() == base.list_mnos() == [H_MNO]
        assert st.releases(H_MNO) == base.releases(H_MNO)
        assert st.req_history(H_MNO, "PLANA", "R1") == base.req_history(H_MNO, "PLANA", "R1")
        assert st.diff_releases(H_MNO, "", "", True) == base.diff_releases(H_MNO, "", "", True)
        assert st.plan_history(H_MNO, "PLANA") == base.plan_history(H_MNO, "PLANA")
    with pytest.raises(SchemaVersionError) as exc:
        SqliteHistoryStore(bumped).list_mnos()
    assert "HIS-E006" in str(exc.value) and "history_build" in str(exc.value)


def test_overlay_tolerance_and_refusal(tmp_path):
    src = tmp_path / "base" / "ovl.db"
    base = SqliteOverlayStore(src)
    rec = {"word": "handover", "label": "c1", "reason": {"category": "x", "note": ""},
           "by": "expert", "at": "2031-01-15T12:00:00+00:00",
           "origin": {"release": "Jan2031"}, "fingerprint": "fp1"}
    base.put_entry("GP", "R1", {"remove": [rec], "add": [{**rec, "word": "t3402"}]})
    base.put_entry("GP", "R2", {"suppress_all": {**rec, "word": "", "value": True}})
    base.set_accepted_labels({"c1"})
    extra, missing, bumped = _variants(
        src,
        "ALTER TABLE record ADD COLUMN future_field TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE record DROP COLUMN fingerprint",        # additive, defaulted
        "UPDATE meta SET value = '7' WHERE key = 'schema_version'")
    for read_only in (True, False):
        st = SqliteOverlayStore(extra / "ovl.db", read_only=read_only)
        assert st.get_overlay("GP") == base.get_overlay("GP")
        assert st.get_entry("GP", "R2") == base.get_entry("GP", "R2")
        assert st.accepted_labels() == {"c1"}
        st = SqliteOverlayStore(missing / "ovl.db", read_only=read_only)
        want = {rid: {d: ([{k: v for k, v in r.items() if k != "fingerprint"} for r in recs]
                          if isinstance(recs, list)
                          else {k: v for k, v in recs.items() if k != "fingerprint"})
                      for d, recs in entry.items()}
                for rid, entry in base.get_overlay("GP").items()}
        assert st.get_overlay("GP") == want
        assert st.list_mnos() == ["GP"]
        for read_only in (True, False):
            with pytest.raises(SchemaVersionError) as exc:
                SqliteOverlayStore(bumped / "ovl.db", read_only=read_only).get_overlay("GP")
            assert "OVL-E005" in str(exc.value) and "'7'" in str(exc.value)
    # a writer refuses too, before touching the file
    with pytest.raises(SchemaVersionError):
        SqliteOverlayStore(bumped / "ovl.db").put_entry("GP", "R3", {"add": [rec]})
    # a missing column stays missing: the file is not migrated by a read
    con = sqlite3.connect(str(missing / "ovl.db"))
    assert "fingerprint" not in {r[1] for r in con.execute("PRAGMA table_info(record)")}
    con.close()


def test_overlay_read_never_creates_the_file(tmp_path):
    st = SqliteOverlayStore(tmp_path / "new.db")        # read-write open
    assert st.get_overlay("GP") == {} and st.list_mnos() == []
    assert not (tmp_path / "new.db").exists()
    st.put_entry("GP", "R1", {"add": [{"word": "w"}]})
    assert (tmp_path / "new.db").exists()
