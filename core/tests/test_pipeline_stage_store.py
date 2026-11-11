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
        clone_tree(store.path("parse", CELL, "f1"), view)   # a pre-step-4 build's clone
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
        # step 4: neither build keeps a copy; both read the one stored output
        for ctx in (a, b):
            assert not ctx.stage_output("parse", CELL).exists()
        assert b.stage_input("parse", CELL) == a.stage_input("parse", CELL)
        assert b.stage_input("parse", CELL).parent.parent.parent.parent == stages
        assert (b.stage_input("parse", CELL) / tree).is_file()
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


# -- garbage collection (step 3, slice 4) ------------------------------------

from core.src.pipeline import stage_store as ss  # noqa: E402
from core.src.pipeline.error_codes import PipelineError  # noqa: E402

GC_CELL = Cell("MNOA", "Apr2031")
DAY = 86400.0


def _stored(store: StageStore, stage: str, fp: str, tmp: Path, cell: Cell = GC_CELL) -> Path:
    src = tmp / f"src-{stage}-{fp}"
    src.mkdir()
    (src / "out.json").write_text(fp)
    assert store.commit(stage, cell, fp, src, {"build": "b"})
    return store.path(stage, cell, fp)


def _manifest_file(d: Path, entries: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    stages = {s: {k: {"fp": fp} for k, fp in cells.items()} for s, cells in entries.items()}
    (d / "STAGES.json").write_text(json.dumps({"version": 1, "stages": stages}))


def _registry(db: Path, stage_fps: list[str], column: bool = True) -> Path:
    import sqlite3
    con = sqlite3.connect(db)
    cols = "label TEXT, stage_fps TEXT" if column else "label TEXT"
    con.execute(f"CREATE TABLE release_set({cols})")
    for i, v in enumerate(stage_fps):
        con.execute("INSERT INTO release_set VALUES (?,?)" if column else "INSERT INTO release_set VALUES (?)",
                    (f"l{i}", v) if column else (f"l{i}",))
    con.commit()
    con.close()
    return db


def test_gc_keeps_what_manifests_and_release_sets_name(tmp_path):
    store = StageStore(tmp_path / "stages")
    a = _stored(store, "parse", "aaaa", tmp_path)
    b = _stored(store, "parse", "bbbb", tmp_path)
    c = _stored(store, "taxonomy", "cccc", tmp_path)
    d = _stored(store, "resolve", "dddd", tmp_path)
    builds = tmp_path / "builds"
    _manifest_file(builds / "b1", {"parse": {"MNOA/Apr2031": "aaaa"}, "graph": {"*": "gggg"}})
    labels = tmp_path / "labels"
    _manifest_file(labels / "label-1", {"taxonomy": {"MNOA/Apr2031": "cccc"}})
    db = _registry(tmp_path / "registry.db", [json.dumps({"resolve": {"MNOA/Apr2031": "dddd"}}), ""])
    later = time.time() + 2 * DAY

    dry = ss.gc(store, [builds, labels], [db], now=later)
    assert dry.manifests == 2 and dry.kept == 3
    assert dry.removed == ["parse/MNOA/Apr2031/bbbb"]
    assert b.is_dir()                      # a dry run removes nothing

    rep = ss.gc(store, [builds, labels], [db], apply=True, now=later)
    assert rep.removed == ["parse/MNOA/Apr2031/bbbb"]
    assert not b.exists() and not store.provenance_path("parse", GC_CELL, "bbbb").exists()
    assert a.is_dir() and c.is_dir() and d.is_dir()
    assert (rep.freed_bytes, rep.shared_bytes) == (0, 4)   # the source still links it


def test_gc_keeps_young_and_building_entries(tmp_path):
    store = StageStore(tmp_path / "stages", wait_seconds=3600)
    young = _stored(store, "parse", "aaaa", tmp_path)
    old = _stored(store, "parse", "bbbb", tmp_path)
    busy = _stored(store, "parse", "cccc", tmp_path)
    past = time.time() - 2 * DAY
    for p in (old, busy):
        os.utime(p, (past, past))
    store.mark_building("parse", GC_CELL, "cccc", "other-build")
    builds = tmp_path / "builds"
    builds.mkdir()
    rep = ss.gc(store, [builds], [], apply=True)
    assert (rep.young, rep.building, rep.removed) == (1, 1, ["parse/MNOA/Apr2031/bbbb"])
    assert rep.empty_sources == [str(builds)]
    assert young.is_dir() and busy.is_dir() and not old.exists()


def test_gc_clears_debris_and_empty_dirs(tmp_path):
    store = StageStore(tmp_path / "stages", wait_seconds=60)
    cell_dir = store.cell_dir("parse", GC_CELL)
    cell_dir.mkdir(parents=True)
    (cell_dir / "eeee.tmp-0123456789ab").mkdir()
    (cell_dir / "ffff.json").write_text("{}")          # provenance without its output
    (cell_dir / "ffff.building").write_text("{}")      # an abandoned marker
    past = time.time() - 2 * DAY
    for p in cell_dir.iterdir():
        os.utime(p, (past, past))
    builds = tmp_path / "builds"
    builds.mkdir()
    rep = ss.gc(store, [builds], [], apply=True)
    assert len(rep.debris) == 3 and rep.removed == []
    assert not (tmp_path / "stages" / "parse").exists()   # empty parents go too


def test_gc_leaves_builds_and_labels_intact(tmp_path):
    store = StageStore(tmp_path / "stages")
    _stored(store, "parse", "aaaa", tmp_path)
    view = tmp_path / "builds" / "b1" / "out" / "parse" / "MNOA" / "Apr2031"
    clone_tree(store.path("parse", GC_CELL, "aaaa"), view)   # a pre-step-4 build's clone
    (tmp_path / "builds" / "b1" / "STAGES.json").write_text(json.dumps({"version": 1, "stages": {}}))
    rep = ss.gc(store, [tmp_path / "builds"], [], apply=True, now=time.time() + 2 * DAY)
    assert rep.removed == ["parse/MNOA/Apr2031/aaaa"]
    assert (view / "out.json").read_text() == "aaaa"     # the build's clone survives


@pytest.mark.parametrize("case", ["nothing", "bad-manifest", "missing-dir", "missing-db", "bad-fps"])
def test_gc_refuses_when_it_cannot_know_what_to_keep(tmp_path, case):
    store = StageStore(tmp_path / "stages")
    kept = _stored(store, "parse", "aaaa", tmp_path)
    builds = tmp_path / "builds"
    builds.mkdir()
    keep, dbs = [builds], []
    if case == "nothing":
        keep = []
    elif case == "bad-manifest":
        (builds / "b1").mkdir()
        (builds / "b1" / "STAGES.json").write_text("{not json")
    elif case == "missing-dir":
        keep = [tmp_path / "nowhere"]
    elif case == "missing-db":
        dbs = [tmp_path / "none.db"]
    else:
        dbs = [_registry(tmp_path / "registry.db", ["{bad"])]
    with pytest.raises(PipelineError) as e:
        ss.gc(store, keep, dbs, apply=True, now=time.time() + 2 * DAY)
    assert e.value.code == "PIP-E006"
    assert kept.is_dir()


def test_gc_reads_a_registry_without_the_column(tmp_path):
    db = _registry(tmp_path / "registry.db", [], column=False)
    assert ss.keep_set([], [db]) == (set(), 0)


def test_gc_cli_dry_run_and_refusal(tmp_path, capsys):
    store = StageStore(tmp_path / "stages")
    _stored(store, "parse", "aaaa", tmp_path)
    builds = tmp_path / "builds"
    builds.mkdir()
    assert ss.main(["gc", str(tmp_path / "stages"), "--keep-from", str(builds),
                    "--min-age-hours", "0", "-v"]) == 0
    cap = capsys.readouterr()
    out = cap.out
    assert "holds no STAGES.json" in cap.err
    assert "would remove parse/MNOA/Apr2031/aaaa" in out and "to_remove=1" in out and "dry run" in out
    assert store.has("parse", GC_CELL, "aaaa")
    assert ss.main(["gc", str(tmp_path / "stages")]) == 1
    assert "PIP-E006" in capsys.readouterr().err


# -- step 4 slice 4: per-document extract reuse across store entries ----------

XCELL = Cell("MNO-A", "Apr2031")


def _fake_extract(calls: list):
    def extract_document(path, mno, release, doc_type, images_root=None, **_kw):
        calls.append(Path(path).name)
        if images_root is not None:
            img = Path(images_root) / Path(path).stem
            img.mkdir(parents=True, exist_ok=True)
            (img / "fig.png").write_bytes(Path(path).read_bytes())
        return DocumentIR(source_file=Path(path).name, source_format="pdf", mno=mno,
                          release=release, doc_type="requirement")
    return extract_document


def _xbuild(root: Path, name: str, corpus: Path, stages: Path, docs: dict) -> PipelineContext:
    env = root / name
    d = env / "input" / XCELL.mno / XCELL.release
    d.mkdir(parents=True)
    for n, body in docs.items():
        (d / n).write_text(body, encoding="utf-8")
    ctx = PipelineContext.standalone(env_dir=env)
    ctx.corpus_dir = corpus
    ctx.stages_root = stages
    return ctx


class TestExtractReuse:
    @pytest.fixture(autouse=True)
    def _fake(self, monkeypatch):
        self.calls: list = []
        monkeypatch.setattr("core.src.extraction.registry.extract_document", _fake_extract(self.calls))

    def _run(self, ctx):
        from core.src.pipeline.stages import run_extract
        return run_extract(ctx)

    def test_unchanged_documents_come_from_the_previous_entry(self, tmp_path):
        corpus, stages = _corpus(tmp_path), tmp_path / "stages"
        a = _xbuild(tmp_path, "a", corpus, stages, {"d1.pdf": "one", "d2.pdf": "two"})
        ra = self._run(a)
        assert ra.stats["docs"] == 2 and sorted(self.calls) == ["d1.pdf", "d2.pdf"]
        fa = _manifest(a)["extract"]["MNO-A/Apr2031"]["fp"]
        entry_a = StageStore(stages).path("extract", XCELL, fa)
        assert sorted(json.loads((entry_a / ".sources.json").read_text())) == ["d1_ir.json", "d2_ir.json"]

        self.calls.clear()
        b = _xbuild(tmp_path, "b", corpus, stages, {"d1.pdf": "one", "d2.pdf": "two, revised"})
        rb = self._run(b)
        assert self.calls == ["d2.pdf"]
        assert rb.stats["reused_docs"] == 1 and rb.stats["docs"] == 1 and rb.status == "OK"
        fb = _manifest(b)["extract"]["MNO-A/Apr2031"]["fp"]
        assert fb != fa
        entry_b = StageStore(stages).path("extract", XCELL, fb)
        assert (entry_b / "d1_ir.json").read_bytes() == (entry_a / "d1_ir.json").read_bytes()
        assert (entry_b / "images" / "d1" / "fig.png").read_text() == "one"
        assert (entry_b / "images" / "d2" / "fig.png").read_text() == "two, revised"
        assert sorted(json.loads((entry_b / ".sources.json").read_text())) == ["d1_ir.json", "d2_ir.json"]
        assert (entry_a / "d2_ir.json").exists()          # the earlier entry is untouched

    def test_force_and_entries_without_sources_reuse_nothing(self, tmp_path):
        corpus, stages = _corpus(tmp_path), tmp_path / "stages"
        a = _xbuild(tmp_path, "a", corpus, stages, {"d1.pdf": "one", "d2.pdf": "two"})
        self._run(a)
        self.calls.clear()
        b = _xbuild(tmp_path, "b", corpus, stages, {"d1.pdf": "one", "d2.pdf": "changed"})
        b.force = True
        assert self._run(b).stats["reused_docs"] == 0 and sorted(self.calls) == ["d1.pdf", "d2.pdf"]
        # an entry stored before slice 4 has no sources record
        for p in stages.rglob(".sources.json"):
            os.chmod(p.parent, 0o755)
            p.unlink()
        self.calls.clear()
        c = _xbuild(tmp_path, "c", corpus, stages, {"d1.pdf": "one", "d2.pdf": "third"})
        assert self._run(c).stats["reused_docs"] == 0 and sorted(self.calls) == ["d1.pdf", "d2.pdf"]

    def test_a_changed_parameter_reuses_nothing(self, tmp_path, monkeypatch):
        corpus, stages = _corpus(tmp_path), tmp_path / "stages"
        a = _xbuild(tmp_path, "a", corpus, stages, {"d1.pdf": "one", "d2.pdf": "two"})
        self._run(a)
        self.calls.clear()
        b = _xbuild(tmp_path, "b", corpus, stages, {"d1.pdf": "one", "d2.pdf": "changed"})
        monkeypatch.setattr(fpmod, "code_version", lambda stage: "other")
        assert self._run(b).stats["reused_docs"] == 0 and sorted(self.calls) == ["d1.pdf", "d2.pdf"]
