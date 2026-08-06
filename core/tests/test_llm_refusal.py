"""Refusal detection + fallback provider (core twin of the sandbox
module). Marker strings here are invented placeholders (NFR-8) — real
markers live only in local env files.
"""

from __future__ import annotations

import pytest

from core.src.llm.refusal import (
    RefusalFallbackProvider,
    is_permanent_refusal,
    maybe_wrap_with_refusal_fallback,
    parse_markers,
)

MARKERS = parse_markers("BLOCKED_NOTICE_FOO||POLICY_NOTE_BAR")


class TestDetection:
    def test_marker_prefix_no_json_is_refusal(self):
        assert is_permanent_refusal("BLOCKED_NOTICE_FOO: cannot answer.", MARKERS)
        assert is_permanent_refusal("  POLICY_NOTE_BAR", MARKERS)

    def test_answer_with_json_payload_is_not(self):
        raw = 'BLOCKED_NOTICE_FOO mentioned, but: {"keywords": ["a"]}'
        assert not is_permanent_refusal(raw, MARKERS)

    def test_non_marker_empty_and_unconfigured(self):
        assert not is_permanent_refusal("A normal answer.", MARKERS)
        assert not is_permanent_refusal("", MARKERS)
        assert not is_permanent_refusal(None, MARKERS)
        assert not is_permanent_refusal("BLOCKED_NOTICE_FOO", ())

    def test_parse_markers_drops_blanks(self):
        assert parse_markers(" a || ||b ") == ("a", "b")
        assert parse_markers(None) == ()


class _Primary:
    model = "primary-model"

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def complete(self, prompt, system="", temperature=0.0, max_tokens=4096):
        self.calls += 1
        return self.answers.pop(0)


class _Fallback:
    model = "fallback-model"

    def __init__(self):
        self.calls = 0
        self.last_kwargs = None

    def complete(self, prompt, system="", temperature=0.0, max_tokens=4096):
        self.calls += 1
        self.last_kwargs = {"prompt": prompt, "system": system,
                            "temperature": temperature, "max_tokens": max_tokens}
        return "fallback answer"


class TestProvider:
    def test_refused_call_routes_to_fallback_once(self):
        primary = _Primary(["BLOCKED_NOTICE_FOO nope"])
        fb = _Fallback()
        p = RefusalFallbackProvider(primary, fb, MARKERS)
        out = p.complete("q?", system="sys", temperature=0.3, max_tokens=99)
        assert out == "fallback answer"
        assert (primary.calls, fb.calls, p.used) == (1, 1, 1)
        assert p.last_model == "fallback-model"  # provenance for the epilogue
        # Same prompt/params forwarded.
        assert fb.last_kwargs == {"prompt": "q?", "system": "sys",
                                  "temperature": 0.3, "max_tokens": 99}

    def test_normal_answer_never_touches_fallback(self):
        primary = _Primary(["fine answer"])
        fb = _Fallback()
        p = RefusalFallbackProvider(primary, fb, MARKERS)
        assert p.complete("q?") == "fine answer"
        assert (fb.calls, p.used) == (0, 0)
        assert p.last_model == "primary-model" and p.model == "primary-model"


class TestMaybeWrap:
    def test_unconfigured_returns_unwrapped(self, monkeypatch):
        for var in ("NORA_LLM_REFUSAL_MARKERS", "NORA_LLM_FALLBACK_BASE_URL",
                    "NORA_LLM_FALLBACK_MODEL"):
            monkeypatch.delenv(var, raising=False)
        llm = _Primary(["x"])
        assert maybe_wrap_with_refusal_fallback(llm) is llm

    def test_partial_config_warns_and_returns_unwrapped(self, monkeypatch, caplog):
        monkeypatch.setenv("NORA_LLM_REFUSAL_MARKERS", "BLOCKED_NOTICE_FOO")
        monkeypatch.delenv("NORA_LLM_FALLBACK_BASE_URL", raising=False)
        monkeypatch.delenv("NORA_LLM_FALLBACK_MODEL", raising=False)
        llm = _Primary(["x"])
        with caplog.at_level("WARNING"):
            assert maybe_wrap_with_refusal_fallback(llm) is llm
        assert "partially configured" in caplog.text

    def test_full_config_wraps(self, monkeypatch):
        monkeypatch.setenv("NORA_LLM_REFUSAL_MARKERS", "BLOCKED_NOTICE_FOO")
        monkeypatch.setenv("NORA_LLM_FALLBACK_BASE_URL", "http://127.0.0.1:1/v1")
        monkeypatch.setenv("NORA_LLM_FALLBACK_MODEL", "fallback-model")
        wrapped = maybe_wrap_with_refusal_fallback(_Primary(["x"]))
        assert isinstance(wrapped, RefusalFallbackProvider)

    def test_mock_provider_never_wrapped(self, monkeypatch):
        monkeypatch.setenv("NORA_LLM_REFUSAL_MARKERS", "BLOCKED_NOTICE_FOO")
        monkeypatch.setenv("NORA_LLM_FALLBACK_BASE_URL", "http://127.0.0.1:1/v1")
        monkeypatch.setenv("NORA_LLM_FALLBACK_MODEL", "fallback-model")
        llm = _Primary(["x"])
        llm._is_mock = True
        assert maybe_wrap_with_refusal_fallback(llm) is llm


class TestTwinSync:
    def test_detection_twins_in_sync(self):
        """The core module and sandbox/llm_refusal.py are deliberate
        copies (D-111 boundary) — fail loudly when the shared functions
        drift apart.
        """
        import inspect
        from pathlib import Path
        import core.src.llm.refusal as core_mod

        sandbox_src = (
            Path(core_mod.__file__).parents[3] / "sandbox" / "llm_refusal.py"
        ).read_text(encoding="utf-8")
        for fn in (core_mod.parse_markers, core_mod._contains_json_payload,
                   core_mod.is_permanent_refusal):
            assert inspect.getsource(fn) in sandbox_src, (
                f"{fn.__name__} drifted from sandbox/llm_refusal.py — "
                "the twin copies must change together"
            )
