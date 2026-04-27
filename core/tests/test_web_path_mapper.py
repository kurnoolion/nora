"""Tests for the web path mapper module."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.src.web.config import PathMapping
from core.src.web.path_mapper import PathMapper

MAPPINGS = [
    PathMapping(
        windows=r"\\SERVER\OADocs",
        linux="/mnt/oa_docs",
        label="OA Documents",
    ),
    PathMapping(
        windows=r"\\SERVER\Specs",
        linux="/mnt/specs",
        label="Specifications",
    ),
]


@pytest.fixture
def mapper() -> PathMapper:
    return PathMapper(MAPPINGS)


# -- to_linux -----------------------------------------------------------------

class TestToLinux:
    def test_root_path(self, mapper: PathMapper) -> None:
        result = mapper.to_linux(r"\\SERVER\OADocs")
        assert result == Path("/mnt/oa_docs")

    def test_subdirectory(self, mapper: PathMapper) -> None:
        result = mapper.to_linux(r"\\SERVER\OADocs\vzw\doc.pdf")
        assert result == Path("/mnt/oa_docs/vzw/doc.pdf")

    def test_no_match_returns_none(self, mapper: PathMapper) -> None:
        assert mapper.to_linux(r"\\OTHER\Share\file.txt") is None

    def test_case_insensitive(self, mapper: PathMapper) -> None:
        result = mapper.to_linux(r"\\server\oadocs\VZW\Doc.pdf")
        assert result == Path("/mnt/oa_docs/VZW/Doc.pdf")

    def test_forward_slashes_in_windows_path(self, mapper: PathMapper) -> None:
        result = mapper.to_linux("\\\\SERVER\\OADocs/vzw/doc.pdf")
        assert result == Path("/mnt/oa_docs/vzw/doc.pdf")


# -- to_windows ----------------------------------------------------------------

class TestToWindows:
    def test_root_path(self, mapper: PathMapper) -> None:
        result = mapper.to_windows("/mnt/oa_docs")
        assert result == r"\\SERVER\OADocs"

    def test_subdirectory(self, mapper: PathMapper) -> None:
        result = mapper.to_windows("/mnt/oa_docs/vzw/doc.pdf")
        assert result == r"\\SERVER\OADocs\vzw\doc.pdf"

    def test_path_object_input(self, mapper: PathMapper) -> None:
        result = mapper.to_windows(Path("/mnt/specs/att/req.xlsx"))
        assert result == r"\\SERVER\Specs\att\req.xlsx"

    def test_no_match_returns_none(self, mapper: PathMapper) -> None:
        assert mapper.to_windows("/home/user/file.txt") is None


# -- list_roots ----------------------------------------------------------------

class TestListRoots:
    def test_returns_all_mappings(self, mapper: PathMapper) -> None:
        roots = mapper.list_roots()
        assert len(roots) == 2
        assert roots[0] == {
            "windows": r"\\SERVER\OADocs",
            "linux": "/mnt/oa_docs",
            "label": "OA Documents",
        }
        assert roots[1]["label"] == "Specifications"


# -- resolve -------------------------------------------------------------------

class TestResolve:
    def test_windows_path_detected(self, mapper: PathMapper, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        m = PathMapper([
            PathMapping(windows=r"\\SRV\Share", linux=str(tmp_path), label="tmp"),
        ])
        sub = tmp_path / "a"
        sub.mkdir()
        result = m.resolve(r"\\SRV\Share\a")
        assert result == sub.resolve()

    def test_linux_path_passed_through(self, tmp_path: Path) -> None:
        m = PathMapper([
            PathMapping(windows=r"\\SRV\Share", linux=str(tmp_path), label="tmp"),
        ])
        sub = tmp_path / "b"
        sub.mkdir()
        result = m.resolve(str(sub))
        assert result == sub.resolve()

    def test_unmapped_windows_raises(self, mapper: PathMapper) -> None:
        with pytest.raises(ValueError, match="does not match"):
            mapper.resolve(r"\\UNKNOWN\Share\file")

    def test_outside_roots_raises(self, mapper: PathMapper) -> None:
        with pytest.raises(ValueError, match="outside all configured roots"):
            mapper.resolve("/tmp/evil")


# -- is_within_roots / traversal protection ------------------------------------

class TestIsWithinRoots:
    def test_within_root(self, mapper: PathMapper) -> None:
        assert mapper.is_within_roots(Path("/mnt/oa_docs/vzw/doc.pdf"))

    def test_exact_root(self, mapper: PathMapper) -> None:
        assert mapper.is_within_roots(Path("/mnt/oa_docs"))

    def test_outside_root(self, mapper: PathMapper) -> None:
        assert not mapper.is_within_roots(Path("/etc/passwd"))

    def test_traversal_blocked(self, mapper: PathMapper) -> None:
        crafted = Path("/mnt/oa_docs/../../../etc/passwd")
        assert not mapper.is_within_roots(crafted)

    def test_traversal_within_root_ok(self, mapper: PathMapper) -> None:
        benign = Path("/mnt/oa_docs/subdir/../other/file.txt")
        assert mapper.is_within_roots(benign)
