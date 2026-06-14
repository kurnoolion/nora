"""Tests for the shared (MNO, release) cell primitives (multi-mno-sira)."""

from __future__ import annotations

import pytest

from sandbox.sira_cells import (
    cell_dirname,
    enumerate_cells,
    is_valid_release,
    latest_release,
    order_key,
    parse_cell_dirname,
)


# ── is_valid_release / RELEASE_RE ─────────────────────────────────

@pytest.mark.parametrize("good", ["Feb2026", "Jan2025", "Dec2099", "Oct2025"])
def test_valid_release(good):
    assert is_valid_release(good)


@pytest.mark.parametrize("bad", [
    "OA-baseline", "February 2026", "Feb-2026", "feb2026",
    "FEB2026", "Feb26", "Q1-2026", "Feb2026x", "", None,
])
def test_invalid_release(bad):
    assert not is_valid_release(bad)


# ── cell_dirname / parse_cell_dirname round-trip ──────────────────

def test_cell_dirname():
    assert cell_dirname(("VZW", "Feb2026")) == "VZW__Feb2026"


@pytest.mark.parametrize("cell", [("VZW", "Feb2026"), ("TMO", "Jan2026")])
def test_dirname_parse_roundtrip(cell):
    assert parse_cell_dirname(cell_dirname(cell)) == cell


@pytest.mark.parametrize("bad", [
    "nora",              # no separator (legacy single dataset)
    "VZW_Feb2026",       # single underscore
    "VZW__OA-baseline",  # bad release
    "__Feb2026",         # empty mno
    "VZW__",             # empty release
])
def test_parse_rejects_non_cells(bad):
    assert parse_cell_dirname(bad) is None


def test_parse_mno_with_underscore_splits_on_first():
    # an MNO token containing '_' before '__' shouldn't break: split on FIRST __
    assert parse_cell_dirname("VZW_X__Feb2026") == ("VZW_X", "Feb2026")


# ── order_key / latest_release ────────────────────────────────────

def test_order_key():
    assert order_key("Feb2026") == (2026, 2)
    assert order_key("Dec2025") == (2025, 12)
    assert order_key("Jan2026") == (2026, 1)


def test_order_key_invalid():
    assert order_key("OA-baseline") is None
    assert order_key("") is None


def test_order_key_orders_correctly():
    # Jan2026 newer than Dec2025 (year dominates); Feb2026 newer than Jan2026
    assert order_key("Jan2026") > order_key("Dec2025")
    assert order_key("Feb2026") > order_key("Jan2026")


def test_latest_release_per_mno():
    cells = [("VZW", "Oct2025"), ("VZW", "Feb2026"), ("TMO", "Jan2026")]
    assert latest_release("VZW", cells) == "Feb2026"   # newest VZW
    assert latest_release("TMO", cells) == "Jan2026"
    assert latest_release("ATT", cells) is None         # no cells


def test_latest_release_crosses_year_boundary():
    cells = [("VZW", "Dec2025"), ("VZW", "Jan2026")]
    assert latest_release("VZW", cells) == "Jan2026"


# ── enumerate_cells — filesystem scan ─────────────────────────────

def _make_cell(db_root, dirname, with_metadata=True):
    raw = db_root / dirname / "raw"
    raw.mkdir(parents=True)
    if with_metadata:
        (raw / "metadata.json").write_text("{}")


def test_enumerate_cells(tmp_path):
    _make_cell(tmp_path, "VZW__Feb2026")
    _make_cell(tmp_path, "VZW__Oct2025")
    _make_cell(tmp_path, "TMO__Jan2026")
    assert enumerate_cells(tmp_path) == [
        ("TMO", "Jan2026"), ("VZW", "Feb2026"), ("VZW", "Oct2025"),
    ]


def test_enumerate_skips_non_cell_dirs(tmp_path):
    _make_cell(tmp_path, "VZW__Feb2026")
    (tmp_path / "nora").mkdir()                 # legacy single dataset
    (tmp_path / "VZW__OA-baseline").mkdir()     # bad release
    (tmp_path / "loose-file.txt").write_text("x")
    assert enumerate_cells(tmp_path) == [("VZW", "Feb2026")]


def test_enumerate_requires_metadata(tmp_path):
    _make_cell(tmp_path, "VZW__Feb2026", with_metadata=True)
    _make_cell(tmp_path, "TMO__Jan2026", with_metadata=False)  # no metadata.json
    assert enumerate_cells(tmp_path) == [("VZW", "Feb2026")]


def test_enumerate_missing_db_root(tmp_path):
    assert enumerate_cells(tmp_path / "does-not-exist") == []
