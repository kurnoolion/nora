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


def _seed_plan_tree(env: Path, mno: str, rel: str, plan: str) -> None:
    d = env / "out" / "parse" / mno / rel
    d.mkdir(parents=True, exist_ok=True)
    RequirementTree(
        mno=mno, release=rel, plan_id=plan,
        requirements=[Requirement(req_id=f"{plan}-1", title="T",
                                  text="Device shall support X.")],
    ).save_json(d / f"{plan}_tree.json")


def _seed_multiplan_tree(env: Path, mno: str, rel: str, fname: str,
                         plans: list[str]) -> None:
    """One doc whose chapters are each a plan: empty tree-level plan_id,
    per-requirement plan_id set (D-DRAFT-1)."""
    d = env / "out" / "parse" / mno / rel
    d.mkdir(parents=True, exist_ok=True)
    RequirementTree(
        mno=mno, release=rel, plan_id="",
        requirements=[
            Requirement(req_id=f"{p}-1", plan_id=p, section_number=str(i + 2),
                        title=f"Chapter {p}", text="Device shall support X.")
            for i, p in enumerate(plans)
        ],
    ).save_json(d / f"{fname}_tree.json")


class _ScriptedProvider:
    """LLM stub: valid feature JSON, except plans scripted to error/garble."""

    def __init__(self, fail_plans=(), garbage_plans=()):
        self.fail_plans = set(fail_plans)
        self.garbage_plans = set(garbage_plans)
        self.prompts: list[str] = []

    def complete(self, prompt: str, system: str = "",
                 temperature: float = 0.0, max_tokens: int = 4096) -> str:
        self.prompts.append(prompt)
        for p in self.fail_plans:
            if f"Plan ID: {p}" in prompt:
                raise RuntimeError("LLM HTTP 500 (scripted)")
        for p in self.garbage_plans:
            if f"Plan ID: {p}" in prompt:
                return "server hiccup, not json"
        return json.dumps({
            "primary_features": [
                {"feature_id": "F", "name": "F", "description": "d",
                 "keywords": ["k"], "confidence": 0.9}
            ],
            "referenced_features": [], "key_concepts": [],
        })


class TestTaxonomyCache:
    """D-DRAFT-9 — global taxonomy corpus-fingerprint cache."""

    def _seed_tree(self, env: Path, mno: str, rel: str, plan: str) -> None:
        _seed_plan_tree(env, mno, rel, plan)

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


class TestTaxonomyResilience:
    """Newest-release selection + fail-soft / resume / retry in run_taxonomy."""

    def _ctx(self, env: Path, monkeypatch, provider) -> PipelineContext:
        ctx = PipelineContext.standalone(env_dir=env, model_provider="mock")
        monkeypatch.setattr(ctx, "create_llm_provider", lambda *a, **k: provider)
        return ctx

    def test_release_sort_key_chronological(self):
        """MMMYYYY names compare by (year, month), not alphabetically."""
        from core.src.pipeline.stages import _release_sort_key
        assert _release_sort_key("Mar2026") == "202603"
        assert _release_sort_key("Jul2026") == "202607"
        assert _release_sort_key("Dec2025") < _release_sort_key("Jan2026")
        assert _release_sort_key("not-a-release") == "not-a-release"

    def test_newest_release_wins(self, tmp_path, monkeypatch):
        """Same plan in two releases → only the chronologically newest
        release's copy is extracted; the older one is superseded. Jul2026
        vs Mar2026 pins the MMMYYYY parse — plain string comparison would
        pick Mar2026 ('J' < 'M')."""
        _seed_plan_tree(tmp_path, "MNO-A", "Mar2026", "PLANX")
        _seed_plan_tree(tmp_path, "MNO-A", "Jul2026", "PLANX")
        prov = _ScriptedProvider()
        res = run_taxonomy(self._ctx(tmp_path, monkeypatch, prov))
        assert res.status == "OK", res
        assert res.stats["docs"] == 1 and res.stats["superseded"] == 1
        assert len(prov.prompts) == 1
        assert "Release: Jul2026" in prov.prompts[0]

    def test_same_plan_different_mnos_both_kept(self, tmp_path, monkeypatch):
        """Dedup keys on (MNO, plan_id) — a shared plan_id across MNOs is
        two documents, not a supersession."""
        _seed_plan_tree(tmp_path, "MNO-A", "R2026Q1", "PLANX")
        _seed_plan_tree(tmp_path, "MNO-B", "R2026Q1", "PLANX")
        prov = _ScriptedProvider()
        res = run_taxonomy(self._ctx(tmp_path, monkeypatch, prov))
        assert res.stats["docs"] == 2 and res.stats["superseded"] == 0

    def test_fail_soft_resume_and_retry(self, tmp_path, monkeypatch):
        """One doc's LLM error → WARN, rest completes; re-run retries only
        the failed doc; clean re-run then hits the stage cache."""
        _seed_plan_tree(tmp_path, "MNO-A", "R2026Q1", "PLANA")
        _seed_plan_tree(tmp_path, "MNO-B", "R2026Q1", "PLANB")
        prov = _ScriptedProvider(fail_plans={"PLANB"})
        ctx = self._ctx(tmp_path, monkeypatch, prov)
        out = tmp_path / "out" / "taxonomy"

        r1 = run_taxonomy(ctx)
        assert r1.status == "WARN" and r1.stats["failed"] == 1
        assert r1.stats["docs"] == 1
        assert any("TAX-W004" in w for w in r1.warnings)
        assert (out / "taxonomy.json").is_file()          # degraded but usable
        assert (out / "PLANA_features.json").is_file()
        assert not (out / "PLANB_features.json").exists() # no empty success

        prov.fail_plans.clear()                           # endpoint recovered
        r2 = run_taxonomy(ctx)
        assert r2.status == "OK"
        assert r2.stats["docs"] == 1 and r2.stats["cached_docs"] == 1
        assert (out / "PLANB_features.json").is_file()

        r3 = run_taxonomy(ctx)
        assert r3.stats["source"] == "cache"

    def test_unparseable_response_is_failure(self, tmp_path, monkeypatch):
        """A response that never parses is recorded as failed — and with
        every doc failed the stage FAILs instead of consolidating nothing."""
        _seed_plan_tree(tmp_path, "MNO-A", "R2026Q1", "PLANA")
        prov = _ScriptedProvider(garbage_plans={"PLANA"})
        res = run_taxonomy(self._ctx(tmp_path, monkeypatch, prov))
        assert res.status == "FAIL"
        state = json.loads(
            (tmp_path / "out" / "taxonomy" / "extraction_state.json")
            .read_text(encoding="utf-8"))
        (entry,) = state["docs"].values()
        assert entry["status"] == "failed"

    def test_stale_features_files_cleaned(self, tmp_path, monkeypatch):
        """Leftover per-plan files with no matching selected plan (e.g. empty
        files from pre-fail-soft crashed runs) are removed on re-derive."""
        out = tmp_path / "out" / "taxonomy"
        out.mkdir(parents=True)
        (out / "GHOST_features.json").write_text("{}", encoding="utf-8")
        _seed_plan_tree(tmp_path, "MNO-A", "R2026Q1", "PLANA")
        res = run_taxonomy(self._ctx(tmp_path, monkeypatch, _ScriptedProvider()))
        assert res.status == "OK"
        assert not (out / "GHOST_features.json").exists()
        assert (out / "PLANA_features.json").is_file()


