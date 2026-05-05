"""Tests for parse_review: template generation and compact report format."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.src.parser.parse_review import generate_compact_report, generate_template


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_LOG = {
    "doc_id": "LTEOTADM",
    "source_file": "LTEOTADM.pdf",
    "mno": "VZW",
    "release": "OA-test",
    "generated_at": "2026-05-04T12:00:00+00:00",
    "dropped_blocks": [
        {"block_start": 1, "block_end": 1, "page_start": 1, "page_end": 1, "block_count": 1, "reason": "toc"},
        {"block_start": 2, "block_end": 3, "page_start": 1, "page_end": 1, "block_count": 2, "reason": "revhist"},
        {"block_start": 6, "block_end": 6, "page_start": 3, "page_end": 3, "block_count": 1, "reason": "text_strikethrough"},
    ],
    "toc": {"block_start": 1, "block_end": 1, "page_start": 1, "page_end": 1},
    "revision_history": {"block_start": 2, "block_end": 3, "page_start": 1, "page_end": 1},
    "glossary_section": {
        "section_number": "2",
        "section_title": "Definitions",
        "block_start": 7,
        "block_end": 9,
        "page_start": 4,
        "page_end": 5,
        "acronym_count": 2,
    },
    "acronyms": [
        {"acronym": "SDM", "expansion": "Subscription Data Management", "source": "table"},
        {"acronym": "IMS", "expansion": "IP Multimedia Subsystem", "source": "table"},
    ],
    "summary": {
        "toc_blocks_dropped": 1,
        "revhist_blocks_dropped": 2,
        "struck_blocks_dropped": 1,
        "cascade_blocks_dropped": 0,
        "total_dropped": 4,
        "glossary_acronyms": 2,
    },
}


def _write_log(tmp_path: Path) -> Path:
    p = tmp_path / "LTEOTADM_parse_log.json"
    p.write_text(json.dumps(SAMPLE_LOG), encoding="utf-8")
    return p


def _clean_review(tmp_path: Path, corrections: dict | None = None) -> Path:
    """Write a minimal review file with given corrections."""
    base = {
        "doc_id": "LTEOTADM",
        "reviewer": "alice",
        "review_date": "2026-05-04",
        "overall_verdict": "pass",
        "corrections": {
            "false_positive_drops": [],
            "missed_drops": [],
            "toc_error": None,
            "revhist_error": None,
            "glossary_error": None,
            "acronym_wrong_expansion": [],
            "acronym_missed": [],
            "acronym_extra": [],
        },
        "notes": "",
    }
    if corrections:
        base["corrections"].update(corrections)
    p = tmp_path / "LTEOTADM_parse_review.json"
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Template generation
# ---------------------------------------------------------------------------

def test_template_has_required_keys(tmp_path):
    log_path = _write_log(tmp_path)
    tmpl = generate_template(log_path)
    assert tmpl["doc_id"] == "LTEOTADM"
    assert "parser_snapshot" in tmpl
    assert "corrections" in tmpl
    assert tmpl["reviewer"] == ""


def test_template_snapshot_includes_toc_range(tmp_path):
    log_path = _write_log(tmp_path)
    tmpl = generate_template(log_path)
    snap = tmpl["parser_snapshot"]
    assert "p1" in snap["toc"]
    assert "1 blocks" in snap["toc"]


def test_template_snapshot_includes_glossary(tmp_path):
    log_path = _write_log(tmp_path)
    tmpl = generate_template(log_path)
    assert "section 2" in tmpl["parser_snapshot"]["glossary"]
    assert "2 acronyms" in tmpl["parser_snapshot"]["glossary"]


def test_template_snapshot_acronyms_listed(tmp_path):
    log_path = _write_log(tmp_path)
    tmpl = generate_template(log_path)
    acr_list = tmpl["parser_snapshot"]["acronyms"]
    assert any("SDM" in s for s in acr_list)
    assert any("IMS" in s for s in acr_list)


def test_template_corrections_empty_by_default(tmp_path):
    log_path = _write_log(tmp_path)
    tmpl = generate_template(log_path)
    corr = tmpl["corrections"]
    assert corr["false_positive_drops"] == []
    assert corr["missed_drops"] == []
    assert corr["toc_error"] is None
    assert corr["acronym_wrong_expansion"] == []


def test_template_struck_ranges_listed(tmp_path):
    log_path = _write_log(tmp_path)
    tmpl = generate_template(log_path)
    ranges = tmpl["parser_snapshot"]["struck_ranges"]
    assert len(ranges) == 1
    assert ranges[0]["reason"] == "text_strikethrough"
    assert ranges[0]["pages"] == "3"


def test_template_large_acronym_list_truncated(tmp_path):
    """More than 10 acronyms get a '+N more' suffix."""
    big_log = dict(SAMPLE_LOG)
    big_log["acronyms"] = [
        {"acronym": f"X{i}", "expansion": f"Expansion {i}", "source": "table"}
        for i in range(15)
    ]
    p = tmp_path / "BIG_parse_log.json"
    p.write_text(json.dumps(big_log), encoding="utf-8")
    tmpl = generate_template(p)
    acr_list = tmpl["parser_snapshot"]["acronyms"]
    assert len(acr_list) == 11  # 10 entries + "… +5 more"
    assert "+5 more" in acr_list[-1]


# ---------------------------------------------------------------------------
# Compact report — clean (no errors)
# ---------------------------------------------------------------------------

def test_report_header_contains_doc_id(tmp_path):
    log_path = _write_log(tmp_path)
    review_path = _clean_review(tmp_path)
    report = generate_compact_report(review_path, log_path=log_path)
    assert "LTEOTADM" in report
    assert "reviewer=alice" in report
    assert "verdict=pass" in report


def test_report_parser_line_present(tmp_path):
    log_path = _write_log(tmp_path)
    review_path = _clean_review(tmp_path)
    report = generate_compact_report(review_path, log_path=log_path)
    assert "toc=" in report
    assert "revhist=" in report
    assert "struck=" in report
    assert "glossary=" in report


def test_report_zero_errors_when_no_corrections(tmp_path):
    log_path = _write_log(tmp_path)
    review_path = _clean_review(tmp_path)
    report = generate_compact_report(review_path, log_path=log_path)
    assert "errors 0" in report


# ---------------------------------------------------------------------------
# Compact report — false positive drop
# ---------------------------------------------------------------------------

def test_report_fp_line(tmp_path):
    log_path = _write_log(tmp_path)
    review_path = _clean_review(tmp_path, corrections={
        "false_positive_drops": [
            {"pages": "3-3", "reason": "text_strikethrough", "note": "emphasis not deletion"}
        ]
    })
    report = generate_compact_report(review_path, log_path=log_path)
    assert "FP" in report
    assert "p3-3" in report
    assert "text_strikethrough" in report
    assert "errors 1" in report


def test_report_fp_note_truncated(tmp_path):
    log_path = _write_log(tmp_path)
    long_note = "x" * 100
    review_path = _clean_review(tmp_path, corrections={
        "false_positive_drops": [{"pages": "1", "reason": "toc", "note": long_note}]
    })
    report = generate_compact_report(review_path, log_path=log_path)
    # Note is shown as [Nch] where N <= 50
    import re
    match = re.search(r"\[(\d+)ch\]", report)
    assert match and int(match.group(1)) <= 50


# ---------------------------------------------------------------------------
# Compact report — missed drop
# ---------------------------------------------------------------------------

def test_report_md_line(tmp_path):
    log_path = _write_log(tmp_path)
    review_path = _clean_review(tmp_path, corrections={
        "missed_drops": [{"pages": "35-36", "expected_reason": "struck"}]
    })
    report = generate_compact_report(review_path, log_path=log_path)
    assert "MD" in report
    assert "p35-36" in report
    assert "want=struck" in report


# ---------------------------------------------------------------------------
# Compact report — section boundary errors
# ---------------------------------------------------------------------------

def test_report_toc_error(tmp_path):
    log_path = _write_log(tmp_path)
    review_path = _clean_review(tmp_path, corrections={
        "toc_error": {"correct_page_start": 1, "correct_page_end": 3}
    })
    report = generate_compact_report(review_path, log_path=log_path)
    assert "TOC" in report
    assert "p1-3" in report  # correct_page_end=3 shows in boundary string


def test_report_revhist_error(tmp_path):
    log_path = _write_log(tmp_path)
    review_path = _clean_review(tmp_path, corrections={
        "revhist_error": {"correct_page_start": 1, "correct_page_end": 2}
    })
    report = generate_compact_report(review_path, log_path=log_path)
    assert "RH" in report


def test_report_glossary_page_error(tmp_path):
    log_path = _write_log(tmp_path)
    review_path = _clean_review(tmp_path, corrections={
        "glossary_error": {"correct_page_end": 7}
    })
    report = generate_compact_report(review_path, log_path=log_path)
    assert "GL" in report
    assert "p4-7" in report  # correct_page_end=7 shown as p4-5→p4-7


# ---------------------------------------------------------------------------
# Compact report — acronym errors
# ---------------------------------------------------------------------------

def test_report_acronym_wrong_expansion(tmp_path):
    log_path = _write_log(tmp_path)
    review_path = _clean_review(tmp_path, corrections={
        "acronym_wrong_expansion": [
            {"acronym": "SDM", "correct": "Software-Defined Management"}
        ]
    })
    report = generate_compact_report(review_path, log_path=log_path)
    assert "AX" in report
    assert "SDM" in report
    assert "Software-Defined Management" in report
    # parser value is also included
    assert "Subscription Data Management" in report


def test_report_acronym_missing(tmp_path):
    log_path = _write_log(tmp_path)
    review_path = _clean_review(tmp_path, corrections={
        "acronym_missed": [{"acronym": "MNO", "expansion": "Mobile Network Operator"}]
    })
    report = generate_compact_report(review_path, log_path=log_path)
    assert "AM" in report
    assert "MNO" in report


def test_report_acronym_extra(tmp_path):
    log_path = _write_log(tmp_path)
    review_path = _clean_review(tmp_path, corrections={
        "acronym_extra": [{"acronym": "ABCDE", "note": "section code"}]
    })
    report = generate_compact_report(review_path, log_path=log_path)
    assert "AE" in report
    assert "ABCDE" in report


# ---------------------------------------------------------------------------
# Compact report — auto-detection of log path
# ---------------------------------------------------------------------------

def test_report_auto_detects_log(tmp_path):
    """When log_path is None but log exists in same dir as review, it's auto-loaded."""
    _write_log(tmp_path)  # LTEOTADM_parse_log.json in tmp_path
    review_path = _clean_review(tmp_path)
    # Do not pass log_path — should auto-detect
    report = generate_compact_report(review_path)
    # Parser stats should be present (from the auto-detected log)
    assert "toc=" in report
    assert "glossary=" in report


def test_report_works_without_log(tmp_path):
    """Compact report still generates even with no parse_log file available."""
    review_path = _clean_review(tmp_path)
    # No log written to tmp_path — should degrade gracefully
    report = generate_compact_report(review_path)
    assert "LTEOTADM" in report
    assert "errors 0" in report


# ---------------------------------------------------------------------------
# Multiple errors in one report
# ---------------------------------------------------------------------------

def test_report_multiple_errors_count(tmp_path):
    log_path = _write_log(tmp_path)
    review_path = _clean_review(tmp_path, corrections={
        "false_positive_drops": [{"pages": "3", "reason": "text_strikethrough"}],
        "missed_drops": [{"pages": "10-11", "expected_reason": "struck"}],
        "acronym_wrong_expansion": [{"acronym": "SDM", "correct": "Correct"}],
        "acronym_missed": [{"acronym": "MNO", "expansion": "Mobile Network Operator"}],
    })
    report = generate_compact_report(review_path, log_path=log_path)
    assert "errors 4:" in report
