"""Attachment captions in leading-id mode (strand history-fidelity).

Field shape: after a requirement's definition, a body paragraph holds the
same id followed by a diagram's file name. It opened a second node for that
id; the stores keep the last occurrence, so History and search served the
caption in place of the definition. A block holding one id and one file name
now continues the open requirement.

Generic example ids only (``ABC-FOO-001``).
"""

from __future__ import annotations

from core.src.models.document import DocumentIR
from core.src.parser.structural_parser import GenericStructuralParser
from core.tests.test_structural_parser_leading_id import _body, _heading, _profile


def _parse(*bodies: str):
    blocks = [_heading(0, "1 General"), _heading(1, "1.1 Device")]
    blocks += [_body(i + 2, t) for i, t in enumerate(bodies)]
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="fixture.pdf", source_format="pdf", mno="MNOX",
                    release="R2", content_blocks=blocks)
    return GenericStructuralParser(_profile()).parse(ir)


def _nodes(tree, rid):
    return [r for r in tree.requirements if r.req_id == rid]


def test_id_with_file_name_continues_the_requirement():
    tree = _parse("ABC-FOO-001 The device shall support the call flow shown.",
                  "ABC-FOO-001 diagram.pdf")
    nodes = _nodes(tree, "ABC-FOO-001")
    assert len(nodes) == 1
    assert "shall support the call flow" in nodes[0].text
    assert "diagram.pdf" in nodes[0].text
    assert tree.parse_stats.file_ref_blocks_continued == 1
    assert tree.parse_stats.duplicate_req_ids == 0


def test_statement_mentioning_a_file_still_opens_a_requirement():
    tree = _parse("ABC-FOO-001 The device shall do alpha.",
                  "ABC-FOO-002 The device shall log to trace.txt")
    assert len(_nodes(tree, "ABC-FOO-002")) == 1
    assert tree.parse_stats.file_ref_blocks_continued == 0


def test_unknown_extension_or_two_tokens_is_not_a_file_reference():
    tree = _parse("ABC-FOO-001 The device shall do alpha.",
                  "ABC-FOO-002 Version 1.2",
                  "ABC-FOO-003 call flow.pdf")
    assert len(_nodes(tree, "ABC-FOO-002")) == 1
    assert len(_nodes(tree, "ABC-FOO-003")) == 1
    assert tree.parse_stats.file_ref_blocks_continued == 0
