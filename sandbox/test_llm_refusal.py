"""Permanent-refusal detection (sandbox/llm_refusal.py). Markers here
are invented test strings — real marker values are deployment-local
env config and never appear in the repo."""

from __future__ import annotations

import json

from sandbox.llm_refusal import (
    is_permanent_refusal,
    parse_markers,
)

MARKERS = ("CANNOT_COMPLY", "Request declined:")


class TestParseMarkers:
    def test_empty_and_none(self):
        assert parse_markers("") == ()
        assert parse_markers(None) == ()

    def test_single_marker(self):
        assert parse_markers("CANNOT_COMPLY") == ("CANNOT_COMPLY",)

    def test_multiple_double_pipe_separated(self):
        got = parse_markers("CANNOT_COMPLY || Request declined: ||")
        assert got == ("CANNOT_COMPLY", "Request declined:")

    def test_marker_may_contain_commas(self):
        got = parse_markers("Sorry, that request, as given")
        assert got == ("Sorry, that request, as given",)


class TestIsPermanentRefusal:
    def test_marker_prefix_no_payload_is_refusal(self):
        assert is_permanent_refusal("CANNOT_COMPLY with this input.", MARKERS)
        assert is_permanent_refusal("  Request declined: policy.", MARKERS)

    def test_exact_marker_is_refusal(self):
        assert is_permanent_refusal("CANNOT_COMPLY", MARKERS)

    def test_no_marker_is_not_refusal(self):
        assert not is_permanent_refusal("thinking forever, no answer", MARKERS)

    def test_empty_response_is_not_refusal(self):
        # empty = transient endpoint trouble; the retry path handles it
        assert not is_permanent_refusal("", MARKERS)
        assert not is_permanent_refusal(None, MARKERS)

    def test_no_markers_configured_disables_detection(self):
        assert not is_permanent_refusal("CANNOT_COMPLY", ())

    def test_json_payload_defuses_marker_prefix(self):
        # a genuine answer that merely quotes a marker keeps its payload
        raw = 'CANNOT_COMPLY was mentioned...\n' + json.dumps({"R1": ["a"]})
        assert not is_permanent_refusal(raw, MARKERS)

    def test_json_array_payload_counts(self):
        raw = 'Request declined: just kidding ["kw one", "kw two"]'
        assert not is_permanent_refusal(raw, MARKERS)

    def test_fenced_payload_counts(self):
        raw = 'CANNOT_COMPLY...\n```json\n{"R1": ["a"]}\n```'
        assert not is_permanent_refusal(raw, MARKERS)

    def test_unparseable_braces_still_refusal(self):
        assert is_permanent_refusal("CANNOT_COMPLY {not json", MARKERS)
