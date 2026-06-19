"""Tests for per-cell profile binding (D-DRAFT-7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.src.env.profile_bindings import (
    ProfileBinding,
    ProfileBindings,
    load_profile_bindings,
)


def _bindings(**kw) -> ProfileBindings:
    return ProfileBindings(
        bindings=[
            ProfileBinding("VZW-OA", "*", "p/oa.json"),
            ProfileBinding("MNO-B", "Mar2025", "p/b_mar.json"),
            ProfileBinding("MNO-B", "*", "p/b_default.json"),
        ],
        **kw,
    )


class TestResolvePrecedence:
    def test_exact_release_wins_over_mno_wildcard(self):
        assert _bindings().resolve("MNO-B", "Mar2025") == Path("p/b_mar.json")

    def test_mno_wildcard_when_no_exact(self):
        assert _bindings().resolve("MNO-B", "Jun2026") == Path("p/b_default.json")

    def test_plain_mno_wildcard(self):
        assert _bindings().resolve("VZW-OA", "Feb2026") == Path("p/oa.json")

    def test_mno_match_is_case_insensitive(self):
        assert _bindings().resolve("vzw-oa", "Feb2026") == Path("p/oa.json")

    def test_default_fallback(self):
        b = ProfileBindings([], default="p/fallback.json")
        assert b.resolve("NEW", "Feb2026") == Path("p/fallback.json")

    def test_override_wins_over_everything(self):
        b = _bindings(override="p/forced.json")
        assert b.resolve("MNO-B", "Mar2025") == Path("p/forced.json")
        assert b.resolve("ANY", "Whatever") == Path("p/forced.json")

    def test_fail_loud_pip_e003_on_miss(self):
        with pytest.raises(ValueError, match="PIP-E003"):
            _bindings().resolve("UNKNOWN", "Feb2026")


class TestCoverage:
    def test_covers(self):
        b = _bindings()
        assert b.covers("VZW-OA", "Feb2026") is True
        assert b.covers("MNO-B", "anything") is True
        assert b.covers("UNKNOWN", "Feb2026") is False

    def test_override_and_default_cover_everything(self):
        assert ProfileBindings([], override="p/x.json").covers("Z", "z") is True
        assert ProfileBindings([], default="p/d.json").covers("Z", "z") is True

    def test_uncovered_accepts_tuples_and_objects(self):
        b = _bindings()
        cells = [("VZW-OA", "Feb2026"), ("UNKNOWN", "Feb2026")]
        assert b.uncovered(cells) == [("UNKNOWN", "Feb2026")]

        class C:
            def __init__(self, m, r):
                self.mno, self.release = m, r

        assert b.uncovered([C("UNKNOWN", "Jan2026")]) == [("UNKNOWN", "Jan2026")]


class TestLoadProfileBindings:
    def test_load_manifest(self, tmp_path):
        (tmp_path / "profiles.json").write_text(
            json.dumps(
                {
                    "bindings": [
                        {"mno": "VZW-OA", "release": "*", "profile": "p/oa.json"},
                        {"mno": "MNO-B", "release": "Mar2025", "profile": "p/b.json"},
                    ],
                    "default": None,
                }
            ),
            encoding="utf-8",
        )
        b = load_profile_bindings(tmp_path)
        assert b.resolve("VZW-OA", "Feb2026") == Path("p/oa.json")
        assert b.resolve("MNO-B", "Mar2025") == Path("p/b.json")

    def test_override_bypasses_manifest(self, tmp_path):
        (tmp_path / "profiles.json").write_text('{"bindings": []}', encoding="utf-8")
        b = load_profile_bindings(tmp_path, override="p/forced.json")
        assert b.resolve("ANY", "X") == Path("p/forced.json")

    def test_missing_manifest_no_override_fails_loud_per_cell(self, tmp_path):
        b = load_profile_bindings(tmp_path)  # no profiles.json, no override
        assert b.covers("VZW-OA", "Feb2026") is False
        with pytest.raises(ValueError, match="PIP-E003"):
            b.resolve("VZW-OA", "Feb2026")
