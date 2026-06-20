"""Profile-driven section exclusion (D-DRAFT-13).

`exclude_section_pattern` drops sections whose title matches (plus descendants)
from the parsed tree — REFERENCES, traceability matrices, etc. Runs after
reference-list extraction so a REFERENCES section still populates
`reference_list_map` before being dropped from chunks.
"""

from __future__ import annotations

from core.src.parser.structural_parser import GenericStructuralParser, Requirement
from core.src.profiler.profile_schema import DocumentProfile


def _parser(pattern: str) -> GenericStructuralParser:
    return GenericStructuralParser(
        DocumentProfile(profile_name="t", exclude_section_pattern=pattern)
    )


def _sec(section_number: str, title: str, parent_section: str = "", req_id: str = "") -> Requirement:
    return Requirement(req_id=req_id, section_number=section_number, title=title,
                       parent_section=parent_section)


class TestDropExcludedSections:
    def test_drops_references_section_and_descendants(self):
        p = _parser(r"(?i)^\s*references\b")
        sections = [
            _sec("4", "Functional Requirements"),
            _sec("4.1", "Band Support", parent_section="4", req_id="R-1"),
            _sec("9", "REFERENCES", req_id="R-REF"),
            _sec("9.1", "3GPP refs", parent_section="9"),
            # a table-anchored child of the references section (no section_number)
            _sec("", "ref row", parent_section="9", req_id="R-REFROW"),
        ]
        kept = p._drop_excluded_sections(sections)
        nums = {r.section_number for r in kept}
        assert nums == {"4", "4.1"}
        assert all("REFERENCES" not in (r.title or "") for r in kept)
        assert not any(r.parent_section == "9" for r in kept)  # descendants gone

    def test_matches_substituted_traceability_literal(self):
        # mirrors post-substitution pattern: references OR an escaped literal
        p = _parser(r"(?i)^\s*references\b|Req\-to\-TC\ Traceability\ Matrix")
        sections = [
            _sec("4", "Functional Requirements"),
            _sec("7", "Req-to-TC Traceability Matrix"),
            _sec("7.1", "matrix body", parent_section="7"),
        ]
        kept = p._drop_excluded_sections(sections)
        assert {r.section_number for r in kept} == {"4"}

    def test_no_match_keeps_everything(self):
        p = _parser(r"(?i)^\s*references\b")
        sections = [_sec("4", "Functional Requirements"),
                    _sec("4.1", "References to 3GPP in body", parent_section="4")]
        # "References to 3GPP in body" is NOT anchored-^references-word? it IS:
        # "^\s*references\b" matches "References to ..." → dropped. Use a clearly
        # non-matching title to assert the keep path.
        sections = [_sec("4", "Functional Requirements"),
                    _sec("4.1", "Band Support", parent_section="4")]
        assert p._drop_excluded_sections(sections) == sections

    def test_empty_pattern_is_noop(self):
        p = _parser("")
        sections = [_sec("9", "REFERENCES")]
        assert p._drop_excluded_sections(sections) == sections

    def test_bad_pattern_is_noop_not_crash(self):
        p = _parser("(unclosed")
        sections = [_sec("9", "REFERENCES")]
        assert p._drop_excluded_sections(sections) == sections


class TestProfileFieldRoundTrip:
    def test_load_and_default(self, tmp_path):
        assert DocumentProfile(profile_name="t").exclude_section_pattern == ""
        prof = DocumentProfile(profile_name="t", exclude_section_pattern="(?i)^refs$")
        prof.save_json(tmp_path / "p.json")
        assert DocumentProfile.load_json(tmp_path / "p.json").exclude_section_pattern == "(?i)^refs$"

    def test_substitution_resolves_placeholder(self):
        from core.src.profiler.profile_substitute import substitute_placeholders
        prof = DocumentProfile(profile_name="t",
                               exclude_section_pattern=r"(?i)^references\b|<TRACEABILITY>")
        out = substitute_placeholders(prof, {"TRACEABILITY": "TC Matrix"})
        assert out.exclude_section_pattern == r"(?i)^references\b|TC\ Matrix"
