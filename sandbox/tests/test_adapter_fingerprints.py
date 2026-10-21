"""The adapter writes one fingerprint per requirement row, beside corpus.jsonl."""

import json

from core.src.models.fingerprint import req_fingerprint
from sandbox.adapter.nora_to_beir import FINGERPRINTS_NAME, _emit_corpus


def _tree(reqs):
    return {"mno": "MNOA", "release": "Jan2031", "plan_id": "PLANA",
            "requirements": reqs}


def test_sidecar_has_one_fingerprint_per_requirement_row(tmp_path):
    reqs = [
        {"req_id": "REQ-A-1", "section_number": "1.1", "title": "Retry",
         "text": "Retry after T1.", "is_requirement": True},
        {"req_id": "REQ-A-2", "section_number": "1.2", "title": "Backoff",
         "text": "Back off.", "is_requirement": True},
        {"req_id": "", "section_number": "1", "title": "Section only"},
    ]
    out = tmp_path / "raw" / "corpus.jsonl"
    _emit_corpus([_tree(reqs)], out)
    rows = [json.loads(line) for line in (out.parent / FINGERPRINTS_NAME).read_text().splitlines()]
    assert [r["_id"] for r in rows] == ["REQ-A-1", "REQ-A-2"]
    assert rows[0]["fingerprint"] == req_fingerprint("Retry", "Retry after T1.")


def test_fingerprint_ignores_section_number(tmp_path):
    a = [{"req_id": "R1", "section_number": "1.1", "title": "T", "text": "x",
          "is_requirement": True}]
    b = [{"req_id": "R1", "section_number": "9.9", "title": "T", "text": "x",
          "is_requirement": True}]
    _emit_corpus([_tree(a)], tmp_path / "a" / "corpus.jsonl")
    _emit_corpus([_tree(b)], tmp_path / "b" / "corpus.jsonl")
    fa = (tmp_path / "a" / FINGERPRINTS_NAME).read_text()
    fb = (tmp_path / "b" / FINGERPRINTS_NAME).read_text()
    assert fa == fb