class TestTaxonomyMultiPlan:
    """Chapter-per-plan docs: per-plan split, output naming, cross-release
    unit supersession, and per-plan retry."""

    def _ctx(self, env: Path, monkeypatch, provider) -> PipelineContext:
        ctx = PipelineContext.standalone(env_dir=env, model_provider="mock")
        monkeypatch.setattr(ctx, "create_llm_provider", lambda *a, **k: provider)
        return ctx

    def test_multiplan_doc_splits_per_plan(self, tmp_path, monkeypatch):
        """One doc, two chapter-plans → two focused LLM calls, two per-plan
        features files, and no empty-prefix '_features.json'."""
        _seed_multiplan_tree(tmp_path, "MNO-B", "Jul2026", "onedoc",
                             ["PLANA", "PLANB"])
        prov = _ScriptedProvider()
        res = run_taxonomy(self._ctx(tmp_path, monkeypatch, prov))
        assert res.status == "OK", res
        assert res.stats["docs"] == 2
        out = tmp_path / "out" / "taxonomy"
        assert (out / "PLANA_features.json").is_file()
        assert (out / "PLANB_features.json").is_file()
        assert not (out / "_features.json").exists()
        assert len(prov.prompts) == 2
        assert any("Plan ID: PLANA" in p and "Plan ID: PLANB" not in p
                   for p in prov.prompts)

    def test_multiplan_cross_release_newest_wins(self, tmp_path, monkeypatch):
        """Different file names across releases mean file-level selection
        can't dedupe (empty tree plan_id) — unit-level supersession must
        pick each plan's newest-release copy."""
        _seed_multiplan_tree(tmp_path, "MNO-B", "Mar2026", "doc_mar",
                             ["PLANA", "PLANB"])
        _seed_multiplan_tree(tmp_path, "MNO-B", "Jul2026", "doc_jul",
                             ["PLANA", "PLANB"])
        prov = _ScriptedProvider()
        res = run_taxonomy(self._ctx(tmp_path, monkeypatch, prov))
        assert res.status == "OK", res
        assert res.stats["docs"] == 2 and res.stats["superseded"] == 2
        assert len(prov.prompts) == 2
        assert all("Release: Jul2026" in p for p in prov.prompts)

    def test_multiplan_per_plan_retry(self, tmp_path, monkeypatch):
        """One chapter-plan fails → only that plan retries on re-run; the
        sibling plan from the SAME tree file is reused."""
        _seed_multiplan_tree(tmp_path, "MNO-B", "Jul2026", "onedoc",
                             ["PLANA", "PLANB"])
        prov = _ScriptedProvider(fail_plans={"PLANB"})
        ctx = self._ctx(tmp_path, monkeypatch, prov)
        r1 = run_taxonomy(ctx)
        assert r1.status == "WARN"
        assert r1.stats["docs"] == 1 and r1.stats["failed"] == 1
        prov.fail_plans.clear()
        r2 = run_taxonomy(ctx)
        assert r2.status == "OK"
        assert r2.stats["docs"] == 1 and r2.stats["cached_docs"] == 1
        assert run_taxonomy(ctx).stats["source"] == "cache"


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
