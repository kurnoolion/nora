"""The three-valued NORA_QUERY_ANALYZER knob and the phase-2 identity
gate (strand jev-experimentation)."""

from __future__ import annotations

import pytest

from core.src.env.config import (
    DEFAULT_QUERY_ANALYZER,
    QUERY_ANALYZER_ENV_VAR,
    QUERY_ANALYZERS,
    resolve_decision_provider,
    resolve_query_analyzer,
)


class TestResolveQueryAnalyzer:
    def test_default_is_rules(self, monkeypatch):
        """Legacy is the default at every phase (design §7) — an
        operator who sets nothing sees today's behaviour."""
        monkeypatch.delenv(QUERY_ANALYZER_ENV_VAR, raising=False)
        assert resolve_query_analyzer() == "rules"
        assert DEFAULT_QUERY_ANALYZER == "rules"

    def test_three_values_exactly(self):
        assert QUERY_ANALYZERS == ("rules", "llm", "decision")

    def test_env_var_selects(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        assert resolve_query_analyzer() == "decision"

    def test_cli_beats_env(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        assert resolve_query_analyzer(cli_value="llm") == "llm"

    def test_config_store_beats_env(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "rules")
        assert resolve_query_analyzer(config_store_value="llm") == "llm"

    def test_unknown_value_raises(self, monkeypatch):
        monkeypatch.delenv(QUERY_ANALYZER_ENV_VAR, raising=False)
        with pytest.raises(ValueError, match="nonsense"):
            resolve_query_analyzer(cli_value="nonsense")

    def test_unknown_env_value_raises(self, monkeypatch):
        """A typo in a shell export must fail loudly, not silently
        serve the default."""
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decisions")
        with pytest.raises(ValueError, match="decisions"):
            resolve_query_analyzer()

    def test_empty_env_value_falls_through(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "")
        assert resolve_query_analyzer() == "rules"


class TestResolveDecisionProvider:
    def test_default_is_mock(self, monkeypatch):
        """So the `decision` knob value is exercisable on a laptop with
        nothing installed."""
        monkeypatch.delenv("NORA_DECISION_PROVIDER", raising=False)
        assert resolve_decision_provider() == "mock"

    def test_env_var_selects(self, monkeypatch):
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "systemone")
        assert resolve_decision_provider() == "systemone"

    def test_unknown_value_raises(self, monkeypatch):
        monkeypatch.delenv("NORA_DECISION_PROVIDER", raising=False)
        with pytest.raises(ValueError, match="jev"):
            resolve_decision_provider(cli_value="jev")
