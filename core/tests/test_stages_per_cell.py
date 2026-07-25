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
from core.src.pipeline.stages import run_parse, run_profile, run_resolve, run_taxonomy
from core.src.parser.structural_parser import Requirement, RequirementTree
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


class TestIncrementalSkip:
    """D-DRAFT-8 — profile_fingerprint + mtime skip in run_parse."""

    def _prep(self, env: Path) -> PipelineContext:
        _seed_env(env)
        ctx = PipelineContext.standalone(env_dir=env)
        run_profile(ctx)
        _seed_irs(env)
        return ctx

    def test_parse_skips_unchanged_on_rerun(self, tmp_path):
        ctx = self._prep(tmp_path)
        r1 = run_parse(ctx)
        assert r1.stats["docs"] == 2 and r1.stats["skipped"] == 0
        r2 = run_parse(ctx)
        assert r2.stats["docs"] == 0 and r2.stats["skipped"] == 2

    def test_force_reparses(self, tmp_path):
        ctx = self._prep(tmp_path)
        run_parse(ctx)
        ctx.force = True
        r = run_parse(ctx)
        assert r.stats["docs"] == 2 and r.stats["skipped"] == 0

    def test_profile_change_reparses_only_that_cell(self, tmp_path):
        ctx = self._prep(tmp_path)
        run_parse(ctx)
        # mutate MNO-A's materialized profile -> its fingerprint flips
        pj = tmp_path / "out" / "profile" / "MNO-A" / "Feb2026" / "profile.json"
        data = json.loads(pj.read_text(encoding="utf-8"))
        data["profile_name"] = "changed"
        pj.write_text(json.dumps(data), encoding="utf-8")
        r = run_parse(ctx)
        assert r.stats["docs"] == 1 and r.stats["skipped"] == 1  # A re-parsed, B skipped


class TestCellScopeInStages:
    def test_scope_processes_only_scoped_cell(self, tmp_path):
        env = _seed_env(tmp_path)  # MNO-A/Feb2026 + MNO-B/Mar2025
        ctx = PipelineContext.standalone(env_dir=env)
        ctx.scope_mnos = ["MNO-B"]
        res = run_profile(ctx)
        assert res.status == "OK", res
        assert res.stats["cells"] == 1
        assert (env / "out" / "profile" / "MNO-B" / "Mar2025" / "profile.json").is_file()
        assert not (env / "out" / "profile" / "MNO-A").exists()


class TestTaxonomyCache:
    """D-DRAFT-9 — global taxonomy corpus-fingerprint cache."""

    def _seed_tree(self, env: Path, mno: str, rel: str, plan: str) -> None:
        d = env / "out" / "parse" / mno / rel
        d.mkdir(parents=True, exist_ok=True)
        RequirementTree(
            mno=mno, release=rel, plan_id=plan,
            requirements=[Requirement(req_id=f"{plan}-1", title="T",
                                      text="Device shall support X.")],
        ).save_json(d / f"{plan}_tree.json")

    def test_cache_hit_on_unchanged_corpus(self, tmp_path):
        self._seed_tree(tmp_path, "MNO-A", "Feb2026", "PLANX")
        ctx = PipelineContext.standalone(env_dir=tmp_path, model_provider="mock")
        assert run_taxonomy(ctx).stats["source"] == "derived"
        assert run_taxonomy(ctx).stats["source"] == "cache"

    def test_cache_busted_on_new_tree(self, tmp_path):
        self._seed_tree(tmp_path, "MNO-A", "Feb2026", "PLANX")
        ctx = PipelineContext.standalone(env_dir=tmp_path, model_provider="mock")
        run_taxonomy(ctx)
        self._seed_tree(tmp_path, "MNO-B", "Mar2025", "PLANY")  # add a cell tree
        assert run_taxonomy(ctx).stats["source"] == "derived"

    def test_force_busts_cache(self, tmp_path):
        self._seed_tree(tmp_path, "MNO-A", "Feb2026", "PLANX")
        ctx = PipelineContext.standalone(env_dir=tmp_path, model_provider="mock")
        run_taxonomy(ctx)
        ctx.force = True
        assert run_taxonomy(ctx).stats["source"] == "derived"

    def test_overview_change_busts_cache(self, tmp_path, monkeypatch):
        """Corpus-overview files are prompt inputs — adding or editing one
        flips the fingerprint (strand sira-enrichment-pe, no --force needed)."""
        self._seed_tree(tmp_path, "MNO-A", "Feb2026", "PLANX")
        ov_dir = tmp_path / "overviews"
        ov_dir.mkdir()
        monkeypatch.setenv("NORA_TAXONOMY_OVERVIEW_DIR", str(ov_dir))
        ctx = PipelineContext.standalone(env_dir=tmp_path, model_provider="mock")
        assert run_taxonomy(ctx).stats["source"] == "derived"
        ov = ov_dir / "corpus_overview_MNO-A_v01.txt"
        ov.write_text("Corpus covers X.", encoding="utf-8")   # add
        assert run_taxonomy(ctx).stats["source"] == "derived"
        assert run_taxonomy(ctx).stats["source"] == "cache"    # stable
        ov.write_text("Corpus covers X and Y.", encoding="utf-8")  # edit
        assert run_taxonomy(ctx).stats["source"] == "derived"


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
