"""Tests for golden_release_cli — migrate / advance / review and the text
check. Synthetic parse trees and samples only (NFR-8)."""

from __future__ import annotations

import json

from core.src.eval.golden import (
    GoldenSample,
    GroundTruthEntry,
    golden_root,
    load_samples,
    needs_review,
    save_sample,
)
from core.src.eval.golden_release_cli import (
    cell_texts,
    main,
    parse_cells,
    text_check,
)
from core.src.eval.release_map import ReleaseMap, load_release_map, save_release_map


def _tree(env, mno, rel, reqs):
    d = env / "out" / "parse" / mno / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "DOC_tree.json").write_text(json.dumps({
        "plan_id": "PLAN_X",
        "requirements": [
            {"req_id": rid, "title": "T", "text": text} for rid, text in reqs
        ],
    }))


def _sample(env, sid, entries, kind="standard", status="stage1-ready",
            query="widget retry?"):
    s = GoldenSample(sample_id=sid, query=query, kind=kind, status=status,
                     ground_truth=entries)
    save_sample(env, s)
    return s


def _gt(rid, rel, mno="mno-b"):
    return GroundTruthEntry(req_id=rid, mno=mno, release=rel)


def _corpus(env):
    # R-1 unchanged, R-2 changed, R-3 removed in the newer release.
    _tree(env, "mno-b", "Jan2026",
          [("R-1", "same text"), ("R-2", "old text"), ("R-3", "gone")])
    _tree(env, "mno-b", "Apr2026",
          [("R-1", "same   text"), ("R-2", "new text"), ("R-4", "added")])
    _tree(env, "mno-a", "Jan2026", [("A-1", "x")])


def test_parse_cells_and_texts(tmp_path):
    _corpus(tmp_path)
    assert parse_cells(tmp_path) == {
        "mno-a": ["Jan2026"], "mno-b": ["Jan2026", "Apr2026"]}
    # Whitespace-normalised, so re-flowed text compares equal.
    assert cell_texts(tmp_path, "mno-b", "Apr2026")["R-1"] == \
        cell_texts(tmp_path, "mno-b", "Jan2026")["R-1"]


def test_text_check_classifies_entries(tmp_path):
    _corpus(tmp_path)
    _sample(tmp_path, "gs-0001", [_gt("R-1", "Jan2026")])
    _sample(tmp_path, "gs-0002", [_gt("R-1", "Jan2026"), _gt("R-2", "Jan2026"),
                                  _gt("R-3", "Jan2026"), _gt("R-9", "Jan2026")])
    _sample(tmp_path, "gs-0003", [_gt("R-1", "Apr2026")])       # already on N
    _sample(tmp_path, "gs-0004", [_gt("R-1", "Jan2026")], kind="version")
    _sample(tmp_path, "gs-0005", [_gt("R-1", "N")], kind="version")
    _sample(tmp_path, "gs-0006", [GroundTruthEntry(req_id="R-1", mno="mno-b")])
    rep = text_check(load_samples(tmp_path), tmp_path, "mno-b", "Apr2026")
    by_id = {c.sample_id: c for c in rep.samples}
    assert set(by_id) == {"gs-0001", "gs-0002", "gs-0005"}
    assert by_id["gs-0001"].unchanged == ["R-1"] and not by_id["gs-0001"].flagged
    c2 = by_id["gs-0002"]
    assert (c2.unchanged, c2.changed, c2.absent) == (["R-1"], ["R-2"], ["R-3", "R-9"])
    assert c2.flagged
    assert by_id["gs-0005"].relative and by_id["gs-0005"].flagged
    assert rep.counts()["unattributed_entries"] == 1


def test_unverified_when_missing_from_stored_release(tmp_path):
    _corpus(tmp_path)
    _sample(tmp_path, "gs-0001", [_gt("R-4", "Jan2026")])
    rep = text_check(load_samples(tmp_path), tmp_path, "mno-b", "Apr2026")
    assert rep.samples[0].unverified == ["R-4"] and rep.samples[0].flagged


def _run(env, *args):
    return main([args[0], "--env-dir", str(env), *args[1:]])


def test_migrate_dry_run_writes_nothing(tmp_path, capsys):
    _corpus(tmp_path)
    _sample(tmp_path, "gs-0001", [_gt("R-2", "Jan2026")])
    assert _run(tmp_path, "migrate") == 0
    out = capsys.readouterr().out
    assert "mno-b: N=Apr2026" in out and "dry run" in out
    assert load_release_map(golden_root(tmp_path)) is None
    assert not needs_review(load_samples(tmp_path)[0])


