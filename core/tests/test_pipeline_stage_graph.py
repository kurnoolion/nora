"""Stage graph, fingerprints and the build manifest (strand stage-graph,
step 1: D-DRAFT-1 tiers, D-DRAFT-2 uniform fingerprints).

Fixtures are hand-made in-memory cells (no document content).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.src.env.config import STAGE_NAMES
from core.src.pipeline import fingerprints as fpm
from core.src.pipeline import stage_graph as sg
from core.src.pipeline.cells import PER_CELL_STAGES, Cell
from core.src.pipeline.error_codes import CODES, PipelineError
from core.src.pipeline.manifest import MANIFEST_NAME, BuildManifest
from core.src.pipeline.runner import PipelineContext

REPO = Path(__file__).resolve().parents[2]
PIN_FILE = Path(__file__).parent / "fixtures" / "stage_code_hashes.json"


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------

class TestStageGraph:
    def test_well_formed(self):
        assert sg.validate_graph() == []

    def test_order_is_the_stage_registry(self):
        assert list(sg.STAGE_ORDER) == list(STAGE_NAMES)
        assert set(sg.STAGE_GRAPH) == set(STAGE_NAMES)

    def test_tier_rule(self):
        """Corpus = no model and corpus inputs only; graph is variant because
        it reads taxonomy, standards is corpus because it reads resolve."""
        assert sg.CORPUS_STAGES == {"extract", "profile", "parse", "resolve", "standards"}
        assert sg.VARIANT_STAGES == {"taxonomy", "graph", "vectorstore", "eval"}
        assert sg.CORPUS_PREFIX == ("extract", "profile", "parse", "resolve", "standards")
        assert sg.tier_of("graph") == sg.VARIANT and not sg.spec_of("graph").uses_model

    def test_every_variant_stage_has_a_model_or_a_variant_input(self):
        for name in sg.VARIANT_STAGES:
            spec = sg.spec_of(name)
            assert spec.uses_model or any(i in sg.VARIANT_STAGES for i in spec.inputs), name

    def test_per_cell_flag_follows_cells_partition(self):
        for spec in sg.SPECS:
            assert spec.per_cell == (spec.name in PER_CELL_STAGES)

    def test_a_model_call_moves_a_stage_to_the_variant_tier(self, monkeypatch):
        """The rule, not a list: give resolve a model and it and graph flip."""
        spec = sg.spec_of("resolve")
        monkeypatch.setitem(sg.STAGE_GRAPH, "resolve", sg.StageSpec(
            spec.name, spec.inputs, spec.externals, spec.params, uses_model=True))
        assert sg.tier_of("resolve") == sg.VARIANT
        assert sg.tier_of("standards") == sg.VARIANT
        assert sg.tier_of("parse") == sg.CORPUS


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

class TestStageFingerprint:
    BASE = dict(stage="parse", code_version="1", params={},
                input_fps={"extract": "aaaa", "profile": "bbbb"}, external_fps={"annotations": "absent"})

    def test_deterministic_and_short(self):
        a = fpm.stage_fingerprint(**self.BASE)
        assert a == fpm.stage_fingerprint(**self.BASE)
        assert len(a) == fpm.FP_LEN and int(a, 16) >= 0

    @pytest.mark.parametrize("field,value", [
        ("stage", "resolve"),
        ("code_version", "2"),
        ("params", {"x": "1"}),
        ("input_fps", {"extract": "aaab", "profile": "bbbb"}),
        ("external_fps", {"annotations": "cccc"}),
    ])
    def test_each_part_changes_it(self, field, value):
        changed = dict(self.BASE, **{field: value})
        assert fpm.stage_fingerprint(**changed) != fpm.stage_fingerprint(**self.BASE)

    def test_param_order_does_not_matter(self):
        a = fpm.stage_fingerprint("t", "1", {"a": "1", "b": "2"}, {}, {})
        b = fpm.stage_fingerprint("t", "1", {"b": "2", "a": "1"}, {}, {})
        assert a == b

    def test_hash_files_sees_adds_removes_edits(self, tmp_path):
        (tmp_path / "a.txt").write_text("1", encoding="utf-8")
        h1 = fpm.hash_files(tmp_path.glob("*"), rel_to=tmp_path)
        (tmp_path / "b.txt").write_text("2", encoding="utf-8")
        h2 = fpm.hash_files(tmp_path.glob("*"), rel_to=tmp_path)
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        h3 = fpm.hash_files(tmp_path.glob("*"), rel_to=tmp_path)
        (tmp_path / "b.txt").unlink()
        h4 = fpm.hash_files(tmp_path.glob("*"), rel_to=tmp_path)
        assert len({h1, h2, h3, h4}) == 4

    def test_code_version_by_tier(self, monkeypatch):
        for stage in sg.CORPUS_STAGES:
            assert fpm.code_version(stage) == "1"
        monkeypatch.delenv(fpm.VARIANT_CODE_VERSION_ENV, raising=False)
        assert fpm.code_version("taxonomy") == fpm.DEFAULT_VARIANT_CODE_VERSION
        monkeypatch.setenv(fpm.VARIANT_CODE_VERSION_ENV, "img-42")
        assert fpm.code_version("taxonomy") == "img-42"
        assert fpm.code_version("parse") == "1"   # a corpus stage ignores the image version

    def test_cell_key(self):
        assert fpm.cell_key("parse", Cell("MNO-A", "Feb2026")) == "MNO-A/Feb2026"
        assert fpm.cell_key("graph", Cell("MNO-A", "Feb2026")) == sg.GLOBAL_KEY
        assert fpm.cell_key("parse", None) == sg.GLOBAL_KEY


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------

class TestBuildManifest:
    def test_round_trip_and_atomic_file(self, tmp_path):
        m = BuildManifest(tmp_path / MANIFEST_NAME)
        assert m.get("parse", "A/R") is None
        m.record("parse", "A/R", "f1", code_version="1", inputs={"extract": "e1"})
        m.record("parse", "B/R", "f2")
        m.record("graph", sg.GLOBAL_KEY, "g1")
        again = BuildManifest(tmp_path / MANIFEST_NAME)
        assert again.get("parse", "A/R") == "f1"
        assert again.fingerprints("parse") == {"A/R": "f1", "B/R": "f2"}
        assert again.entry("parse", "A/R")["inputs"] == {"extract": "e1"}
        assert "at" in again.entry("parse", "A/R")
        assert again.stages() == ["graph", "parse"]
        assert not (tmp_path / (MANIFEST_NAME + ".tmp")).exists()

    def test_forget(self, tmp_path):
        m = BuildManifest(tmp_path / MANIFEST_NAME)
        m.record("parse", "A/R", "f1")
        m.forget("parse", "A/R")
        m.forget("parse", "never")   # no-op
        assert BuildManifest(tmp_path / MANIFEST_NAME).fingerprints("parse") == {}

    def test_corrupt_file_fails_loud(self, tmp_path):
        (tmp_path / MANIFEST_NAME).write_text("{not json", encoding="utf-8")
        with pytest.raises(PipelineError) as ei:
            BuildManifest(tmp_path / MANIFEST_NAME)
        assert ei.value.code == "PIP-E004" and "PIP-E004" in CODES


# ---------------------------------------------------------------------------
# The fingerprinter over a build
# ---------------------------------------------------------------------------

A = Cell("MNO-A", "Feb2026")
B = Cell("MNO-B", "Mar2025")


def _env(tmp_path: Path) -> PipelineContext:
    for c in (A, B):
        d = tmp_path / "input" / c.mno / c.release
        d.mkdir(parents=True)
        (d / "doc.pdf").write_text(f"doc of {c.mno}", encoding="utf-8")
    return PipelineContext.standalone(env_dir=tmp_path, model_provider="mock")


class TestFingerprinter:
    def test_taxonomy_correction_moves_graph_and_vectorstore_only(self, tmp_path):
        """corrections/taxonomy.json replaces the build-level taxonomy the
        graph and vector store read (TAX-W002); no per-cell stage reads it."""
        ctx = _env(tmp_path)
        fps = ctx.fingerprints
        stages = ("extract", "parse", "taxonomy")
        before = {s: fps.current(s, A) for s in stages}
        g, v = fps.current("graph"), fps.current("vectorstore", A)
        (tmp_path / "corrections").mkdir(exist_ok=True)
        (tmp_path / "corrections" / "taxonomy.json").write_text('{"features": []}', encoding="utf-8")
        assert {s: fps.current(s, A) for s in stages} == before
        assert fps.current("graph") != g and fps.current("vectorstore", A) != v

    def test_documents_external_is_per_cell(self, tmp_path):
        ctx = _env(tmp_path)
        fps = ctx.fingerprints
        fa, fb = fps.current("extract", A), fps.current("extract", B)
        assert fa != fb
        (tmp_path / "input" / B.mno / B.release / "doc.pdf").write_text("edited", encoding="utf-8")
        assert fps.current("extract", A) == fa          # A untouched
        assert fps.current("extract", B) != fb          # B's documents changed
        (tmp_path / "input" / A.mno / A.release / "new.pdf").write_text("more", encoding="utf-8")
        assert fps.current("extract", A) != fa          # a document added

    def test_documents_dir_matched_case_insensitively(self, tmp_path):
        ctx = _env(tmp_path)
        assert ctx.fingerprints.cell_input_dir(Cell("mno-a", "Feb2026")) == tmp_path / "input" / "MNO-A" / "Feb2026"
        assert ctx.fingerprints.cell_input_dir(Cell("MNO-Z", "Feb2026")) is None
        assert ctx.fingerprints.externals("extract", Cell("MNO-Z", "Feb2026")) == {"documents": fpm.ABSENT}

    def test_inputs_cascade_through_the_manifest(self, tmp_path):
        ctx = _env(tmp_path)
        fps = ctx.fingerprints
        p0 = fps.current("parse", A)                      # inputs absent
        assert fps.input_fps("parse", A) == {"extract": fpm.ABSENT, "profile": fpm.ABSENT}
        e1 = fps.stamp("extract", A)
        p1 = fps.current("parse", A)
        assert p1 != p0 and fps.input_fps("parse", A)["extract"] == e1
        r1 = fps.stamp("parse", A)
        # a change upstream flips extract, parse and resolve for A, nothing for B
        (tmp_path / "input" / A.mno / A.release / "doc.pdf").write_text("v2", encoding="utf-8")
        e2 = fps.stamp("extract", A)
        assert e2 != e1
        assert fps.current("parse", A) != p1
        fps.stamp("parse", A)
        assert fps.input_fps("resolve", A)["parse"] != r1
        assert fps.input_fps("parse", B) == {"extract": fpm.ABSENT, "profile": fpm.ABSENT}

    def test_global_stage_keys_on_every_cell(self, tmp_path):
        ctx = _env(tmp_path)
        fps = ctx.fingerprints
        fps.stamp("parse", A)
        g1 = fps.current("graph")
        fps.stamp("parse", B)                             # a cell added
        g2 = fps.current("graph")
        assert g1 != g2
        assert fps.input_fps("graph", None)["parse"] == fps.manifest.fingerprints("parse")
        assert fps.input_fps("graph", None)["standards"] == fpm.ABSENT
        fps.stamp("graph")
        assert fps.recorded("graph") == g2 and fps.manifest.get("graph", sg.GLOBAL_KEY) == g2

    def test_unchanged_and_force(self, tmp_path):
        ctx = _env(tmp_path)
        fps = ctx.fingerprints
        assert fps.unchanged("extract", A) is False      # nothing recorded
        fps.stamp("extract", A)
        assert fps.unchanged("extract", A) is True
        ctx.force = True
        assert fps.unchanged("extract", A) is False
        ctx.force = False
        fps.forget("extract", A)
        assert fps.unchanged("extract", A) is False

    def test_params_enter_variant_fingerprints(self, tmp_path):
        ctx = _env(tmp_path)
        fps = ctx.fingerprints
        t1 = fps.current("taxonomy", A)
        ctx.model_name = "other-model"
        assert fps.current("taxonomy", A) != t1
        assert fps.params("taxonomy") == {"model_provider": "mock", "model_name": "other-model", "llm_reasoning": ""}
        v1 = fps.current("vectorstore", A)
        ctx.embedding_model = "other-embedder"
        assert fps.current("vectorstore", A) != v1

    def test_variant_image_version_does_not_touch_corpus_stages(self, tmp_path, monkeypatch):
        ctx = _env(tmp_path)
        fps = ctx.fingerprints
        monkeypatch.delenv(fpm.VARIANT_CODE_VERSION_ENV, raising=False)
        p1, t1 = fps.current("parse", A), fps.current("taxonomy", A)
        monkeypatch.setenv(fpm.VARIANT_CODE_VERSION_ENV, "rebuilt-image")
        assert fps.current("parse", A) == p1
        assert fps.current("taxonomy", A) != t1

    def test_overview_prompt_is_per_mno(self, tmp_path, monkeypatch):
        ctx = _env(tmp_path)
        fps = ctx.fingerprints
        ov = tmp_path / "ov"
        ov.mkdir()
        monkeypatch.setenv("NORA_TAXONOMY_OVERVIEW_DIR", str(ov))
        ta, tb = fps.current("taxonomy", A), fps.current("taxonomy", B)
        (ov / "corpus_overview_MNO-A_v01.txt").write_text("about A", encoding="utf-8")
        assert fps.current("taxonomy", A) != ta
        assert fps.current("taxonomy", B) == tb
        assert fps.externals("taxonomy", B) == {"overview_prompt": fpm.ABSENT}

    def test_annotations_external(self, tmp_path):
        ctx = _env(tmp_path)
        fps = ctx.fingerprints
        ext = ctx.stage_output("extract", A)
        ext.mkdir(parents=True)
        (ext / "doc_ir.json").write_text("{}", encoding="utf-8")
        p1 = fps.current("parse", A)
        ann = tmp_path / "annotations"
        ann.mkdir()
        (ann / "doc_annotations.json").write_text('{"remove": []}', encoding="utf-8")
        assert fps.current("parse", A) != p1
        (ann / "other_annotations.json").write_text("{}", encoding="utf-8")   # another doc's: no effect
        assert fps.externals("parse", A) == fps.externals("parse", A)
        assert fps.externals("parse", B) == {"annotations": fpm.ABSENT}

    def test_stamp_records_provenance(self, tmp_path):
        ctx = _env(tmp_path)
        fps = ctx.fingerprints
        fps.stamp("extract", A)
        e = fps.manifest.entry("extract", "MNO-A/Feb2026")
        assert e["code_version"] == "1" and e["params"] == {} and e["inputs"] == {}
        assert e["externals"]["documents"] != fpm.ABSENT
        assert (tmp_path / MANIFEST_NAME).is_file()

    def test_profile_external_is_the_substituted_bound_profile(self, tmp_path):
        from core.src.profiler.profile_schema import DocumentProfile
        ctx = _env(tmp_path)
        fps = ctx.fingerprints
        assert fps.externals("profile", A) == {"profile": fpm.UNRESOLVED}   # no bindings yet
        prof = tmp_path / "p.json"
        DocumentProfile(profile_name="one").save_json(prof)
        (tmp_path / "profiles.json").write_text(json.dumps({
            "bindings": [{"mno": "MNO-A", "release": "*", "profile": str(prof)}], "default": None,
        }), encoding="utf-8")
        f1 = fps.current("profile", A)
        assert fps.externals("profile", A)["profile"] != fpm.UNRESOLVED
        DocumentProfile(profile_name="two").save_json(prof)
        assert fps.current("profile", A) != f1
        assert fps.externals("profile", B) == {"profile": fpm.UNRESOLVED}  # B unbound


# ---------------------------------------------------------------------------
# Code-version pins (D-DRAFT-2 consequence)
# ---------------------------------------------------------------------------

def package_source_hash(package: str) -> str:
    """sha256 over the package's .py files (relative path + bytes), sorted."""
    root = REPO / package.replace(".", "/")
    h = hashlib.sha256()
    for f in sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts):
        h.update(f.relative_to(root).as_posix().encode("utf-8"))
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()[:16]


