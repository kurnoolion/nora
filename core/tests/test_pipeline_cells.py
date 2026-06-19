"""Tests for the pipeline (MNO, release) cell substrate (D-DRAFT-6).

Covers the per-cell/global stage partition, input-cell enumeration, and
cell-aware `PipelineContext.stage_output` (non-breaking — flat when no cell).
"""

from __future__ import annotations

from pathlib import Path

from core.src.env.config import STAGE_NAMES
from core.src.pipeline.cells import (
    GLOBAL_STAGES,
    PER_CELL_STAGES,
    Cell,
    enumerate_input_cells,
    is_per_cell_stage,
)
from core.src.pipeline.runner import PipelineContext


class TestStagePartition:
    def test_partition_is_exhaustive_and_disjoint(self):
        assert PER_CELL_STAGES | GLOBAL_STAGES == set(STAGE_NAMES)
        assert PER_CELL_STAGES & GLOBAL_STAGES == set()

    def test_classification(self):
        for s in ("extract", "profile", "parse", "resolve", "vectorstore"):
            assert is_per_cell_stage(s) is True
        for s in ("taxonomy", "standards", "graph", "eval"):
            assert is_per_cell_stage(s) is False


class TestCell:
    def test_relpath(self):
        assert Cell("VZW-OA", "Feb2026").relpath == Path("VZW-OA") / "Feb2026"


def _make_input(root: Path, layout: dict[str, list[str]]) -> Path:
    """Build input/<mno>/<rel>/doc.pdf for each (mno -> [releases])."""
    inp = root / "input"
    for mno, rels in layout.items():
        for rel in rels:
            d = inp / mno / rel
            d.mkdir(parents=True, exist_ok=True)
            (d / "doc.pdf").write_text("x", encoding="utf-8")
    return inp


class TestEnumerateInputCells:
    def test_finds_valid_cells_uppercased(self, tmp_path):
        inp = _make_input(tmp_path, {"vzw-oa": ["Feb2026"], "mno-b": ["Mar2025"]})
        cells = enumerate_input_cells(inp)
        assert cells == [Cell("MNO-B", "Mar2025"), Cell("VZW-OA", "Feb2026")]

    def test_skips_non_mmmyyyy_release_dirs(self, tmp_path):
        inp = _make_input(tmp_path, {"vzw": ["OA-baseline", "Feb2026"]})
        cells = enumerate_input_cells(inp)
        assert cells == [Cell("VZW", "Feb2026")]  # OA-baseline skipped

    def test_skips_empty_cell_dirs(self, tmp_path):
        inp = _make_input(tmp_path, {"vzw": ["Feb2026"]})
        (inp / "vzw" / "Jun2026").mkdir(parents=True)  # empty -> skipped
        assert enumerate_input_cells(inp) == [Cell("VZW", "Feb2026")]

    def test_releases_sorted_chronologically_per_mno(self, tmp_path):
        inp = _make_input(tmp_path, {"vzw": ["Jun2026", "Feb2026", "Dec2025"]})
        cells = enumerate_input_cells(inp)
        assert [c.release for c in cells] == ["Dec2025", "Feb2026", "Jun2026"]

    def test_missing_dir_returns_empty(self, tmp_path):
        assert enumerate_input_cells(tmp_path / "nope") == []


class TestStageOutputCellAware:
    def _ctx(self, tmp_path) -> PipelineContext:
        return PipelineContext.standalone(env_dir=tmp_path)

    def test_per_cell_stage_with_cell_nests(self, tmp_path):
        ctx = self._ctx(tmp_path)
        out = ctx.stage_output("parse", Cell("VZW-OA", "Feb2026"))
        assert out == ctx.stage_dirs["parse"] / "VZW-OA" / "Feb2026"

    def test_global_stage_ignores_cell(self, tmp_path):
        ctx = self._ctx(tmp_path)
        out = ctx.stage_output("graph", Cell("VZW-OA", "Feb2026"))
        assert out == ctx.stage_dirs["graph"]  # flat, cell ignored

    def test_no_cell_is_flat_backcompat(self, tmp_path):
        ctx = self._ctx(tmp_path)
        assert ctx.stage_output("parse") == ctx.stage_dirs["parse"]
        assert ctx.stage_output("graph") == ctx.stage_dirs["graph"]


class TestCellScope:
    """D-DRAFT-8 — --mno/--release scope filtering of input_cells()."""

    def test_no_scope_is_all_cells(self, tmp_path):
        _make_input(tmp_path, {"vzw-oa": ["Feb2026"], "mno-b": ["Mar2025"]})
        ctx = PipelineContext.standalone(env_dir=tmp_path)
        assert len(ctx.input_cells()) == 2

    def test_mno_scope(self, tmp_path):
        _make_input(tmp_path, {"vzw-oa": ["Feb2026"], "mno-b": ["Mar2025"]})
        ctx = PipelineContext.standalone(env_dir=tmp_path)
        ctx.scope_mnos = ["MNO-B"]
        assert [c.mno for c in ctx.input_cells()] == ["MNO-B"]

    def test_mno_scope_case_insensitive(self, tmp_path):
        _make_input(tmp_path, {"mno-b": ["Mar2025"]})
        ctx = PipelineContext.standalone(env_dir=tmp_path)
        ctx.scope_mnos = ["mno-b"]
        assert len(ctx.input_cells()) == 1

    def test_release_scope(self, tmp_path):
        _make_input(tmp_path, {"vzw": ["Feb2026", "Jun2026"]})
        ctx = PipelineContext.standalone(env_dir=tmp_path)
        ctx.scope_releases = ["Jun2026"]
        assert [c.release for c in ctx.input_cells()] == ["Jun2026"]

    def test_cell_in_scope_method(self, tmp_path):
        ctx = PipelineContext.standalone(env_dir=tmp_path)
        ctx.scope_mnos = ["MNO-B"]
        assert ctx.cell_in_scope("MNO-B", "Mar2025") is True
        assert ctx.cell_in_scope("VZW-OA", "Feb2026") is False
