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
    assert_remote_head,
    confidence_is_calibrated,
    inspect_weights,
)


def _good(tmp_path):
    """The file layout jaredpalmer/kev-4b actually ships: a LoRA adapter
    plus `head.pt`, which carries the calibration temperature inside it.
    This fixture originally wrote `head.safetensors` and
    `calibration.json` — names taken from the design doc, neither of
    which exists in a real distribution."""
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "model-00001-of-00002.safetensors").write_text("x")
    (tmp_path / "head.pt").write_text("x")
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
        (d / "head.pt").unlink()
        with pytest.raises(DecisionError, match="head.pt"):
            assert_decision_head_present(d)

    def test_missing_adapter_raises(self, tmp_path):
        d = _good(tmp_path)
        for marker in ADAPTER_MARKERS:
            for p in d.rglob(marker):
                p.unlink()
        with pytest.raises(DecisionError, match="adapter"):
            assert_decision_head_present(d)

    def test_adapter_at_top_level_also_counts(self, tmp_path):
        """Which is how the real repo ships it — adapter_config.json sits
        beside head.pt, with no adapter subfolder."""
        (tmp_path / "head.pt").write_text("x")
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


class TestAssertRemoteHead:
    """The remote half of the gate, and the one that is authoritative.

    D-DRAFT-20: a verified local head plus an unreachable server produced
    the rule-based baseline under `arm=decision` at exit 0. The weights a
    client can see are not the weights the server loaded.
    """

    @staticmethod
    def _serving(monkeypatch, payload, error=None):
        import json
        from io import BytesIO

        class _Response(BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        seen = {}

        def fake_urlopen(url, timeout=None):
            seen["url"] = url
            if error is not None:
                raise error
            return _Response(json.dumps(payload).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        return seen

    _CARD = {"models": [{
        "name": "kev-latest", "base": "Qwen/Qwen3.5-4B-Base", "lora": 16,
        "temperature": 2.41, "backend": "mlx", "dtype": "bfloat16",
    }]}

    def test_good_server_passes_and_returns_the_card(self, monkeypatch):
        self._serving(monkeypatch, self._CARD)
        card = assert_remote_head("http://host:8008")
        assert card["temperature"] == 2.41
        assert card["base"] == "Qwen/Qwen3.5-4B-Base"

    def test_probes_v1_models(self, monkeypatch):
        seen = self._serving(monkeypatch, self._CARD)
        assert_remote_head("http://host:8008")
        assert seen["url"] == "http://host:8008/v1/models"

    def test_base_url_already_carrying_v1_is_not_doubled(self, monkeypatch):
        seen = self._serving(monkeypatch, self._CARD)
        assert_remote_head("http://host:8008/v1")
        assert seen["url"] == "http://host:8008/v1/models"

    def test_unreachable_server_raises(self, monkeypatch):
        """The case that slipped through. Not a warning — nothing may be
        recorded when the server was never reached."""
        self._serving(monkeypatch, {}, error=OSError("connection refused"))
        with pytest.raises(DecisionError, match="unreachable"):
            assert_remote_head("http://host:8008")

    def test_unreachable_message_names_the_local_check_trap(self, monkeypatch):
        self._serving(monkeypatch, {}, error=OSError("connection refused"))
        with pytest.raises(DecisionError, match="D-DRAFT-20"):
            assert_remote_head("http://host:8008")

    def test_no_models_raises(self, monkeypatch):
        self._serving(monkeypatch, {"data": []})
        with pytest.raises(DecisionError, match="no models"):
            assert_remote_head("http://host:8008")

    def test_server_without_an_adapter_is_rejected(self, monkeypatch):
        """A plain backbone being served where a decider was expected. It
        answers without erroring, which is exactly the danger."""
        card = {"models": [{"name": "qwen", "lora": None, "temperature": 2.41}]}
        self._serving(monkeypatch, card)
        with pytest.raises(DecisionError, match="no adapter reported"):
            assert_remote_head("http://host:8008")

    def test_server_without_a_temperature_is_rejected(self, monkeypatch):
        card = {"models": [{"name": "kev", "lora": 16, "temperature": 0}]}
        self._serving(monkeypatch, card)
        with pytest.raises(
            DecisionError, match="no calibration temperature"
        ):
            assert_remote_head("http://host:8008")
