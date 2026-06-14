"""Tests for the NORA → BEIR adapter — multi-cell partitioning (multi-mno-sira).

Focused on the cell-identity + fail-loud behavior added for multi-MNO SIRA
(D-DRAFT-3/5/6). The single-dataset emission path predates this strand and
is exercised end-to-end via the SIRA sandbox runs, not unit-tested here.
"""

from __future__ import annotations

import pytest

import json

from sandbox.adapter.nora_to_beir import (
    _cell_dirname,
    _emit_multi_cell,
    _partition_trees_by_cell,
    _RELEASE_RE,
)


def _tree(mno: str, release: str, plan_id: str = "PLAN") -> dict:
    return {"mno": mno, "release": release, "plan_id": plan_id, "requirements": []}


def _tree_with_req(mno: str, release: str, plan_id: str, req_id: str) -> dict:
    return {
        "mno": mno, "release": release,
        "plan_id": plan_id, "plan_name": f"{plan_id} plan",
        "requirements": [{
            "req_id": req_id, "title": f"{req_id} title",
            "text": f"body of {req_id}", "section_number": "1.1",
        }],
    }


# ── _RELEASE_RE — the MMMYYYY convention ──────────────────────────

@pytest.mark.parametrize("good", ["Feb2026", "Jan2025", "Dec2099", "Oct2025"])
def test_release_re_accepts_mmmyyyy(good):
    assert _RELEASE_RE.match(good)


@pytest.mark.parametrize("bad", [
    "OA-baseline",      # the legacy free-form label
    "February 2026",    # the document release_date format (display-only)
    "Feb-2026",         # punctuation
    "feb2026",          # lowercase month
    "FEB2026",          # uppercase month
    "Feb26",            # 2-digit year
    "Q1-2026",          # quarter phrasing
    "Feb2026x",         # trailing junk
    "",                 # empty
])
def test_release_re_rejects_non_mmmyyyy(bad):
    assert not _RELEASE_RE.match(bad)


# ── _cell_dirname — source-case-preserved naming ──────────────────

def test_cell_dirname_double_underscore_source_case():
    assert _cell_dirname(("VZW", "Feb2026")) == "VZW__Feb2026"
    assert _cell_dirname(("TMO", "Jan2026")) == "TMO__Jan2026"


# ── _partition_trees_by_cell ──────────────────────────────────────

def test_partition_groups_by_cell():
    trees = [
        _tree("VZW", "Feb2026", "A"),
        _tree("VZW", "Feb2026", "B"),
        _tree("TMO", "Jan2026", "C"),
        _tree("VZW", "Oct2025", "D"),
    ]
    cells = _partition_trees_by_cell(trees)
    assert set(cells) == {("VZW", "Feb2026"), ("TMO", "Jan2026"), ("VZW", "Oct2025")}
    assert [t["plan_id"] for t in cells[("VZW", "Feb2026")]] == ["A", "B"]
    assert len(cells[("VZW", "Oct2025")]) == 1


def test_partition_fail_loud_on_non_mmmyyyy_release():
    trees = [_tree("VZW", "Feb2026", "ok"), _tree("VZW", "OA-baseline", "bad")]
    with pytest.raises(ValueError) as exc:
        _partition_trees_by_cell(trees)
    msg = str(exc.value)
    assert "OA-baseline" in msg          # names the offender
    assert "MMMYYYY" in msg              # names the expected shape
    assert "input directory" in msg      # points at the fix location


def test_partition_fail_loud_collects_all_violations():
    trees = [
        _tree("VZW", "bad1", "p1"),
        _tree("VZW", "Feb2026", "ok"),
        _tree("TMO", "bad2", "p2"),
    ]
    with pytest.raises(ValueError) as exc:
        _partition_trees_by_cell(trees)
    msg = str(exc.value)
    assert "bad1" in msg and "bad2" in msg   # both reported in one pass
    assert "2 tree(s)" in msg


