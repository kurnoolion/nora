"""Tests for the golden eval sample schema + persistence (FR-38).

Fixtures are synthetic — generic placeholder ids only, no proprietary
content (NFR-8).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from core.src.eval.golden import (
    STATUS_DRAFT,
    STATUS_GOLDEN_READY,
    STATUS_STAGE1_READY,
    GoldenEvalError,
    GoldenSample,
    GroundTruthEntry,
    load_sample,
    load_samples,
    next_sample_id,
    sample_path,
    save_sample,
    validate_sample,
)

_NOW = datetime(2026, 8, 5, 12, 0, 0)


def _sample(sample_id="gs-0001", status=STATUS_DRAFT, **kw) -> GoldenSample:
    defaults = dict(
        sample_id=sample_id,
        query="What are the widget retry requirements?",
        area="retry",
        created_by="expert-a",
        ground_truth=[
            GroundTruthEntry(
                req_id="REQ_FOO_0001", mno="mno-a", release="Jan2026",
                plan="PLAN_X", source="picker",
            ),
            GroundTruthEntry(req_id="REQ_FOO_0002"),
        ],
        status=status,
    )
    defaults.update(kw)
    return GoldenSample(**defaults)


# ─── Validation ─────────────────────────────────────────────────────


def test_valid_draft_sample_passes():
    assert validate_sample(_sample()) == []


def test_bad_sample_id_flagged():
    problems = validate_sample(_sample(sample_id="sample-1"))
    assert any("sample_id" in p for p in problems)


def test_empty_query_flagged():
    problems = validate_sample(_sample(query="   "))
    assert any("query is empty" in p for p in problems)


def test_unknown_status_flagged():
    problems = validate_sample(_sample(status="done"))
    assert any("status" in p for p in problems)


def test_empty_req_id_flagged():
    s = _sample(ground_truth=[GroundTruthEntry(req_id="  ")])
    assert any("empty req_id" in p for p in validate_sample(s))


def test_duplicate_entry_flagged():
    e = GroundTruthEntry(req_id="REQ_FOO_0001", mno="mno-a", release="Jan2026")
    s = _sample(ground_truth=[e, GroundTruthEntry.from_dict(e.to_dict())])
    assert any("duplicate" in p for p in validate_sample(s))


def test_same_req_id_different_cell_is_not_duplicate():
    s = _sample(ground_truth=[
        GroundTruthEntry(req_id="REQ_FOO_0001", mno="mno-a", release="Jan2026"),
        GroundTruthEntry(req_id="REQ_FOO_0001", mno="mno-a", release="Apr2026"),
    ])
    assert validate_sample(s) == []


def test_stage1_ready_requires_ground_truth():
    s = _sample(status=STATUS_STAGE1_READY, ground_truth=[])
    assert any("requires ground_truth" in p for p in validate_sample(s))


def test_golden_ready_requires_golden_response():
    s = _sample(status=STATUS_GOLDEN_READY)
    assert any("golden_response" in p for p in validate_sample(s))
    s.golden_response = "The widgets shall retry with backoff."
    assert validate_sample(s) == []


# ─── Persistence ────────────────────────────────────────────────────


def test_save_load_roundtrip(tmp_path: Path):
    s = _sample()
    path = save_sample(tmp_path, s, now=_NOW)
    assert path == sample_path(tmp_path, "gs-0001")
    loaded = load_sample(path)
    assert loaded.to_dict() == s.to_dict()
    assert loaded.created_at == "2026-08-05T12:00:00"
    assert loaded.ground_truth[0].plan == "PLAN_X"
    assert loaded.ground_truth[0].source == "picker"


def test_save_stamps_updated_but_preserves_created(tmp_path: Path):
    s = _sample()
    save_sample(tmp_path, s, now=_NOW)
    later = datetime(2026, 8, 6, 9, 30, 0)
    save_sample(tmp_path, s, now=later)
    loaded = load_sample(sample_path(tmp_path, "gs-0001"))
    assert loaded.created_at == "2026-08-05T12:00:00"
    assert loaded.updated_at == "2026-08-06T09:30:00"


def test_save_rejects_invalid(tmp_path: Path):
    with pytest.raises(GoldenEvalError) as exc:
        save_sample(tmp_path, _sample(query=""), now=_NOW)
    assert exc.value.code == "GEV-E001"
    assert not sample_path(tmp_path, "gs-0001").exists()


def test_load_samples_sorted(tmp_path: Path):
    save_sample(tmp_path, _sample(sample_id="gs-0002"), now=_NOW)
    save_sample(tmp_path, _sample(sample_id="gs-0001"), now=_NOW)
    ids = [s.sample_id for s in load_samples(tmp_path)]
    assert ids == ["gs-0001", "gs-0002"]


def test_load_samples_empty_when_missing_dir(tmp_path: Path):
    assert load_samples(tmp_path) == []


def test_load_samples_fails_loud_on_malformed(tmp_path: Path):
    save_sample(tmp_path, _sample(), now=_NOW)
    bad = sample_path(tmp_path, "gs-0002")
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(GoldenEvalError) as exc:
        load_samples(tmp_path)
    assert exc.value.code == "GEV-E001"
    assert "gs-0002" in str(exc.value)


def test_next_sample_id(tmp_path: Path):
    assert next_sample_id(tmp_path) == "gs-0001"
    save_sample(tmp_path, _sample(sample_id="gs-0001"), now=_NOW)
    save_sample(tmp_path, _sample(sample_id="gs-0007"), now=_NOW)
    assert next_sample_id(tmp_path) == "gs-0008"
