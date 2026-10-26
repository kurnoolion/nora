"""Overlay restamp after a canonical-form change (strand rel-history):
stale-version fingerprints are recomputed from the requirement's text in
its origin release; current ones and unresolvable ones are left alone."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.src.models.fingerprint import FINGERPRINT_VERSION, text_fp
from core.src.pipeline.error_codes import PipelineError
from core.src.stores.overlay import SqliteOverlayStore
from core.src.stores.overlay_restamp import main, restamp

MNO, REL = "mno-r", "Jan2031"
TITLE, TEXT = "title R1", "The device shall retry after 30 s."


def _env(tmp_path: Path) -> Path:
    env = tmp_path / "env"
    d = env / "out" / "parse" / MNO / REL
    d.mkdir(parents=True)
    tree = {"mno": MNO, "release": REL, "plan_id": "PLANA", "plan_name": "Plan A",
            "version": REL, "requirements": [
                {"req_id": "R1", "section_number": "1.1", "title": TITLE, "text": TEXT,
                 "parent_req_id": "", "is_requirement": True, "priority": "", "applicability": []}]}
    (d / "doc_tree.json").write_text(json.dumps(tree), encoding="utf-8")
    return env


def _rec(word: str, fp: str) -> dict:
    r = {"word": word, "label": "", "reason": {"category": "x", "note": ""}, "by": "u",
         "at": "2031-01-01T00:00:00Z", "origin": {"release": REL}}
    if fp:
        r["fingerprint"] = fp
    return r


@pytest.fixture()
def world(tmp_path):
    env = _env(tmp_path)
    db = tmp_path / "overlay.db"
    s = SqliteOverlayStore(db)
    with s.transaction():
        s.put_entry(MNO, "R1", {"remove": [_rec("alpha", "v2:" + "0" * 64), _rec("beta", "")],
                                "add": [_rec("gamma", text_fp(TITLE, TEXT))]})
        s.put_entry(MNO, "R-GONE", {"remove": [_rec("delta", "v2:" + "1" * 64)]})
    return env, db


def test_restamps_stale_leaves_current_and_unresolved(world):
    env, db = world
    rep = restamp(db, env_dir=str(env))
    c = rep["mnos"][MNO]
    assert c == {"entries": 2, "records": 4, "restamped": 1, "current": 1, "missing": 1,
                 "unresolved": 1}
    s = SqliteOverlayStore(db, read_only=True)
    e = s.get_entry(MNO, "R1")
    by_word = {r["word"]: r for r in e["remove"] + e["add"]}
    assert by_word["alpha"]["fingerprint"] == text_fp(TITLE, TEXT)
    assert by_word["alpha"]["fingerprint"].startswith(FINGERPRINT_VERSION + ":")
    assert "fingerprint" not in by_word["beta"]                 # missing stays missing
    assert by_word["gamma"]["fingerprint"] == text_fp(TITLE, TEXT)
    gone = s.get_entry(MNO, "R-GONE")["remove"][0]
    assert gone["fingerprint"] == "v2:" + "1" * 64                 # unresolved untouched


def test_dry_run_and_stamp_missing(world):
    env, db = world
    rep = restamp(db, env_dir=str(env), dry_run=True, stamp_missing=True)
    assert rep["dry_run"] and rep["mnos"][MNO]["restamped"] == 2
    s = SqliteOverlayStore(db, read_only=True)
    assert s.get_entry(MNO, "R1")["remove"][0]["fingerprint"].startswith("v2:")
    restamp(db, env_dir=str(env), stamp_missing=True)
    e = SqliteOverlayStore(db, read_only=True).get_entry(MNO, "R1")
    assert all(r["fingerprint"] == text_fp(TITLE, TEXT) for r in e["remove"] + e["add"])


def test_no_trees_source_is_a_coded_error(world, capsys):
    _, db = world
    with pytest.raises(PipelineError) as ei:
        restamp(db)
    assert ei.value.code == "OVL-E006"
    assert main(["--db", str(db)]) == 1
    assert "OVL-E006" in capsys.readouterr().err