def test_partition_fail_loud_on_missing_mno():
    trees = [_tree("", "Feb2026", "no-mno")]
    with pytest.raises(ValueError, match="missing MNO"):
        _partition_trees_by_cell(trees)


# ── _emit_multi_cell — on-disk layout + partition end-to-end ──────

def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_emit_multi_cell_layout_and_partition(tmp_path):
    trees = [
        _tree_with_req("VZW", "Feb2026", "A", "req:A:1"),
        _tree_with_req("VZW", "Feb2026", "B", "req:B:1"),
        _tree_with_req("TMO", "Jan2026", "C", "req:C:1"),
    ]
    names = _emit_multi_cell(
        trees, tmp_path, section_max_depth=2, wipe_index=False, wipe_all=False,
    )
    # two cells, source-case-preserved names
    assert sorted(names) == ["TMO__Jan2026", "VZW__Feb2026"]

    vzw = tmp_path / "VZW__Feb2026" / "raw"
    tmo = tmp_path / "TMO__Jan2026" / "raw"
    # each cell has the four files; queries/qrels carry a single dummy
    # index-build row (keeps SIRA's bm25 eval+pick-best alive so
    # index/best is produced) targeting a real corpus id.
    for raw in (vzw, tmo):
        assert (raw / "corpus.jsonl").is_file()
        assert (raw / "metadata.json").is_file()
        q = _read_jsonl(raw / "queries-test.jsonl")
        qr = _read_jsonl(raw / "qrels-test.jsonl")
        assert len(q) == 1 and q[0]["_id"] == "_idxbuild_0"
        assert len(qr) == 1 and qr[0]["query-id"] == "_idxbuild_0"
        corpus_ids = {r["_id"] for r in _read_jsonl(raw / "corpus.jsonl")}
        assert qr[0]["corpus-id"] in corpus_ids   # qrel target is real

    # partition correctness: VZW cell holds A+B's reqs, TMO holds C's.
    # (doc:/section: multigranularity rows also present — tested in
    # plan-aware-sira; here we only assert the per-req partitioning.)
    vzw_ids = {r["_id"] for r in _read_jsonl(vzw / "corpus.jsonl")}
    tmo_ids = {r["_id"] for r in _read_jsonl(tmo / "corpus.jsonl")}
    assert {"req:A:1", "req:B:1"} <= vzw_ids
    assert "req:C:1" not in vzw_ids
    assert "req:C:1" in tmo_ids
    assert "req:A:1" not in tmo_ids


def test_emit_multi_cell_metadata_name_is_cell_dir(tmp_path):
    trees = [_tree_with_req("VZW", "Feb2026", "A", "req:A:1")]
    _emit_multi_cell(trees, tmp_path, section_max_depth=2,
                     wipe_index=False, wipe_all=False)
    meta = json.loads((tmp_path / "VZW__Feb2026" / "raw" / "metadata.json").read_text())
    assert meta["name"] == "VZW__Feb2026"


def test_emit_multi_cell_same_reqid_isolated_across_cells(tmp_path):
    # The SAME req_id in two cells (release-diff case) must NOT collide —
    # each cell is its own corpus. (D-DRAFT-4 composite identity at the
    # ingest layer: cells are physically separate.)
    trees = [
        _tree_with_req("VZW", "Oct2025", "A", "req:FOO:5.1"),
        _tree_with_req("VZW", "Feb2026", "A", "req:FOO:5.1"),
    ]
    _emit_multi_cell(trees, tmp_path, section_max_depth=2,
                     wipe_index=False, wipe_all=False)
    oct_ids = {r["_id"] for r in _read_jsonl(
        tmp_path / "VZW__Oct2025" / "raw" / "corpus.jsonl")}
    feb_ids = {r["_id"] for r in _read_jsonl(
        tmp_path / "VZW__Feb2026" / "raw" / "corpus.jsonl")}
    # same req_id present in BOTH cells, independently — not deduped away
    assert "req:FOO:5.1" in oct_ids
    assert "req:FOO:5.1" in feb_ids