def test_migrate_writes_map_and_flags(tmp_path, capsys):
    _corpus(tmp_path)
    _sample(tmp_path, "gs-0001", [_gt("R-1", "Jan2026")])
    _sample(tmp_path, "gs-0002", [_gt("R-2", "Jan2026")])
    _sample(tmp_path, "gs-0003", [_gt("R-1", "N")], kind="version")
    _sample(tmp_path, "gs-0004", [_gt("R-1", "Jan2026")],
            query="what changed in the jan 2026 release?")
    assert _run(tmp_path, "migrate", "--yes", "--by", "tester") == 0
    out = capsys.readouterr().out
    assert "gs-0004" in out.split("names a release")[1].splitlines()[1]
    rmap = load_release_map(golden_root(tmp_path))
    assert rmap.version == 1 and rmap.current("mno-b") == "Apr2026"
    assert rmap.updated_by == "tester"
    s = {x.sample_id: x for x in load_samples(tmp_path)}
    # Unchanged: provenance carried forward, no flag.
    assert s["gs-0001"].ground_truth[0].release == "Apr2026"
    assert not needs_review(s["gs-0001"])
    # Changed: flagged, stored release kept for the review.
    assert s["gs-0002"].ground_truth[0].release == "Jan2026"
    r = s["gs-0002"].golden_meta["review"]["mno-b"]
    assert r["changed"] == ["R-2"] and r["from"] == ["Jan2026"]
    assert r["to"] == "Apr2026"
    # N is not moving on migrate: relative samples are left alone.
    assert not needs_review(s["gs-0003"])
    assert _run(tmp_path, "migrate", "--yes") == 1      # map exists


def test_migrate_n_override(tmp_path):
    _corpus(tmp_path)
    assert _run(tmp_path, "migrate", "--yes", "--n", "mno-b=Jan2026") == 0
    rmap = load_release_map(golden_root(tmp_path))
    assert rmap.mnos["mno-b"] == ["Jan2026"]
    assert _run(tmp_path, "migrate", "--n", "mno-z=Jan2026") == 1


def test_advance(tmp_path, capsys):
    _corpus(tmp_path)
    save_release_map(golden_root(tmp_path), ReleaseMap(
        version=4, mnos={"mno-a": ["Jan2026"], "mno-b": ["Jan2026"]}))
    _sample(tmp_path, "gs-0001", [_gt("R-1", "Jan2026"), _gt("R-3", "Jan2026")])
    _sample(tmp_path, "gs-0002", [_gt("R-1", "N")], kind="version")
    assert _run(tmp_path, "advance", "--mno", "mno-b",
                "--release", "Apr2026") == 0
    assert "dry run" in capsys.readouterr().out
    assert load_release_map(golden_root(tmp_path)).version == 4
    assert _run(tmp_path, "advance", "--mno", "mno-b", "--release",
                "Apr2026", "--yes") == 0
    rmap = load_release_map(golden_root(tmp_path))
    assert rmap.version == 5 and rmap.current("mno-b") == "Apr2026"
    assert load_release_map(golden_root(tmp_path), version=4).current(
        "mno-b") == "Jan2026"
    s = {x.sample_id: x for x in load_samples(tmp_path)}
    gt = {e.req_id: e.release for e in s["gs-0001"].ground_truth}
    assert gt == {"R-1": "Apr2026", "R-3": "Jan2026"}
    assert s["gs-0001"].golden_meta["review"]["mno-b"]["absent"] == ["R-3"]
    assert s["gs-0002"].golden_meta["review"]["mno-b"]["relative"] is True


def test_advance_refusals(tmp_path):
    _corpus(tmp_path)
    assert _run(tmp_path, "advance", "--mno", "mno-b",
                "--release", "Apr2026") == 1                 # no map
    save_release_map(golden_root(tmp_path), ReleaseMap(
        version=1, mnos={"mno-b": ["Apr2026"]}))
    assert _run(tmp_path, "advance", "--mno", "mno-b",
                "--release", "Jan2026") == 1                 # not newer
    assert _run(tmp_path, "advance", "--mno", "mno-b",
                "--release", "Oct2026") == 1                 # no parse trees
    assert _run(tmp_path, "advance", "--mno", "mno-b",
                "--release", "2026-10") == 1                 # not MMMYYYY


def test_review_list_and_clear(tmp_path, capsys):
    _corpus(tmp_path)
    _sample(tmp_path, "gs-0001", [_gt("R-2", "Jan2026")])
    _run(tmp_path, "migrate", "--yes")
    capsys.readouterr()
    assert _run(tmp_path, "review") == 0
    out = capsys.readouterr().out
    assert "gs-0001 mno-b Jan2026 -> Apr2026: changed=R-2" in out
    assert "1 sample(s) flagged" in out
    assert _run(tmp_path, "review", "--clear", "gs-0001", "--mno", "mno-a") == 1
    assert _run(tmp_path, "review", "--clear", "gs-0001") == 0
    assert not needs_review(load_samples(tmp_path)[0])
    assert _run(tmp_path, "review", "--clear", "gs-0001") == 1


def test_show(tmp_path, capsys):
    assert _run(tmp_path, "show") == 1
    save_release_map(golden_root(tmp_path), ReleaseMap(
        version=1, mnos={"mno-b": ["Jan2026", "Apr2026"]}))
    capsys.readouterr()
    assert _run(tmp_path, "show") == 0
    assert "mno-b: N=Apr2026  older: Jan2026" in capsys.readouterr().out
