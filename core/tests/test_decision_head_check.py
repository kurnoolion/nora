"""The decision-head verification gate (strand jev-experimentation,
phase 3). Design §7 makes this an invariant: a hollow build answers
without erroring, so the check must be in code."""

from __future__ import annotations

import pytest

from core.src.decision.base import DecisionAnswer, DecisionError
from core.src.decision.head_check import (
    ADAPTER_MARKERS,
    REQUIRED_HEAD_FILES,
    assert_decision_head_present,
    confidence_is_calibrated,
    inspect_weights,
)


def _good(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "model-00001-of-00002.safetensors").write_text("x")
    (tmp_path / "head.safetensors").write_text("x")
    (tmp_path / "calibration.json").write_text("{}")
    adapter = tmp_path / "decision_adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text("{}")
    (adapter / "adapter_model.safetensors").write_text("x")
    return tmp_path


class TestAssertDecisionHeadPresent:
    def test_good_distribution_passes(self, tmp_path):
        report = assert_decision_head_present(_good(tmp_path))
        assert report["missing"] == []
        assert report["has_adapter"] is True
        assert report["gguf_only"] is False

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(DecisionError, match="not found"):
            assert_decision_head_present(tmp_path / "nope")

    def test_empty_directory_raises(self, tmp_path):
        with pytest.raises(DecisionError, match="empty"):
            assert_decision_head_present(tmp_path)

    def test_gguf_only_raises_with_the_hollow_diagnosis(self, tmp_path):
        (tmp_path / "kev-4b-q4_k_m.gguf").write_text("x")
        with pytest.raises(DecisionError, match="hollow distribution"):
            assert_decision_head_present(tmp_path)

    def test_missing_head_raises(self, tmp_path):
        d = _good(tmp_path)
        (d / "head.safetensors").unlink()
        with pytest.raises(DecisionError, match="head.safetensors"):
            assert_decision_head_present(d)

    def test_missing_calibration_raises(self, tmp_path):
        d = _good(tmp_path)
        (d / "calibration.json").unlink()
        with pytest.raises(DecisionError, match="calibration.json"):
            assert_decision_head_present(d)

    def test_missing_adapter_raises(self, tmp_path):
        d = _good(tmp_path)
        for marker in ADAPTER_MARKERS:
            for p in d.rglob(marker):
                p.unlink()
        with pytest.raises(DecisionError, match="adapter"):
            assert_decision_head_present(d)

    def test_adapter_at_top_level_also_counts(self, tmp_path):
        (tmp_path / "head.safetensors").write_text("x")
        (tmp_path / "calibration.json").write_text("{}")
        (tmp_path / "adapter_config.json").write_text("{}")
        assert assert_decision_head_present(tmp_path)["has_adapter"] is True

    def test_backbone_only_build_is_rejected(self, tmp_path):
        """The dangerous case: a plain safetensors backbone. It loads, it
        answers, and every number it produces is the backbone's."""
        (tmp_path / "config.json").write_text("{}")
        (tmp_path / "model.safetensors").write_text("x")
        with pytest.raises(DecisionError, match="not a head-bearing"):
            assert_decision_head_present(tmp_path)

    def test_report_carries_names_only(self, tmp_path):
        """Safe to log — file names, never contents."""
        report = inspect_weights(_good(tmp_path))
        assert set(report) == {
            "path", "present", "missing", "has_adapter", "gguf_only",
            "file_count",
        }
        assert sorted(report["present"]) == sorted(REQUIRED_HEAD_FILES)


class TestConfidenceIsCalibrated:
    def test_nonzero_confidence_is_calibrated(self):
        assert confidence_is_calibrated(
            [DecisionAnswer("a", "fact", 0.91)]
        ) is True

    def test_all_zero_confidence_is_not(self):
        """The shape a stripped build produces — weak evidence, so the
        caller warns rather than failing."""
        assert confidence_is_calibrated(
            [DecisionAnswer("a", "fact", 0.0), DecisionAnswer("b", "x", 0.0)]
        ) is False

    def test_empty_is_not(self):
        assert confidence_is_calibrated([]) is False
