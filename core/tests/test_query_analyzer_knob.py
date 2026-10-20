"""The four-valued NORA_QUERY_ANALYZER knob and the phase-2 identity
gate (strand jev-experimentation)."""

from __future__ import annotations

import pytest

from core.src.env.config import (
    DECISION_CONFIDENCE_FLOOR_ENV_VAR,
    DEFAULT_DECISION_CONFIDENCE_FLOOR,
    DEFAULT_QUERY_ANALYZER,
    QUERY_ANALYZER_ENV_VAR,
    QUERY_ANALYZERS,
    resolve_decision_confidence_floor,
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

    def test_four_values_exactly(self):
        """`confidence-routed` is spelled the same here, as `--arm`, and
        as the class it builds. One spelling was the point of naming it
        (D-DRAFT-25)."""
        assert QUERY_ANALYZERS == (
            "rules", "llm", "decision", "confidence-routed"
        )

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


from core.src.decision.analyzer import (
    ConfidenceRoutedQueryAnalyzer,
    DecisionQueryAnalyzer,
)
from core.src.decision.mock_provider import MockDecisionProvider
from core.src.query.analyzer import (
    LLMQueryAnalyzer,
    MockQueryAnalyzer,
    make_query_analyzer,
    make_query_analyzer_from_knob,
)
from core.src.query.schema import QueryType


class _StubLLM:
    model = "stub"

    def complete(self, prompt, system=None, temperature=0.0, **kwargs):
        return ""


class TestMakeQueryAnalyzerArgumentDispatch:
    """The existing factory's behaviour must not move — every current
    caller passes a single positional argument."""

    def test_no_arguments_is_still_rules(self):
        assert isinstance(make_query_analyzer(), MockQueryAnalyzer)

    def test_none_is_still_rules(self):
        assert isinstance(make_query_analyzer(None), MockQueryAnalyzer)

    def test_llm_provider_is_still_llm(self):
        assert isinstance(make_query_analyzer(_StubLLM()), LLMQueryAnalyzer)

    def test_decision_provider_selects_the_decision_analyzer(self):
        got = make_query_analyzer(decision_provider=MockDecisionProvider())
        assert isinstance(got, DecisionQueryAnalyzer)

    def test_decision_provider_wins_over_llm(self):
        got = make_query_analyzer(
            _StubLLM(), decision_provider=MockDecisionProvider()
        )
        assert isinstance(got, DecisionQueryAnalyzer)


class TestIdentityGate:
    """With the knob unset, classification is byte-identical to today."""

    def test_knob_unset_builds_the_rule_analyzer(self, monkeypatch):
        monkeypatch.delenv(QUERY_ANALYZER_ENV_VAR, raising=False)
        assert isinstance(make_query_analyzer_from_knob(), MockQueryAnalyzer)

    def test_knob_unset_classifies_identically_to_today(self, monkeypatch):
        """The gate proper: same verdict on every sample of the phase-1
        labelled set, compared against MockQueryAnalyzer directly."""
        from core.src.eval.classify import load_classify_set

        monkeypatch.delenv(QUERY_ANALYZER_ENV_VAR, raising=False)
        legacy = MockQueryAnalyzer()
        knobbed = make_query_analyzer_from_knob()
        for sample in load_classify_set():
            assert (
                knobbed.analyze(sample.query) == legacy.analyze(sample.query)
            ), sample.sample_id

    def test_pipeline_default_analyzer_comes_from_the_knob(self, monkeypatch):
        """pipeline.py's own default must come out of the knob and land
        on today's behaviour. Asserted on the source because
        QueryPipeline.__init__ needs a graph, embedder and store to
        construct — core/tests/test_pipeline.py has those fixtures and is
        the better home if this proves annoying."""
        import inspect

        monkeypatch.delenv(QUERY_ANALYZER_ENV_VAR, raising=False)
        from core.src.query.pipeline import QueryPipeline

        source = inspect.getsource(QueryPipeline.__init__)
        assert "make_query_analyzer_from_knob" in source


class TestKnobSelectsEachArm:
    def test_rules(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "rules")
        assert isinstance(make_query_analyzer_from_knob(), MockQueryAnalyzer)

    def test_llm_with_a_supplied_provider(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "llm")
        got = make_query_analyzer_from_knob(llm_provider=_StubLLM())
        assert isinstance(got, LLMQueryAnalyzer)

    def test_llm_without_a_provider_degrades_to_rules(self, monkeypatch):
        """The sira lane has no LLMProvider wired yet (service.py's own
        recorded follow-up). Selecting `llm` there must degrade, not
        crash the query path."""
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "llm")
        assert isinstance(make_query_analyzer_from_knob(), MockQueryAnalyzer)

    def test_decision_defaults_to_the_mock_provider(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        monkeypatch.delenv("NORA_DECISION_PROVIDER", raising=False)
        got = make_query_analyzer_from_knob()
        assert isinstance(got, DecisionQueryAnalyzer)

    def test_decision_with_a_supplied_provider(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        provider = MockDecisionProvider()
        got = make_query_analyzer_from_knob(decision_provider=provider)
        assert isinstance(got, DecisionQueryAnalyzer)

    def test_decision_systemone_without_a_provider_degrades_to_rules(
        self, monkeypatch
    ):
        """Phase 2 ships no HTTP provider. Asking for one must degrade
        rather than import something that does not exist."""
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "systemone")
        assert isinstance(make_query_analyzer_from_knob(), MockQueryAnalyzer)

    def test_decision_arm_actually_classifies(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        monkeypatch.delenv("NORA_DECISION_PROVIDER", raising=False)
        got = make_query_analyzer_from_knob()
        intent = got.analyze("please summarize the data retry spec")
        assert intent.query_type is QueryType.SUMMARIZE


class TestResolveDecisionConfidenceFloor:
    """The routing threshold for `NORA_QUERY_ANALYZER=confidence-routed`.

    Default 0.50 because that is the value measured in
    phase-3-measurement.md — with the caveat that it was selected on the
    same 50 samples it scored on, so it is a starting point, not a
    validated setting. The resolver logs that caveat the first time it
    hands back the default.
    """

    def test_default_is_the_measured_floor(self, monkeypatch):
        monkeypatch.delenv(DECISION_CONFIDENCE_FLOOR_ENV_VAR, raising=False)
        assert resolve_decision_confidence_floor() == 0.50
        assert DEFAULT_DECISION_CONFIDENCE_FLOOR == 0.50

    def test_env_var_selects(self, monkeypatch):
        monkeypatch.setenv(DECISION_CONFIDENCE_FLOOR_ENV_VAR, "0.35")
        assert resolve_decision_confidence_floor() == 0.35

    def test_cli_beats_env(self, monkeypatch):
        monkeypatch.setenv(DECISION_CONFIDENCE_FLOOR_ENV_VAR, "0.35")
        assert resolve_decision_confidence_floor(cli_value=0.7) == 0.7

    def test_zero_from_the_cli_is_honoured(self, monkeypatch):
        """0.0 is a meaningful floor — it means "take every answer", the
        unrouted behaviour — so it must not be swallowed as falsy."""
        monkeypatch.setenv(DECISION_CONFIDENCE_FLOOR_ENV_VAR, "0.35")
        assert resolve_decision_confidence_floor(cli_value=0.0) == 0.0

    def test_zero_from_the_env_is_honoured(self, monkeypatch):
        monkeypatch.setenv(DECISION_CONFIDENCE_FLOOR_ENV_VAR, "0")
        assert resolve_decision_confidence_floor() == 0.0

    def test_unparseable_value_raises(self, monkeypatch):
        """A floor is the one number a routed result cannot be read
        without, so a typo must not silently become 0.50 and get
        reported as though it were chosen."""
        monkeypatch.setenv(DECISION_CONFIDENCE_FLOOR_ENV_VAR, "half")
        with pytest.raises(ValueError, match="half"):
            resolve_decision_confidence_floor()

    @pytest.mark.parametrize("bad", ["-0.1", "1.5"])
    def test_out_of_range_raises(self, monkeypatch, bad):
        """Calibrated confidence is a probability. A floor above 1.0
        routes everything to the rules while still reporting the arm as
        routed, which is the silent-degradation shape design §7 is
        about."""
        monkeypatch.setenv(DECISION_CONFIDENCE_FLOOR_ENV_VAR, bad)
        with pytest.raises(ValueError, match="between 0.0 and 1.0"):
            resolve_decision_confidence_floor()

    def test_one_is_allowed(self, monkeypatch):
        """Degenerate but legal: only answers the model reports as
        certain are kept."""
        monkeypatch.setenv(DECISION_CONFIDENCE_FLOOR_ENV_VAR, "1.0")
        assert resolve_decision_confidence_floor() == 1.0


class TestConfidenceRoutedKnobValue:
    def test_knob_builds_the_routed_analyzer(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "confidence-routed")
        monkeypatch.delenv(DECISION_CONFIDENCE_FLOOR_ENV_VAR, raising=False)
        got = make_query_analyzer_from_knob(
            decision_provider=MockDecisionProvider()
        )
        assert isinstance(got, ConfidenceRoutedQueryAnalyzer)

    def test_the_floor_reaches_the_analyzer(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "confidence-routed")
        monkeypatch.setenv(DECISION_CONFIDENCE_FLOOR_ENV_VAR, "0.42")
        got = make_query_analyzer_from_knob(
            decision_provider=MockDecisionProvider()
        )
        assert got.confidence_floor == 0.42

    def test_decision_knob_is_still_unrouted(self, monkeypatch):
        """The existing value must not acquire a floor. Phase 3's
        `decision` number was measured at floor 0.0 and has to stay
        reproducible."""
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        monkeypatch.setenv(DECISION_CONFIDENCE_FLOOR_ENV_VAR, "0.42")
        got = make_query_analyzer_from_knob(
            decision_provider=MockDecisionProvider()
        )
        assert not isinstance(got, ConfidenceRoutedQueryAnalyzer)
        assert got.confidence_floor == 0.0

    def test_no_provider_degrades_to_rules(self, monkeypatch):
        """Same posture as every other arm: a misconfigured classifier
        must not take the query path down."""
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "confidence-routed")
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "systemone")
        monkeypatch.delenv("NORA_DECISION_BASE_URL", raising=False)
        got = make_query_analyzer_from_knob()
        assert isinstance(got, MockQueryAnalyzer)
