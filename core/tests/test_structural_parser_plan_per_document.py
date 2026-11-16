"""One plan per document (strand history-fidelity).

Field shape: a single-plan document whose requirement ids use two prefix
variants of its plan. Each requirement took its plan from its own id, so
History showed the one document as two plan rows. With
``plan_metadata.plan_per_document`` every requirement takes the document's
plan; without it the per-id plan stays (a multi-plan document needs it).

Generic example ids only (``ABC-FOO-001``).
"""

from __future__ import annotations

from core.src.models.document import DocumentIR
from core.src.parser.structural_parser import GenericStructuralParser
from core.tests.test_structural_parser_leading_id import _body, _heading, _profile


def _parse(per_document: bool, plan: str = "PlanFoo"):
    blocks = [
        _heading(0, "1 General"),
        _heading(1, "1.1 Device"),
        _body(2, "ABC-FOO-001 The device shall do alpha."),
        _body(3, "ABC-FOOX-002 The device shall do beta."),
    ]
    for i, b in enumerate(blocks):
        b.position.index = i
    prof = _profile()
    prof.plan_metadata.plan_per_document = per_document
    ir = DocumentIR(source_file="fixture.pdf", source_format="pdf", mno="MNOX",
                    release="R2", plan=plan, content_blocks=blocks)
    return GenericStructuralParser(prof).parse(ir)


def _plans(tree) -> dict[str, str]:
    return {r.req_id: r.plan_id for r in tree.requirements if r.req_id}


def test_default_takes_plan_from_each_id():
    assert _plans(_parse(False)) == {"ABC-FOO-001": "FOO", "ABC-FOOX-002": "FOOX"}


def test_plan_per_document_takes_the_documents_plan():
    assert _plans(_parse(True)) == {"ABC-FOO-001": "PlanFoo", "ABC-FOOX-002": "PlanFoo"}


def test_plan_per_document_without_a_document_plan_keeps_id_plans():
    assert _plans(_parse(True, plan="")) == {"ABC-FOO-001": "FOO", "ABC-FOOX-002": "FOOX"}
