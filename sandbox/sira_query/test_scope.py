"""Tests for query-scope resolution (multi-mno-sira D-DRAFT-9/10)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sandbox.sira_query.scope import normalize_release, resolve_cells


def _intent(mnos=None, releases=None):
    return SimpleNamespace(mnos=mnos or [], releases=releases or [])


# ── normalize_release — the human-phrasing -> cell-label seam ──────

@pytest.mark.parametrize("raw,expected", [
    ("February 2026", "Feb2026"),
    ("feb 2026", "Feb2026"),
    ("Feb 2026", "Feb2026"),
    ("2026 february", "Feb2026"),
    ("October 2025", "Oct2025"),
    ("latest", "latest"),
    ("current", "latest"),
    ("LATEST", "latest"),
])
def test_normalize_release_ok(raw, expected):
    assert normalize_release(raw) == expected


@pytest.mark.parametrize("raw", ["", "sometime soon", "Q1 2026", "1999", "2026"])
def test_normalize_release_unparseable(raw):
    assert normalize_release(raw) is None


# ── resolve_cells — the FR-multi-6 cross-product ──────────────────

CELLS = {("VZW", "Feb2026"), ("VZW", "Oct2025"), ("TMO", "Jan2026")}


def test_mno_scoped_no_release_resolves_latest():
    # "What 5G bands does VZW support?" -> VZW's latest cell only
    resolved, unresolved = resolve_cells(_intent(mnos=["VZW"]), CELLS)
    assert resolved == {("VZW", "Feb2026")}
    assert unresolved == []


def test_cross_mno_no_release_latest_each():
    # "Compare VoWiFi of VZW and TMO" -> latest of each
    resolved, _ = resolve_cells(_intent(mnos=["VZW", "TMO"]), CELLS)
    assert resolved == {("VZW", "Feb2026"), ("TMO", "Jan2026")}


def test_release_diff_two_releases_same_mno():
    # "How did VZW change from Oct 2025 to Feb 2026?"
    resolved, unresolved = resolve_cells(
        _intent(mnos=["VZW"], releases=["Oct 2025", "Feb 2026"]), CELLS)
    assert resolved == {("VZW", "Oct2025"), ("VZW", "Feb2026")}
    assert unresolved == []


def test_no_mno_named_expands_to_all():
    # FR-10: missing MNO scope -> all MNOs, each at latest
    resolved, _ = resolve_cells(_intent(), CELLS)
    assert resolved == {("VZW", "Feb2026"), ("TMO", "Jan2026")}


def test_explicit_release_used_verbatim():
    resolved, _ = resolve_cells(
        _intent(mnos=["VZW"], releases=["Oct 2025"]), CELLS)
    assert resolved == {("VZW", "Oct2025")}


def test_latest_keyword_resolves_per_mno():
    resolved, _ = resolve_cells(
        _intent(mnos=["VZW"], releases=["latest"]), CELLS)
    assert resolved == {("VZW", "Feb2026")}


# ── unresolved surfacing (fail-visible at query) ──────────────────

def test_missing_release_is_surfaced_not_dropped():
    # VZW Mar2026 isn't ingested -> unresolved, NOT silently dropped
    resolved, unresolved = resolve_cells(
        _intent(mnos=["VZW"], releases=["Mar 2026"]), CELLS)
    assert resolved == set()
    assert ("VZW", "Mar2026") in unresolved


def test_named_mno_with_no_cells_is_surfaced():
    resolved, unresolved = resolve_cells(_intent(mnos=["ATT"]), CELLS)
    assert resolved == set()
    assert ("ATT", "*") in unresolved


def test_unparseable_release_is_surfaced():
    _, unresolved = resolve_cells(
        _intent(mnos=["VZW"], releases=["sometime in 2026ish"]), CELLS)
    assert any(u == ("*", "sometime in 2026ish") for u in unresolved)


def test_partial_resolution_proceeds_with_available():
    # one release present, one missing -> resolve the present, surface the gap
    resolved, unresolved = resolve_cells(
        _intent(mnos=["VZW"], releases=["Feb 2026", "Mar 2026"]), CELLS)
    assert resolved == {("VZW", "Feb2026")}
    assert ("VZW", "Mar2026") in unresolved


def test_empty_available_yields_empty_resolved():
    resolved, unresolved = resolve_cells(_intent(mnos=["VZW"]), set())
    assert resolved == set()
    assert ("VZW", "*") in unresolved