class TestCodeVersionPins:
    """Each corpus stage's `CODE_VERSION` is pinned to a hash of its
    package's source. A source change without a version bump fails here,
    as the reminder D-DRAFT-2 asks for: bump `CODE_VERSION` in the package
    (when the change alters what the stage produces) and update the pin
    in `fixtures/stage_code_hashes.json` either way."""

    def test_pin_file_covers_every_corpus_stage(self):
        pins = json.loads(PIN_FILE.read_text(encoding="utf-8"))
        assert set(pins) == set(fpm.CORPUS_VERSION_MODULES)
        assert set(fpm.CORPUS_VERSION_MODULES) == sg.CORPUS_STAGES

    @pytest.mark.parametrize("stage", sorted(fpm.CORPUS_VERSION_MODULES))
    def test_source_matches_pin(self, stage):
        pins = json.loads(PIN_FILE.read_text(encoding="utf-8"))
        package = fpm.CORPUS_VERSION_MODULES[stage]
        actual = {"version": fpm.code_version(stage), "sha256": package_source_hash(package)}
        assert actual == pins[stage], (
            f"{package} changed (stage {stage!r}). If the change alters what the stage "
            f"produces from the same inputs, bump CODE_VERSION in {package}/__init__.py. "
            f"Then set fixtures/stage_code_hashes.json[{stage!r}] = {json.dumps(actual)}"
        )
