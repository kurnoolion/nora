"""Per-cell stage routing tests (D-DRAFT-6/7/10).

Verifies profile/parse/resolve write to out/<stage>/<mno>/<rel>/ and that
profile binding + coverage fail-loud work end-to-end on tiny fixtures
(minimal IRs + a minimal profile — content correctness is covered elsewhere;
this pins the cell *routing*).
"""

from __future__ import annotations

import json
from pathlib import Path

from core.src.models.document import DocumentIR
from core.src.pipeline.runner import PipelineContext
from core.src.pipeline.stages import run_parse, run_profile, run_resolve
from core.src.profiler.profile_schema import DocumentProfile

CELLS = [("MNO-A", "Feb2026"), ("MNO-B", "Mar2025")]


def _seed_env(env: Path) -> Path:
    # input/<mno>/<MMMYYYY>/doc.pdf — enumerate_input_cells needs a file present
    for mno, rel in CELLS:
        d = env / "input" / mno / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "doc.pdf").write_text("x", encoding="utf-8")
    # a minimal profile + a binding manifest pointing every cell at it
    prof = env / "prof.json"
    DocumentProfile(profile_name="test").save_json(prof)
    (env / "profiles.json").write_text(
        json.dumps(
            {
                "bindings": [
                    {"mno": m, "release": "*", "profile": str(prof)} for m, _ in CELLS
                ],
                "default": None,
            }
        ),
        encoding="utf-8",
    )
    return env


def _seed_irs(env: Path) -> None:
    for mno, rel in CELLS:
        d = env / "out" / "extract" / mno / rel
        d.mkdir(parents=True, exist_ok=True)
        DocumentIR(
            source_file=f"{mno}_doc.pdf", source_format="pdf",
            mno=mno, release=rel, doc_type="requirement",
        ).save_json(d / f"{mno}_doc_ir.json")


class TestPerCellPipeline:
    def test_profile_materializes_per_cell(self, tmp_path):
        env = _seed_env(tmp_path)
        ctx = PipelineContext.standalone(env_dir=env)
        res = run_profile(ctx)
        assert res.status == "OK", res
        assert res.stats["cells"] == 2
        for mno, rel in CELLS:
            assert (env / "out" / "profile" / mno / rel / "profile.json").is_file()

    def test_parse_writes_per_cell_trees(self, tmp_path):
        env = _seed_env(tmp_path)
        ctx = PipelineContext.standalone(env_dir=env)
        run_profile(ctx)
        _seed_irs(env)
        res = run_parse(ctx)
        assert res.status in ("OK", "WARN"), res
        for mno, rel in CELLS:
            assert (env / "out" / "parse" / mno / rel / f"{mno}_doc_tree.json").is_file()

    def test_resolve_runs_per_cell(self, tmp_path):
        env = _seed_env(tmp_path)
        ctx = PipelineContext.standalone(env_dir=env)
        run_profile(ctx)
        _seed_irs(env)
        run_parse(ctx)
        res = run_resolve(ctx)
        assert res.status != "FAIL", res
        # per-cell resolve directories created (D-DRAFT-10)
        for mno, rel in CELLS:
            assert (env / "out" / "resolve" / mno / rel).is_dir()


class TestProfileCoverageFailLoud:
    def test_uncovered_cell_fails_pip_e003(self, tmp_path):
        env = tmp_path
        # one cell, but profiles.json binds a DIFFERENT mno -> uncovered
        (env / "input" / "MNO-A" / "Feb2026").mkdir(parents=True)
        (env / "input" / "MNO-A" / "Feb2026" / "doc.pdf").write_text("x")
        prof = env / "prof.json"
        DocumentProfile(profile_name="t").save_json(prof)
        (env / "profiles.json").write_text(
            json.dumps({"bindings": [{"mno": "OTHER", "release": "*", "profile": str(prof)}]}),
            encoding="utf-8",
        )
        ctx = PipelineContext.standalone(env_dir=env)
        res = run_profile(ctx)
        assert res.status == "FAIL"
        assert res.error_code == "PIP-E003"
        assert "MNO-A/Feb2026" in res.error_message

    def test_profile_override_covers_all_cells(self, tmp_path):
        env = tmp_path
        (env / "input" / "MNO-A" / "Feb2026").mkdir(parents=True)
        (env / "input" / "MNO-A" / "Feb2026" / "doc.pdf").write_text("x")
        prof = env / "prof.json"
        DocumentProfile(profile_name="t").save_json(prof)
        # --profile override (no profiles.json) covers every cell
        ctx = PipelineContext.standalone(env_dir=env, profile_path=prof)
        res = run_profile(ctx)
        assert res.status == "OK", res
        assert (env / "out" / "profile" / "MNO-A" / "Feb2026" / "profile.json").is_file()
