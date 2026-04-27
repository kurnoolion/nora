"""Tests for regex patterns used across extraction, profiling, and parsing.

These are the patterns most likely to hit edge cases with real document text.
"""

import re

import pytest


class TestSectionNumbering:
    """The numbering pattern from the profile: ^(\\d+\\.)+\\d*\\s"""

    PATTERN = re.compile(r"^(\d+\.)+\d*\s")

    @pytest.mark.parametrize("text,expected_num", [
        ("1.1 INTRODUCTION", "1.1"),
        ("1.3.1 Software Algorithm", "1.3.1"),
        ("1.3.1.3.16 eUICC", "1.3.1.3.16"),
        ("1.3.1.3.16.5 Testability", "1.3.1.3.16.5"),
    ])
    def test_matches_valid_section_numbers(self, text, expected_num):
        m = self.PATTERN.match(text)
        assert m is not None
        assert m.group(0).strip().rstrip(".") == expected_num

    @pytest.mark.parametrize("text", [
        "This is body text",
        "3GPP TS 24.301 Section 5.1",
        "VZ_REQ_LTEDATARETRY_2365",
        "",
        "Release 10",
    ])
    def test_rejects_non_section_text(self, text):
        assert self.PATTERN.match(text) is None

    def test_depth_calculation(self):
        """Depth = number of dots + 1 in the section number."""
        cases = [
            ("1.1 Intro", 2),
            ("1.1.1 Sub", 3),
            ("1.3.1.3.16 Deep", 5),
            ("1.3.1.3.16.5 Deepest", 6),
        ]
        for text, expected_depth in cases:
            m = self.PATTERN.match(text)
            num = m.group(0).strip().rstrip(".")
            depth = num.count(".") + 1
            assert depth == expected_depth, f"{text}: expected depth {expected_depth}, got {depth}"


class TestRequirementIdPattern:
    """The VZ_REQ pattern from the profile."""

    PATTERN = re.compile(r"VZ_REQ_[A-Z0-9_]+_\d+")

    @pytest.mark.parametrize("text,expected_id", [
        ("VZ_REQ_LTEDATARETRY_2365", "VZ_REQ_LTEDATARETRY_2365"),
        ("VZ_REQ_LTEB13NAC_11581500", "VZ_REQ_LTEB13NAC_11581500"),
        ("VZ_REQ_LTESMS_30278", "VZ_REQ_LTESMS_30278"),
        ("See VZ_REQ_LTEAT_100 for details", "VZ_REQ_LTEAT_100"),
    ])
    def test_matches_valid_req_ids(self, text, expected_id):
        ids = self.PATTERN.findall(text)
        assert expected_id in ids

    def test_extracts_multiple_ids(self):
        text = "Refs: VZ_REQ_LTESMS_100, VZ_REQ_LTEDATARETRY_200"
        ids = self.PATTERN.findall(text)
        assert len(ids) == 2

    @pytest.mark.parametrize("text", [
        "Some random text",
        "REQ_LTESMS_100",
        "VZ_LTESMS_100",
    ])
    def test_rejects_non_matching(self, text):
        assert self.PATTERN.findall(text) == []


class TestPlanIdExtraction:
    """Extract plan ID from requirement ID using profile components config."""

    def _extract_plan_id(self, req_id: str, separator: str = "_", plan_pos: int = 2) -> str | None:
        parts = req_id.split(separator)
        if plan_pos < len(parts):
            return parts[plan_pos]
        return None

    @pytest.mark.parametrize("req_id,expected_plan", [
        ("VZ_REQ_LTEDATARETRY_2365", "LTEDATARETRY"),
        ("VZ_REQ_LTEB13NAC_11581500", "LTEB13NAC"),
        ("VZ_REQ_LTESMS_30278", "LTESMS"),
        ("VZ_REQ_LTEAT_100", "LTEAT"),
    ])
    def test_extracts_plan_id(self, req_id, expected_plan):
        assert self._extract_plan_id(req_id) == expected_plan

    def test_returns_none_for_short_id(self):
        assert self._extract_plan_id("VZ_REQ") is None


class TestSpecNumberRegex:
    """3GPP spec number regex — must not capture trailing punctuation dots."""

    SPEC_RE = re.compile(r"3GPP\s+TS\s+(\d[\d.]*\d)")

    @pytest.mark.parametrize("text,expected_spec", [
        ("3GPP TS 24.301 Section 5.1", "24.301"),
        ("3GPP TS 36.331 Release 11", "36.331"),
        ("3GPP TS 23.003", "23.003"),
        ("per 3GPP TS 31.101, the device", "31.101"),
    ])
    def test_captures_spec_number(self, text, expected_spec):
        m = self.SPEC_RE.search(text)
        assert m is not None
        assert m.group(1) == expected_spec

    def test_no_trailing_dot_at_sentence_end(self):
        """The key bug we fixed — 'TS 24.301.' should capture '24.301' not '24.301.'"""
        text = "See 3GPP TS 24.301."
        m = self.SPEC_RE.search(text)
        assert m is not None
        assert m.group(1) == "24.301"

    def test_no_trailing_dot_before_comma(self):
        text = "3GPP TS 24.301, 3GPP TS 36.331."
        matches = self.SPEC_RE.findall(text)
        assert matches == ["24.301", "36.331"]

    def test_spec_with_release(self):
        full_re = re.compile(r"3GPP\s+TS\s+(\d[\d.]*\d).*?[Rr]elease\s+(\d+)")
        m = full_re.search("3GPP TS 24.301 Release 10")
        assert m is not None
        assert m.group(1) == "24.301"
        assert m.group(2) == "10"


class TestHeaderFooterPatterns:
    PAGE_RE = re.compile(r"^\s*Page\s+\d+\s+of\s+\d+\s*$", re.IGNORECASE)
    CONFIDENTIAL_RE = re.compile(
        r"(Official Use Only|Proprietary.*Confidential|Non-Disclosure)", re.IGNORECASE
    )

    @pytest.mark.parametrize("text", [
        "Page 1 of 136",
        "  Page 10 of 200  ",
        "page 3 of 50",
    ])
    def test_page_number_matches(self, text):
        assert self.PAGE_RE.match(text) is not None

    @pytest.mark.parametrize("text", [
        "See Page 5 of this document",
        "Page 1",
        "1 of 136",
    ])
    def test_page_number_rejects(self, text):
        assert self.PAGE_RE.match(text) is None

    @pytest.mark.parametrize("text", [
        "Official Use Only",
        "Proprietary and Confidential",
        "Non-Disclosure Agreement",
    ])
    def test_confidential_matches(self, text):
        assert self.CONFIDENTIAL_RE.search(text) is not None
