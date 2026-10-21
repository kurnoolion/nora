"""Tests for core/src/stores/overlay.py + overlay_import.py (strand
corp-db-layer S3): JSON/SQLite backend parity under every
EnrichOverlayStore op, transactions, read-only opens, the import CLI."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone

import pytest

import core.src.web.enrich_overlay_store as eos
from core.src.pipeline.error_codes import CODES
from core.src.stores import overlay_import
from core.src.stores.overlay import (
    JsonOverlayStore,
    OverlayStore,
    SqliteOverlayStore,
    open_overlay_store,
)
from core.src.stores.overlay import OverlayReadOnlyError
from core.src.web.enrich_overlay_store import EnrichOverlayStore


class _FixedClock:
    """Freezes the edit stamp's `at` so two backends stamp identically."""
    now_value = datetime(2031, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.now_value


@pytest.fixture()
def frozen(monkeypatch):
    monkeypatch.setattr(eos, "datetime", _FixedClock)


@pytest.fixture()
def pair(tmp_path, frozen):
    """(JSON-backed, SQLite-backed) EnrichOverlayStore."""
    j = EnrichOverlayStore(tmp_path / "corr")
    s = EnrichOverlayStore("", backend=SqliteOverlayStore(tmp_path / "ovl.db"))
    return j, s


def _both(pair, method, *args, **kw):
    a = getattr(pair[0], method)(*args, **kw)
    b = getattr(pair[1], method)(*args, **kw)
    assert a == b, method
    return a


def _assert_parity(pair, mnos=("GP", "XB"), labels=("", "c1", "c2")):
    j, s = pair
    assert j.list_mnos() == s.list_mnos()
    for mno in mnos:
        assert j.get_overlay(mno) == s.get_overlay(mno)
        # byte-identical serialization (key order included)
        assert json.dumps(j.get_overlay(mno)) == json.dumps(s.get_overlay(mno))
        for rid in ("R1", "R2", "R3", "nope"):
            assert j.get_entry(mno, rid) == s.get_entry(mno, rid)
        for label in labels:
            assert j.overlay_digest(mno, label) == s.overlay_digest(mno, label)
    assert j.accepted_labels() == s.accepted_labels()
    assert j.disabled_labels() == s.disabled_labels()
    assert j.label_counts() == s.label_counts()
    assert j.label_req_counts() == s.label_req_counts()
    assert j.label_counts(["GP"]) == s.label_counts(["GP"])
    assert j.reason_categories() == s.reason_categories()


def _stamp(**kw):
    kw.setdefault("label", "c1")
    kw.setdefault("reason", {"category": "too-generic", "note": "n"})
    kw.setdefault("by", "expert")
    kw.setdefault("origin_release", "Jan2031")
    return kw


class TestBackendParity:
    def test_full_op_sequence(self, pair):
        _assert_parity(pair)
        steps = [
            ("GP", "R1", "remove", dict(words=["handover", "retry"], fingerprint="fp1")),
            ("GP", "R1", "add", dict(words=["t3402", "t3410", "t3402"])),
            ("GP", "R1", "remove", dict(words=["handover"], label="c2")),  # replace
            ("GP", "R2", "suppress", dict(fingerprint="fp2")),
            ("GP", "R2", "add", dict(words=["only this"])),
            ("XB", "R1", "remove", dict(words=["roaming"], origin_release="Oct2030")),
            ("GP", "R1", "unadd", dict(words=["t3402"])),
            ("GP", "R1", "reaffirm", dict(pairs=[{"direction": "remove", "word": "retry"}],
                                          fingerprint="fp1b")),
            ("XB", "R1", "reaffirm", dict(pairs=[{"direction": "remove", "word": "roaming"}])),
            ("GP", "R2", "reaffirm", dict(pairs=[{"direction": "suppress_all", "word": ""}],
                                          origin_release="Apr2031")),
            ("GP", "R3", "unadd", dict(words=["never there"])),        # no-op edit
            ("GP", "R1", "discard", dict(pairs=[{"direction": "add", "word": "t3410"}])),
            ("GP", "R2", "unsuppress", {}),
            ("GP", "R3", "add", dict(words=["x"], label="c2")),
        ]
        for mno, rid, op, kw in steps:
            _both(pair, "edit", mno, rid, op, **_stamp(**kw))
            _assert_parity(pair)

        _both(pair, "set_label_merged", "c2", True)
        _assert_parity(pair)
        _both(pair, "set_label_disabled", "c1", True)
        _both(pair, "set_label_disabled", "c2", True)
        _assert_parity(pair)
        _both(pair, "add_reason_category", "new-cat")
        _both(pair, "add_reason_category", "new-cat")
        _assert_parity(pair)
        assert _both(pair, "delete_label", "c2") > 0
        _assert_parity(pair)
        _both(pair, "set_label_merged", "c1", True)
        _both(pair, "set_label_merged", "c1", False)
        _assert_parity(pair)
        assert _both(pair, "delete_label", "c1") > 0
        _assert_parity(pair)

    def test_fingerprint_stamps(self, pair):
        j, s = pair
        for st in (j, s):
            e = st.edit("GP", "R1", "remove", words=["a"], **_stamp(fingerprint="fp1"))
            assert e["remove"][0]["fingerprint"] == "fp1"
            e = st.edit("GP", "R1", "add", words=["b"], **_stamp())
            assert "fingerprint" not in e["add"][0]          # legacy shape
            e = st.edit("GP", "R1", "suppress", **_stamp(fingerprint="fp1"))
            assert e["suppress_all"]["fingerprint"] == "fp1"
            # reaffirm re-stamps; without a fingerprint the stale one goes
            e = st.edit("GP", "R1", "reaffirm",
                        pairs=[{"direction": "remove", "word": "a"},
                               {"direction": "suppress_all", "word": ""}],
                        **_stamp(origin_release="Apr2031", fingerprint="fp2"))
            assert e["remove"][0]["fingerprint"] == "fp2"
            assert e["remove"][0]["origin"] == {"release": "Apr2031"}
            assert e["suppress_all"]["fingerprint"] == "fp2"
            e = st.edit("GP", "R1", "reaffirm",
                        pairs=[{"direction": "remove", "word": "a"}], **_stamp())
            assert "fingerprint" not in e["remove"][0]
        _assert_parity(pair)

    def test_record_order_is_insertion_order(self, pair):
        words = ["zeta", "alpha", "mid", "beta"]
        _both(pair, "edit", "GP", "R1", "add", words=words, **_stamp())
        _both(pair, "edit", "GP", "R1", "add", words=["alpha"], **_stamp(label="c2"))
        got = [r["word"] for r in pair[1].get_entry("GP", "R1")["add"]]
        assert got == ["zeta", "mid", "beta", "alpha"]   # re-edit moves to end
        _assert_parity(pair)

    def test_disabled_store_and_defaults(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NORA_OVERLAY_DB", raising=False)
        st = EnrichOverlayStore("")
        assert not st.enabled and st.get_overlay("GP") == {}
        assert st.reason_categories() == eos.DEFAULT_REASON_CATEGORIES
        with pytest.raises(RuntimeError):
            st.edit("GP", "R1", "remove", words=["a"])
        sq = EnrichOverlayStore("", backend=SqliteOverlayStore(tmp_path / "o.db"))
        assert sq.reason_categories() == eos.DEFAULT_REASON_CATEGORIES
        assert sq.overlay_mtime("GP") == 0.0

    def test_env_db_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NORA_OVERLAY_DB", str(tmp_path / "o.db"))
        st = EnrichOverlayStore(tmp_path / "corr")
        assert isinstance(st.backend, SqliteOverlayStore)
        st.edit("GP", "R1", "remove", words=["a"], **_stamp())
        assert not (tmp_path / "corr").exists()


class TestSqliteBackend:
    def test_protocol_conformance(self, tmp_path):
        assert isinstance(SqliteOverlayStore(tmp_path / "o.db"), OverlayStore)
        assert isinstance(JsonOverlayStore(tmp_path), OverlayStore)

    def test_version_is_monotonic(self, tmp_path):
        st = SqliteOverlayStore(tmp_path / "o.db")
        assert st.version("GP") == 0.0
        seen = []
        for i in range(5):
            st.put_entry("GP", f"R{i}", {"add": [{"word": "w"}]})
            seen.append(st.version("GP"))
        assert seen == sorted(seen) and len(set(seen)) == 5
        assert st.version("GP") >= time.time() - 60
        assert st.version("XB") == 0.0

    def test_legacy_shapes_roundtrip_exactly(self, tmp_path):
        st = SqliteOverlayStore(tmp_path / "o.db")
        entry = {
            "remove": [{"word": "a"},                                  # sparse
                       {"word": "b", "label": "", "extra": 1,          # extra key
                        "reason": {"category": "c", "note": "", "x": 2},
                        "by": "t", "at": "x", "origin": {"release": "Jan2031"}},
                       {"word": "c", "label": "", "reason": {}, "by": "",
                        "at": "", "origin": {"release": ""}, "fingerprint": ""}],
            "suppress_all": {"value": False, "label": "l"},
        }
        st.put_entry("GP", "R1", entry)
        assert st.get_entry("GP", "R1") == entry
        assert st.get_overlay("GP") == {"R1": entry}

    def test_schema_is_portable_types_only(self, tmp_path):
        st = SqliteOverlayStore(tmp_path / "o.db")
        st.put_entry("GP", "R1", None)
        con = sqlite3.connect(tmp_path / "o.db")
        types = {row[2].split("(")[0].upper()
                 for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                 for row in con.execute(f"PRAGMA table_info({t})")}
        assert types <= {"TEXT", "INTEGER", "REAL"}
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    def test_direction_check_constraint(self, tmp_path):
        st = SqliteOverlayStore(tmp_path / "o.db")
        st.put_entry("GP", "R1", None)
        con = sqlite3.connect(tmp_path / "o.db")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO record(mno, req_id, direction, word, seq) "
                        "VALUES ('GP','R1','bogus','w',0)")


class TestTransactions:
    @pytest.mark.parametrize("kind", ["json", "sqlite"])
    def test_rollback_on_error(self, tmp_path, kind):
        st = (JsonOverlayStore(tmp_path) if kind == "json"
              else SqliteOverlayStore(tmp_path / "o.db"))
        st.put_entry("GP", "R1", {"add": [{"word": "keep"}]})
        with pytest.raises(ValueError):
            with st.transaction():
                st.put_entry("GP", "R1", None)
                st.set_accepted_labels({"x"})
                assert st.get_entry("GP", "R1") is None    # sees own write
                raise ValueError("boom")
        assert st.get_entry("GP", "R1") == {"add": [{"word": "keep"}]}
        assert st.accepted_labels() == set()

    def test_sqlite_writer_excludes_second_writer(self, tmp_path):
        a = SqliteOverlayStore(tmp_path / "o.db")
        b = SqliteOverlayStore(tmp_path / "o.db", busy_timeout_ms=50)
        a.put_entry("GP", "R0", None)
        with a.transaction():
            a.put_entry("GP", "R1", {"add": [{"word": "a"}]})
            with pytest.raises(sqlite3.OperationalError):
                b.put_entry("GP", "R2", {"add": [{"word": "b"}]})
            # a concurrent reader sees the last committed state (WAL)
            assert b.get_entry("GP", "R1") is None
        assert b.get_entry("GP", "R1") == {"add": [{"word": "a"}]}

    def test_json_lock_serializes_writers(self, tmp_path):
        a, b = JsonOverlayStore(tmp_path), JsonOverlayStore(tmp_path)
        entered, order = threading.Event(), []

        def second():
            entered.wait()
            with b.transaction():
                order.append("b")

        t = threading.Thread(target=second)
        t.start()
        with a.transaction():
            entered.set()
            time.sleep(0.2)
            order.append("a")
        t.join(5)
        assert order == ["a", "b"]

    def test_nested_transaction_joins(self, tmp_path):
        st = SqliteOverlayStore(tmp_path / "o.db")
        with st.transaction():
            with st.transaction():
                st.put_entry("GP", "R1", {"add": [{"word": "a"}]})
            st.put_entry("GP", "R2", {"add": [{"word": "b"}]})
        assert set(st.get_overlay("GP")) == {"R1", "R2"}


class TestReadOnly:
    def test_reader_sees_writer_and_cannot_write(self, tmp_path):
        db = tmp_path / "o.db"
        writer = SqliteOverlayStore(db)
        reader = open_overlay_store(db_path=db, read_only=True)
        assert reader.get_overlay("GP") == {}                  # file absent
        assert not db.exists()
        writer.put_entry("GP", "R1", {"add": [{"word": "a"}]})
        writer.set_accepted_labels({"c1"})
        assert reader.get_entry("GP", "R1") == {"add": [{"word": "a"}]}
        assert reader.accepted_labels() == {"c1"}
        assert reader.version("GP") == writer.version("GP") > 0
        with pytest.raises(OverlayReadOnlyError):
            reader.put_entry("GP", "R2", None)
        with pytest.raises(OverlayReadOnlyError):
            JsonOverlayStore(tmp_path, read_only=True).set_accepted_labels(set())

    def test_factory_precedence(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NORA_OVERLAY_DB", raising=False)
        assert open_overlay_store() is None
        assert isinstance(open_overlay_store(tmp_path), JsonOverlayStore)
        assert isinstance(open_overlay_store(tmp_path, db_path=tmp_path / "o.db"),
                          SqliteOverlayStore)
        monkeypatch.setenv("NORA_OVERLAY_DB", str(tmp_path / "env.db"))
        st = open_overlay_store(tmp_path, read_only=True)
        assert isinstance(st, SqliteOverlayStore) and st.read_only
        assert st.db_path.name == "env.db"


class TestImportCli:
    def _seed(self, root, frozen_store=True):
        st = EnrichOverlayStore(root)
        st.edit("GP", "R1", "remove", words=["secretword"], **_stamp(fingerprint="fp1"))
        st.edit("GP", "R2", "suppress", **_stamp(label="c2"))
        st.edit("XB", "R1", "add", words=["another"], **_stamp())
        st.edit("XB", "R1", "unadd", words=["another"], **_stamp())   # XB emptied
        st.set_label_merged("c2", True)
        st.set_label_disabled("c1", True)
        st.add_reason_category("new-cat")
        return st

    def test_round_trip(self, tmp_path, frozen, capsys):
        corr, db = tmp_path / "corr", tmp_path / "o.db"
        src = self._seed(corr)
        rc = overlay_import.main(["--corrections-root", str(corr), "--db", str(db)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "imported 2 MNO(s): 2 entries, 1 word records, 1 suppress_all" in out
        assert "secretword" not in out and "new-cat" not in out
        dst = EnrichOverlayStore("", backend=SqliteOverlayStore(db))
        _assert_parity((src, dst), mnos=("GP", "XB"))

    def test_refuses_non_empty_without_force(self, tmp_path, frozen, capsys):
        corr, db = tmp_path / "corr", tmp_path / "o.db"
        self._seed(corr)
        assert overlay_import.main(["--corrections-root", str(corr), "--db", str(db)]) == 0
        capsys.readouterr()
        assert overlay_import.main(["--corrections-root", str(corr), "--db", str(db)]) == 1
        assert "OVL-E002" in capsys.readouterr().err
        assert overlay_import.main(["--corrections-root", str(corr), "--db", str(db),
                                    "--force"]) == 0

    def test_missing_root(self, tmp_path, capsys):
        rc = overlay_import.main(["--corrections-root", str(tmp_path / "none"),
                                  "--db", str(tmp_path / "o.db")])
        assert rc == 1 and "OVL-E001" in capsys.readouterr().err

    def test_verify_mismatch_rolls_back(self, tmp_path, capsys):
        corr, db = tmp_path / "corr", tmp_path / "o.db"
        d = corr / "sira-enrich"
        d.mkdir(parents=True)
        # an empty direction list is not representable: verify must catch it
        (d / "GP.json").write_text(json.dumps(
            {"R1": {"remove": [], "add": [{"word": "a"}]}}))
        rc = overlay_import.main(["--corrections-root", str(corr), "--db", str(db)])
        assert rc == 1 and "OVL-E003" in capsys.readouterr().err
        assert SqliteOverlayStore(db).is_empty()

    def test_codes_registered(self):
        for code in ("OVL-E001", "OVL-E002", "OVL-E003", "OVL-E004"):
            assert code in CODES
