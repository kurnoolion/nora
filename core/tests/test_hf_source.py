"""Tests for HuggingFaceSource — fully mocked, no network."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.src.standards.hf_source import HuggingFaceSource


# ---------------------------------------------------------------------------
# urlopen mock helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._buf = io.BytesIO(body)
        self.status = status

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size) if size != -1 else self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _tree_json(filenames: list[str]) -> bytes:
    """Mimic the HF tree API response shape for a directory listing."""
    return json.dumps(
        [{"type": "file", "path": f"any/prefix/{fn}"} for fn in filenames]
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# _pick_latest_docx
# ---------------------------------------------------------------------------


class TestPickLatestDocx:
    """Static version-selection logic, no network."""

    def test_picks_highest_minor_within_release(self):
        files = [
            "21101-j00.docx", "21101-j10.docx", "21101-j20.docx",
            "21101-i70.docx",  # different release prefix — must be ignored
        ]
        chosen = HuggingFaceSource._pick_latest_docx(files, "21101", "j")
        assert chosen == "21101-j20.docx"

    def test_filters_out_other_specs(self):
        files = [
            "24301-j00.docx",
            "24008-j10.docx",  # different spec — must be ignored
            "24301-j10.docx",
        ]
        chosen = HuggingFaceSource._pick_latest_docx(files, "24301", "j")
        assert chosen == "24301-j10.docx"

    def test_filters_out_other_releases(self):
        files = ["24301-i70.docx", "24301-h50.docx"]
        chosen = HuggingFaceSource._pick_latest_docx(files, "24301", "j")
        assert chosen is None

    def test_no_matches_returns_none(self):
        chosen = HuggingFaceSource._pick_latest_docx([], "24301", "j")
        assert chosen is None

    def test_letter_minor_sorts_after_digit(self):
        # 3GPP code: 'a' = 10, beats '9' lexicographically too — verify
        # sort key works for the digit→letter transition.
        files = ["24301-j90.docx", "24301-ja0.docx"]
        chosen = HuggingFaceSource._pick_latest_docx(files, "24301", "j")
        assert chosen == "24301-ja0.docx"

    def test_skips_non_docx(self):
        files = ["24301-j00.zip", "24301-j00.md", "24301-j00.docx"]
        chosen = HuggingFaceSource._pick_latest_docx(files, "24301", "j")
        assert chosen == "24301-j00.docx"


# ---------------------------------------------------------------------------
# download() — full flow with mocked urlopen
# ---------------------------------------------------------------------------


class TestDownload:
    def test_happy_path(self, tmp_path: Path):
        listing = _tree_json(["21101-j00.docx", "21101-j10.docx"])
        body = b"FAKE-DOCX-BYTES"
        responses = [_FakeResponse(listing), _FakeResponse(body)]

        with patch("urllib.request.urlopen", side_effect=responses):
            src = HuggingFaceSource()
            out = src.download("21.101", 19, tmp_path)

        assert out == tmp_path / "21101-j10.docx"
        assert out.exists()
        assert out.read_bytes() == body

    def test_uses_existing_file_without_redownload(self, tmp_path: Path):
        # Prepopulate the cache for the version the listing would pick.
        cached = tmp_path / "21101-j10.docx"
        cached.write_bytes(b"CACHED")
        listing = _tree_json(["21101-j00.docx", "21101-j10.docx"])

        # Only the listing call should hit the network — no download.
        with patch("urllib.request.urlopen", return_value=_FakeResponse(listing)) as mock:
            src = HuggingFaceSource()
            out = src.download("21.101", 19, tmp_path)
            assert mock.call_count == 1

        assert out == cached
        assert out.read_bytes() == b"CACHED"

    def test_unknown_release_returns_none(self, tmp_path: Path):
        # Release 99 isn't in the prefix table → bail before any network call.
        with patch("urllib.request.urlopen") as mock:
            src = HuggingFaceSource()
            out = src.download("21.101", 99, tmp_path)
            mock.assert_not_called()
        assert out is None

    def test_no_matching_files_returns_none(self, tmp_path: Path):
        listing = _tree_json(["99999-j00.docx"])  # different spec only
        with patch("urllib.request.urlopen", return_value=_FakeResponse(listing)):
            src = HuggingFaceSource()
            out = src.download("21.101", 19, tmp_path)
        assert out is None

    def test_listing_network_error_returns_none(self, tmp_path: Path):
        import urllib.error
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            src = HuggingFaceSource()
            out = src.download("21.101", 19, tmp_path)
        assert out is None

    def test_download_network_error_cleans_up(self, tmp_path: Path):
        import urllib.error
        listing = _tree_json(["21101-j10.docx"])
        # First call (listing) succeeds; second (download) fails.
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                _FakeResponse(listing),
                urllib.error.URLError("network down"),
            ],
        ):
            src = HuggingFaceSource()
            out = src.download("21.101", 19, tmp_path)
        assert out is None
        # No partial file left behind.
        assert not (tmp_path / "21101-j10.docx").exists()

    def test_listing_cache_skips_repeat_api_call(self, tmp_path: Path):
        listing = _tree_json(["24301-j10.docx"])
        body = b"DOCX1"
        # First download: 1 listing + 1 file fetch.
        # Second download (same series/release): 0 listings + 1 file fetch.
        responses = [
            _FakeResponse(listing),  # initial listing
            _FakeResponse(body),     # download 1
            _FakeResponse(body),     # download 2 (listing was cached)
        ]
        with patch(
            "urllib.request.urlopen", side_effect=responses
        ) as mock:
            src = HuggingFaceSource()
            dest1 = tmp_path / "first"
            dest2 = tmp_path / "second"
            out1 = src.download("24.301", 19, dest1)
            out2 = src.download("24.301", 19, dest2)
            assert mock.call_count == 3  # not 4 — listing cached

        assert out1 == dest1 / "24301-j10.docx"
        assert out2 == dest2 / "24301-j10.docx"
