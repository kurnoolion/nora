"""The host's stage store and the corpus root (strand stage-graph, step 2).

Pins: write-once commits (read-only files, first complete result wins),
clone by hard link with a copy fallback, privatize before a write, the
building marker and its wait, two builds sharing one store (the second
finds the corpus prefix and computes nothing), `--force` leaving the
store alone, and bindings plus annotations read from the corpus root.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import time
from pathlib import Path

import pytest

from core.src.models.document import DocumentIR
from core.src.pipeline import fingerprints as fpmod
from core.src.pipeline.cells import Cell
from core.src.pipeline.manifest import MANIFEST_NAME
from core.src.pipeline.runner import PipelineContext
from core.src.pipeline.stage_store import StageStore, clone_tree, privatize
from core.src.pipeline.stages import run_parse, run_profile, run_resolve
from core.src.profiler.profile_schema import DocumentProfile

CELL = Cell("MNO-A", "Feb2026")


def _writable(p: Path) -> bool:
    return bool(p.stat().st_mode & stat.S_IWUSR)


def _tree(root: Path) -> dict[str, bytes]:
    return {f.relative_to(root).as_posix(): f.read_bytes()
            for f in sorted(root.rglob("*")) if f.is_file()}


# -- the store on its own ---------------------------------------------------

class TestStageStore:
    def _src(self, tmp_path: Path) -> Path:
        src = tmp_path / "src"
        (src / "sub").mkdir(parents=True)
        (src / "a.json").write_text("A", encoding="utf-8")
        (src / "sub" / "b.json").write_text("B", encoding="utf-8")
        return src

    def test_commit_places_read_only_output_and_provenance(self, tmp_path):
        store = StageStore(tmp_path / "stages")
        src = self._src(tmp_path)
        assert store.commit("parse", CELL, "f1", src, {"build": "b1"}) is True
        out = store.path("parse", CELL, "f1")
        assert _tree(out) == {"a.json": b"A", "sub/b.json": b"B"}
        assert not _writable(out / "a.json")
        assert store.provenance("parse", CELL, "f1")["build"] == "b1"
        assert not list(out.parent.glob("*.tmp-*"))

    def test_second_commit_loses_and_leaves_the_first(self, tmp_path):
        store = StageStore(tmp_path / "stages")
        src = self._src(tmp_path)
        store.commit("parse", CELL, "f1", src, {"build": "b1"})
        other = tmp_path / "other"
        other.mkdir()
        (other / "a.json").write_text("DIFFERENT", encoding="utf-8")
        assert store.commit("parse", CELL, "f1", other, {"build": "b2"}) is False
        assert (store.path("parse", CELL, "f1") / "a.json").read_text() == "A"
        assert store.provenance("parse", CELL, "f1")["build"] == "b1"

    def test_clone_falls_back_to_copy_across_mounts(self, tmp_path, monkeypatch):
        src = self._src(tmp_path)

        def no_link(*_a, **_k):
            raise OSError(errno.EXDEV, "cross-device link")
        monkeypatch.setattr(os, "link", no_link)
        clone_tree(src, tmp_path / "dst")
        assert _tree(tmp_path / "dst") == _tree(src)
        assert (tmp_path / "dst" / "a.json").stat().st_ino != (src / "a.json").stat().st_ino

    def test_privatize_breaks_links_keeps_mtime(self, tmp_path):
        store = StageStore(tmp_path / "stages")
        store.commit("parse", CELL, "f1", self._src(tmp_path), {})
        view = tmp_path / "view"
        store.link_view("parse", CELL, "f1", view)
        stored = store.path("parse", CELL, "f1") / "a.json"
        assert (view / "a.json").stat().st_ino == stored.stat().st_ino
        mtime = stored.stat().st_mtime
        assert privatize(view) == 2
        assert (view / "a.json").stat().st_ino != stored.stat().st_ino
        assert _writable(view / "a.json")
        assert (view / "a.json").stat().st_mtime == mtime
        (view / "a.json").write_text("EDIT", encoding="utf-8")
        assert stored.read_text() == "A"

    def test_wait_on_a_foreign_marker_until_the_output_appears(self, tmp_path):
        calls = []
        store = StageStore(tmp_path / "stages", wait_seconds=60, poll_seconds=0)
        src = self._src(tmp_path)
        store.mark_building("parse", CELL, "f1", "other-build")

        def sleep(_s):
            calls.append(1)
            store.commit("parse", CELL, "f1", src, {})   # the other producer finishes
        store._sleep = sleep
        assert store.wait_for("parse", CELL, "f1", "this-build") is True
        assert calls

    def test_own_or_stale_marker_is_not_waited_on(self, tmp_path):
        store = StageStore(tmp_path / "stages", wait_seconds=60, poll_seconds=0,
                           sleep=lambda _s: pytest.fail("must not wait"))
        store.mark_building("parse", CELL, "f1", "this-build")
        assert store.wait_for("parse", CELL, "f1", "this-build") is False
        store.mark_building("parse", CELL, "f2", "other-build")
        old = time.time() - 3600
        os.utime(store.marker_path("parse", CELL, "f2"), (old, old))
        assert store.wait_for("parse", CELL, "f2", "this-build") is False

    def test_wait_runs_out(self, tmp_path):
        store = StageStore(tmp_path / "stages", wait_seconds=0.05, poll_seconds=0.01)
        store.mark_building("parse", CELL, "f1", "other-build")
        assert store.wait_for("parse", CELL, "f1", "this-build") is False

    def test_release_removes_only_the_owners_marker(self, tmp_path):
        store = StageStore(tmp_path / "stages")
        store.mark_building("parse", CELL, "f1", "owner")
        store.release("parse", CELL, "f1", "someone-else")
        assert store.marker_path("parse", CELL, "f1").exists()
        store.release("parse", CELL, "f1", "owner")
        assert not store.marker_path("parse", CELL, "f1").exists()


# -- builds sharing one store and one corpus root ---------------------------

def _corpus(root: Path) -> Path:
    corpus = root / "corpus"
    (corpus / "annotations").mkdir(parents=True)
    prof = corpus / "prof_MNO-A.json"
    DocumentProfile(profile_name="test-MNO-A").save_json(prof)
    (corpus / "profiles.json").write_text(json.dumps(
        {"bindings": [{"mno": "MNO-A", "release": "*", "profile": str(prof)}], "default": None}),
        encoding="utf-8")
    return corpus


def _build(root: Path, name: str, corpus: Path, stages: Path) -> PipelineContext:
    env = root / name
    d = env / "input" / CELL.mno / CELL.release
    d.mkdir(parents=True)
    (d / "doc.pdf").write_text("x", encoding="utf-8")
    irs = env / "out" / "extract" / CELL.mno / CELL.release
    irs.mkdir(parents=True)
    DocumentIR(source_file="MNO-A_doc.pdf", source_format="pdf", mno=CELL.mno,
               release=CELL.release, doc_type="requirement").save_json(irs / "MNO-A_doc_ir.json")
    ctx = PipelineContext.standalone(env_dir=env)
    ctx.corpus_dir = corpus
    ctx.stages_root = stages
    return ctx


def _manifest(ctx: PipelineContext) -> dict:
    return json.loads((ctx.env_dir / MANIFEST_NAME).read_text(encoding="utf-8"))["stages"]


def _prefix(ctx: PipelineContext):
    return run_profile(ctx), run_parse(ctx), run_resolve(ctx)


class TestSharedStore:
    def test_second_build_finds_the_corpus_prefix(self, tmp_path):
        corpus, stages = _corpus(tmp_path), tmp_path / "stages"
        a = _build(tmp_path, "build-a", corpus, stages)
        pa, ra, _ = _prefix(a)
        assert ra.stats["docs"] == 1
        key = f"{CELL.mno}/{CELL.release}"
        for st in ("profile", "parse", "resolve"):
            entry = _manifest(a)[st][key]
            assert entry["origin"] == "computed"
            assert StageStore(stages).has(st, CELL, entry["fp"])

        b = _build(tmp_path, "build-b", corpus, stages)
        pb, rb, sb = _prefix(b)
        assert pb.stats["reused"] == 1
        assert rb.stats["docs"] == 0 and rb.stats["skipped"] == 1
        for st in ("profile", "parse", "resolve"):
            assert _manifest(b)[st][key]["origin"] == "store"
            assert _manifest(b)[st][key]["fp"] == _manifest(a)[st][key]["fp"]
        tree = "MNO-A_doc_tree.json"
        assert ((b.env_dir / "out/parse/MNO-A/Feb2026" / tree).read_bytes()
                == (a.env_dir / "out/parse/MNO-A/Feb2026" / tree).read_bytes())
        assert not list(stages.rglob("*.building"))

    def test_bindings_and_annotations_come_from_the_corpus_root(self, tmp_path):
        corpus, stages = _corpus(tmp_path), tmp_path / "stages"
        a = _build(tmp_path, "build-a", corpus, stages)
        assert not (a.env_dir / "profiles.json").exists()
        assert a.bindings_dir == corpus and a.annotations_dir == corpus / "annotations"
        p, _r, _ = _prefix(a)
        assert p.status == "OK"
        fp_before = _manifest(a)["parse"]["MNO-A/Feb2026"]["fp"]
        (corpus / "annotations" / "MNO-A_doc_annotations.json").write_text(
            json.dumps({"annotations": []}), encoding="utf-8")
        assert a.fingerprints.current("parse", CELL) != fp_before

    def test_rerun_never_edits_the_store(self, tmp_path):
        corpus, stages = _corpus(tmp_path), tmp_path / "stages"
        a = _build(tmp_path, "build-a", corpus, stages)
        _prefix(a)
        fp = _manifest(a)["parse"]["MNO-A/Feb2026"]["fp"]
        stored = StageStore(stages).path("parse", CELL, fp)
        before = _tree(stored)
        a.force = True
        r = run_parse(a)
        assert r.stats["docs"] == 1
        assert _tree(stored) == before
        assert _manifest(a)["parse"]["MNO-A/Feb2026"]["origin"] == "forced"
        view = a.env_dir / "out/parse/MNO-A/Feb2026/MNO-A_doc_tree.json"
        assert _writable(view)

    def test_profile_edit_computes_a_new_entry(self, tmp_path):
        corpus, stages = _corpus(tmp_path), tmp_path / "stages"
        a = _build(tmp_path, "build-a", corpus, stages)
        _prefix(a)
        prof = corpus / "prof_MNO-A.json"
        data = json.loads(prof.read_text(encoding="utf-8"))
        data["profile_name"] = "changed"
        prof.write_text(json.dumps(data), encoding="utf-8")
        _p, r, _ = _prefix(a)
        assert r.stats["docs"] == 1
        assert len([d for d in (stages / "parse/MNO-A/Feb2026").iterdir() if d.is_dir()]) == 2

    def test_without_a_store_nothing_is_stored(self, tmp_path):
        corpus = _corpus(tmp_path)
        a = _build(tmp_path, "build-a", corpus, tmp_path / "stages")
        a.stages_root = None
        _prefix(a)
        assert not (tmp_path / "stages").exists()
        assert _manifest(a)["parse"]["MNO-A/Feb2026"]["origin"] == "computed"


class TestInStore:
    def test_tiers_and_versions(self, tmp_path, monkeypatch):
        ctx = _build(tmp_path, "b", _corpus(tmp_path), tmp_path / "stages")
        fps = ctx.fingerprints
        for st in ("extract", "profile", "parse", "resolve"):
            assert fps.in_store(st)
        assert not fps.in_store("vectorstore")
        assert not fps.in_store("graph")          # global
        monkeypatch.delenv(fpmod.VARIANT_CODE_VERSION_ENV, raising=False)
        assert not fps.in_store("taxonomy")       # unversioned image
        monkeypatch.setenv(fpmod.VARIANT_CODE_VERSION_ENV, "abc1234")
        assert fps.in_store("taxonomy")
        ctx.stages_root = None
        ctx.state.pop("_stage_store", None)
        assert not fps.in_store("parse")

    def test_factories_read_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NORA_STAGES_ROOT", str(tmp_path / "s"))
        monkeypatch.setenv("NORA_CORPUS_DIR", str(tmp_path / "c"))
        ctx = PipelineContext.standalone(env_dir=tmp_path / "e")
        assert ctx.stages_root == tmp_path / "s" and ctx.corpus_dir == tmp_path / "c"
        monkeypatch.setenv("NORA_STAGES_ROOT", "")
        monkeypatch.delenv("NORA_CORPUS_DIR")
        ctx = PipelineContext.standalone(env_dir=tmp_path / "e")
        assert ctx.stages_root is None and ctx.corpus_dir is None
        assert ctx.bindings_dir == ctx.env_dir
