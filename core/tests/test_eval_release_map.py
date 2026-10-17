"""Tests for the golden set's release map (synthetic labels only)."""

from __future__ import annotations

import json

import pytest

from core.src.eval.golden import GoldenEvalError
from core.src.eval.release_map import (
    HISTORY_FILE,
    MAP_FILE,
    ReleaseMap,
    load_release_map,
    save_release_map,
)


def _map(version=1, **mnos):
    return ReleaseMap(version=version, mnos={k.replace("_", "-"): v
                                             for k, v in mnos.items()})


def test_current_and_resolve():
    m = _map(mno_a=["Jan2026", "Apr2026", "Oct2026"])
    assert m.current("mno-a") == "Oct2026"
    assert m.current("mno-z") is None
    assert m.resolve("mno-a", "N") == "Oct2026"
    assert m.resolve("mno-a", "N-2") == "Jan2026"
    with pytest.raises(GoldenEvalError) as exc:
        m.resolve("mno-a", "N-3")
    assert exc.value.code == "GEV-E005"
    with pytest.raises(GoldenEvalError):
        m.resolve("mno-z", "N")


def test_validate():
    assert _map(mno_a=["Jan2026", "Apr2026"]).validate() == []
    assert _map(version=0, mno_a=["Jan2026"]).validate()
    assert _map(mno_a=[]).validate()
    assert _map(mno_a=["2026-01"]).validate()
    assert _map(mno_a=["Apr2026", "Jan2026"]).validate()       # not oldest first
    assert _map(mno_a=["Jan2026", "Jan2026"]).validate()       # duplicate


def test_missing_map_is_none(tmp_path):
    assert load_release_map(tmp_path) is None


def test_save_load_and_history(tmp_path):
    save_release_map(tmp_path, _map(1, mno_a=["Jan2026"]))
    save_release_map(tmp_path, _map(2, mno_a=["Jan2026", "Apr2026"]))
    assert load_release_map(tmp_path).version == 2
    assert load_release_map(tmp_path, version=1).current("mno-a") == "Jan2026"
    lines = (tmp_path / HISTORY_FILE).read_text().splitlines()
    assert [json.loads(l)["version"] for l in lines] == [1, 2]
    with pytest.raises(GoldenEvalError):
        load_release_map(tmp_path, version=7)


def test_save_refuses_non_advancing_or_invalid(tmp_path):
    save_release_map(tmp_path, _map(2, mno_a=["Jan2026"]))
    with pytest.raises(GoldenEvalError):
        save_release_map(tmp_path, _map(2, mno_a=["Apr2026"]))
    with pytest.raises(GoldenEvalError):
        save_release_map(tmp_path, _map(3, mno_a=["Apr2026", "Jan2026"]))
    assert load_release_map(tmp_path).version == 2


def test_unreadable_map_fails_loud(tmp_path):
    (tmp_path / MAP_FILE).write_text("{not json")
    with pytest.raises(GoldenEvalError) as exc:
        load_release_map(tmp_path)
    assert exc.value.code == "GEV-E001"
    (tmp_path / MAP_FILE).write_text(json.dumps(
        {"version": 1, "mnos": {"mno-a": ["Apr2026", "Jan2026"]}}))
    with pytest.raises(GoldenEvalError):
        load_release_map(tmp_path)


def test_digest_tracks_releases_only():
    a = _map(1, mno_a=["Jan2026"])
    b = _map(5, mno_a=["Jan2026"])
    assert a.digest() == b.digest()
    assert a.digest() != _map(1, mno_a=["Jan2026", "Apr2026"]).digest()
