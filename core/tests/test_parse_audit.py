"""Tests for the per-document parse-audit tool (parse_audit.py)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from core.src.parser.parse_audit import (
    _audit_doc,
    _score_row,
    _summarize,
    _write_csv,
    AuditRow,
)


# ---------------------------------------------------------------------------
# _score_row — confidence rubric
# ---------------------------------------------------------------------------


def test_high_confidence_for_normal_mid_depth_paragraph():
    req = {"section_number": "1.3.4", "title": "TIMER T3402", "parent_section": "1.3"}
    conf, reason = _score_row(req, seen_deep_section_before=True)
    assert conf == "HIGH"
    assert "depth 3" in reason


def test_low_confidence_for_runaway_depth():
    req = {"section_number": "1.5.1.1.3.35.1.1.3.1.2", "title": "x", "parent_section": ""}
    conf, reason = _score_row(req, seen_deep_section_before=True)
    assert conf == "LOW"
    assert "runaway" in reason


def test_low_confidence_for_depth1_after_deeper_section():
    """Depth-1 heading appearing AFTER a deeper section is the
    heading-continuation false positive — ` 13 NETWORK` sliced from
    `1.1.7 ... BAND 13 NETWORK`."""
    req = {"section_number": "13", "title": "NETWORK", "parent_section": ""}
    conf, reason = _score_row(req, seen_deep_section_before=True)
    assert conf == "LOW"
    assert "continuation" in reason


def test_medium_confidence_for_first_depth1_section():
    req = {"section_number": "1", "title": "LTE Data Retry", "parent_section": ""}
    conf, _reason = _score_row(req, seen_deep_section_before=False)
    assert conf == "MEDIUM"  # depth 1, but first one — verify it's a real chapter


def test_low_confidence_for_oversized_title():
    """Title >200 chars suggests heading classifier absorbed body text."""
    long_title = "x" * 250
    req = {"section_number": "2.1", "title": long_title, "parent_section": "2"}
    conf, reason = _score_row(req, seen_deep_section_before=False)
    assert conf == "LOW"
    assert "absorbed body" in reason


def test_medium_confidence_for_deep_but_not_runaway():
    req = {"section_number": "1.2.3.4.5.6.7", "title": "Deep but real", "parent_section": "1.2.3.4.5.6"}
    conf, reason = _score_row(req, seen_deep_section_before=True)
    assert conf == "MEDIUM"
    assert "depth 7" in reason


def test_table_anchored_with_parent_is_medium():
    req = {"section_number": "", "title": "", "parent_section": "1.4.3"}
    conf, reason = _score_row(req, seen_deep_section_before=True)
    assert conf == "MEDIUM"
    assert "table-anchored" in reason


def test_table_anchored_without_parent_is_low():
    req = {"section_number": "", "title": "", "parent_section": ""}
    conf, _reason = _score_row(req, seen_deep_section_before=True)
    assert conf == "LOW"


# ---------------------------------------------------------------------------
# _audit_doc — document-order traversal + seen_deep_section state
# ---------------------------------------------------------------------------


def test_audit_doc_propagates_seen_deep_state(tmp_path: Path):
    """Two depth-1 sections; the SECOND one (after a deeper section) is
    marked LOW for continuation. The first stays MEDIUM."""
    tree = {
        "requirements": [
            {"req_id": "REQ_A", "section_number": "1", "title": "Real Chapter", "parent_section": ""},
            {"req_id": "REQ_B", "section_number": "1.1", "title": "Sub", "parent_section": "1"},
            {"req_id": "REQ_C", "section_number": "13", "title": "NETWORK", "parent_section": ""},
        ],
    }
    p = tmp_path / "x_tree.json"
    p.write_text(json.dumps(tree))
    rows = _audit_doc(p)
    assert len(rows) == 3
    # First depth-1 → MEDIUM (no deep section seen yet)
    assert rows[0].confidence == "MEDIUM"
    # Sub-section → HIGH
    assert rows[1].confidence == "HIGH"
    # Depth-1 AFTER deep section → LOW (continuation suspect)
    assert rows[2].confidence == "LOW"
    assert "continuation" in rows[2].confidence_reason


# ---------------------------------------------------------------------------
# CSV emission
# ---------------------------------------------------------------------------


def test_csv_has_correct_columns_and_blank_correction_field(tmp_path: Path):
    rows = [
        AuditRow(
            req_id="REQ_1",
            anchor="paragraph",
            section_number="1.1",
            parent_section="1",
            depth=2,
            title="Foo",
            confidence="HIGH",
            confidence_reason="depth 2",
        ),
    ]
    out = tmp_path / "out" / "doc_audit.csv"
    _write_csv(rows, out)
    with open(out, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames
        records = list(reader)
    assert cols == [
        "req_id", "anchor", "section_number", "parent_section", "depth",
        "title", "confidence", "confidence_reason", "correct_section", "notes",
    ]
    assert records[0]["correct_section"] == ""  # blank for reviewer
    assert records[0]["notes"] == ""
    assert records[0]["confidence"] == "HIGH"


def test_summarize_counts_by_confidence():
    rows = [
        AuditRow("a", "paragraph", "1.1", "1", 2, "x", "HIGH", "ok"),
        AuditRow("b", "paragraph", "1.2", "1", 2, "y", "HIGH", "ok"),
        AuditRow("c", "paragraph", "13", "", 1, "z", "LOW", "continuation"),
        AuditRow("d", "paragraph", "1", "", 1, "w", "MEDIUM", "depth 1"),
    ]
    counts = _summarize(rows)
    assert counts == {"HIGH": 2, "MEDIUM": 1, "LOW": 1, "TOTAL": 4}
