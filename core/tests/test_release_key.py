"""Tests for the MMMYYYY release-label convention (D-DRAFT-6).

`release_key` is the fail-loud ingest validator; `release_order_key` /
`is_valid_release` are the soft checks. `infer_metadata_from_path` enforces
the convention at the path -> metadata boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.src.extraction.registry import infer_metadata_from_path
from core.src.extraction.release_key import (
    is_valid_release,
    release_key,
    release_order_key,
)


class TestIsValidRelease:
    @pytest.mark.parametrize("rel", ["Feb2026", "Jan2025", "Dec1999", "Jun2026"])
    def test_valid(self, rel):
        assert is_valid_release(rel) is True

    @pytest.mark.parametrize(
        "rel",
        [
            "feb2026",        # lower-case month
            "FEB2026",        # upper-case month
            "February2026",   # full month name
            "Feb26",          # 2-digit year
            "Feb20260",       # 5-digit year
            "2026Feb",        # reversed
            "Feb-2026",       # separator
            "OA-baseline",    # legacy free-form
            "2026_feb",       # legacy free-form
            "Foo2026",        # not a month
            "",               # empty
        ],
    )
    def test_invalid(self, rel):
        assert is_valid_release(rel) is False


class TestReleaseOrderKey:
    def test_orders_by_year_then_month(self):
        assert release_order_key("Feb2026") == (2026, 2)
        assert release_order_key("Dec2025") == (2025, 12)
        assert release_order_key("Jan2026") == (2026, 1)

    def test_sort_is_chronological(self):
        labels = ["Mar2025", "Jan2026", "Feb2026", "Dec2025"]
        assert sorted(labels, key=release_order_key) == [
            "Mar2025", "Dec2025", "Jan2026", "Feb2026",
        ]

    def test_none_for_invalid(self):
        assert release_order_key("OA-baseline") is None
        assert release_order_key("") is None


class TestReleaseKeyRaises:
    def test_returns_label_and_order_key(self):
        assert release_key("Feb2026") == ("Feb2026", (2026, 2))

    def test_raises_ext_e004_on_invalid(self):
        with pytest.raises(ValueError, match="EXT-E004"):
            release_key("OA-baseline")

    def test_raises_on_empty(self):
        with pytest.raises(ValueError, match="EXT-E004"):
            release_key("")


class TestInferMetadataEnforcesConvention:
    def test_valid_mmmyyyy_path_passes(self):
        p = Path("/data/env/input/VZW-OA/Feb2026/LTEDATARETRY.pdf")
        md = infer_metadata_from_path(p)
        assert md["mno"] == "VZW-OA"
        assert md["release"] == "Feb2026"
        assert md["doc_type"] == "requirement"

    def test_non_mmmyyyy_release_fails_loud(self):
        p = Path("/data/env/input/VZW/OA-baseline/LTEDATARETRY.pdf")
        with pytest.raises(ValueError, match="EXT-E004"):
            infer_metadata_from_path(p)

    def test_mno_is_uppercased(self):
        p = Path("/data/env/input/vzw-oa/Feb2026/x.pdf")
        assert infer_metadata_from_path(p)["mno"] == "VZW-OA"
