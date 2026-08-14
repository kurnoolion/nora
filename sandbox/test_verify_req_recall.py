"""Tests for sandbox/verify_req_recall.py — extract-vs-parse req-id diff.

All fixtures are synthetic (generic MNOA/Rel1 cell, ABC-FOO-nnn ids)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sandbox.verify_req_recall import (
    check_doc,
    load_id_config,
    main,
    scan_ir,
    scan_tree,
)

ID_CFG = {
    "pattern": r"ABC-[A-Z]+-\d{3}",
    "requirement_type_pattern": "",
    "normalize": "none",
}


def _block(btype: str, text: str, page: int = 1, index: int = 0,
           struck: bool = False, headers=None, rows=None) -> dict:
    b = {"type": btype, "text": text, "struck": struck,
         "position": {"page": page, "index": index}}
    if headers is not None:
        b["headers"] = headers
    if rows is not None:
        b["rows"] = rows
    return b


def _ir(blocks: list[dict]) -> dict:
    return {"source_file": "doc.pdf", "content_blocks": blocks}


def _tree(reqs: list[dict]) -> dict:
    return {"requirements": reqs}


# ── scan_ir bucketing ────────────────────────────────────────────────


def test_scan_ir_body_keeps_first_location():
    found = scan_ir(_ir([
        _block("heading", "1.1 Title ABC-FOO-001", page=2, index=5),
        _block("paragraph", "see ABC-FOO-001 again", page=3, index=9),
    ]), ID_CFG)
    assert found["body"] == {"ABC-FOO-001": (2, 5, "heading")}


def test_scan_ir_table_and_struck_bucketed_separately():
    found = scan_ir(_ir([
        _block("table", "", headers=["Req"], rows=[["ABC-FOO-002"]]),
        _block("paragraph", "ABC-FOO-003", struck=True),
    ]), ID_CFG)
    assert found["table"] == {"ABC-FOO-002"}
    assert found["struck"] == {"ABC-FOO-003"}
    assert found["body"] == {}


def test_scan_ir_type_pattern_filters_section_ids():
    cfg = dict(ID_CFG, requirement_type_pattern=r"ABC-FOO-\d{3}")
    found = scan_ir(_ir([
        _block("paragraph", "ABC-FOO-001 and ABC-SEC-002"),
    ]), cfg)
    assert found["body"] == {"ABC-FOO-001": (1, 0, "paragraph")}
    assert found["section_id"] == {"ABC-SEC-002"}


def test_scan_ir_normalize_upper():
    cfg = {"pattern": r"(?i)abc-foo-\d{3}", "normalize": "upper"}
    found = scan_ir(_ir([_block("paragraph", "abc-foo-001")]), cfg)
    assert set(found["body"]) == {"ABC-FOO-001"}


# ── scan_tree semantics ──────────────────────────────────────────────


def test_scan_tree_backcompat_and_demotion():
    all_ids, req_ids = scan_tree(_tree([
        {"req_id": "ABC-FOO-001"},                            # no key → req
        {"req_id": "ABC-FOO-002", "is_requirement": True},
        {"req_id": "ABC-SEC-003", "is_requirement": False},   # demoted
        {"req_id": "", "is_requirement": True},               # id-less ignored
    ]))
    assert all_ids == {"ABC-FOO-001", "ABC-FOO-002", "ABC-SEC-003"}
    assert req_ids == {"ABC-FOO-001", "ABC-FOO-002"}


# ── check_doc end to end ─────────────────────────────────────────────


def _write_doc(tmp_path: Path, ir: dict, tree: dict | None):
    ir_path = tmp_path / "doc_ir.json"
    ir_path.write_text(json.dumps(ir))
    tree_path = tmp_path / "doc_tree.json"
    if tree is not None:
        tree_path.write_text(json.dumps(tree))
    return ir_path, tree_path


def test_check_doc_missing_and_buckets(tmp_path):
    ir = _ir([
        _block("heading", "ABC-FOO-001", index=0),
        _block("paragraph", "ABC-FOO-002 body", index=1),   # missing from tree
        _block("table", "", rows=[["ABC-FOO-004"]]),        # table-only
        _block("paragraph", "ABC-FOO-005", struck=True),    # struck-only
    ])
    tree = _tree([{"req_id": "ABC-FOO-001", "is_requirement": True}])
    ir_path, tree_path = _write_doc(tmp_path, ir, tree)
    res = check_doc(ir_path, tree_path, ID_CFG)
    assert not res["unparsed"]
    assert set(res["missing"]) == {"ABC-FOO-002"}
    assert res["missing"]["ABC-FOO-002"] == (1, 1, "paragraph")
    assert res["table_only"] == {"ABC-FOO-004"}
    assert res["struck_only"] == {"ABC-FOO-005"}
    assert res["demoted"] == set()


def test_check_doc_demoted_not_missing(tmp_path):
    ir = _ir([_block("heading", "ABC-FOO-001")])
    tree = _tree([{"req_id": "ABC-FOO-001", "is_requirement": False}])
    ir_path, tree_path = _write_doc(tmp_path, ir, tree)
    res = check_doc(ir_path, tree_path, ID_CFG)
    assert res["missing"] == {}
    assert res["demoted"] == {"ABC-FOO-001"}


def test_check_doc_table_id_also_in_tree_not_reported(tmp_path):
    # A table-anchored req: id lives in a table cell AND in the tree.
    ir = _ir([_block("table", "", rows=[["ABC-FOO-007"]])])
    tree = _tree([{"req_id": "ABC-FOO-007", "is_requirement": True}])
    ir_path, tree_path = _write_doc(tmp_path, ir, tree)
    res = check_doc(ir_path, tree_path, ID_CFG)
    assert res["missing"] == {} and res["table_only"] == set()


def test_check_doc_unparsed(tmp_path):
    ir = _ir([_block("paragraph", "ABC-FOO-001")])
    ir_path, tree_path = _write_doc(tmp_path, ir, None)
    res = check_doc(ir_path, tree_path, ID_CFG)
    assert res["unparsed"] is True
    assert set(res["found"]["body"]) == {"ABC-FOO-001"}


# ── main() over a synthetic env layout ───────────────────────────────


def _env(tmp_path: Path) -> tuple[Path, Path]:
    cell_e = tmp_path / "out" / "extract" / "MNOA" / "Rel1"
    cell_p = tmp_path / "out" / "parse" / "MNOA" / "Rel1"
    cell_prof = tmp_path / "out" / "profile" / "MNOA" / "Rel1"
    for d in (cell_e, cell_p, cell_prof):
        d.mkdir(parents=True)
    (cell_prof / "profile.json").write_text(json.dumps(
        {"requirement_id": dict(ID_CFG)}))
    (cell_e / "doc_ir.json").write_text(json.dumps(_ir([
        _block("heading", "ABC-FOO-001"),
        _block("paragraph", "ABC-FOO-002"),
    ])))
    (cell_p / "doc_tree.json").write_text(json.dumps(_tree(
        [{"req_id": "ABC-FOO-001"}])))
    return cell_e.parent.parent, cell_p.parent.parent


def test_main_reports_and_strict_exit(tmp_path, capsys):
    extract, parse = _env(tmp_path)
    rc = main(["--extract", str(extract), "--parse", str(parse)])
    out = capsys.readouterr().out
    assert rc == 0                      # informational by default
    assert "MISSING=1" in out and "ABC-FOO-002" in out
    rc = main(["--extract", str(extract), "--parse", str(parse), "--strict"])
    capsys.readouterr()
    assert rc == 1


def test_main_skips_cell_without_profile(tmp_path, capsys):
    extract, parse = _env(tmp_path)
    (tmp_path / "out" / "profile" / "MNOA" / "Rel1" / "profile.json").unlink()
    rc = main(["--extract", str(extract), "--parse", str(parse), "--strict"])
    out = capsys.readouterr().out
    assert rc == 0 and "[skip]" in out


def test_load_id_config_empty_pattern_unusable(tmp_path):
    p = tmp_path / "profile.json"
    p.write_text(json.dumps({"requirement_id": {"pattern": ""}}))
    assert load_id_config(p) == {}
    assert load_id_config(tmp_path / "absent.json") == {}
