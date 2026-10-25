"""The four-valued NORA_QUERY_ANALYZER knob and the phase-2 identity
gate (strand jev-experimentation)."""

from __future__ import annotations

import pytest

from core.src.env.config import (
    DECISION_API_KEY_ENV_VAR,
    DECISION_CONFIDENCE_FLOOR_ENV_VAR,
    DEFAULT_DECISION_CONFIDENCE_FLOOR,
    DEFAULT_DECISION_PROVIDER,
    DEFAULT_QUERY_ANALYZER,
    QUERY_ANALYZER_ENV_VAR,
    QUERY_ANALYZERS,
    resolve_decision_api_key,
    resolve_decision_confidence_floor,
    resolve_decision_provider,
    resolve_query_analyzer,
)


class TestResolveQueryAnalyzer:
    def test_default_is_rules(self, monkeypatch):
        """Legacy is the default at every phase (design §7) — an
        administrator who sets nothing sees today's behaviour."""
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
    def test_default_is_empty(self, monkeypatch):
        """No implicit mock: the mock answers from keyword rules, so a
        served path that picked it by default would report the decision
        arm while serving the rules."""
        monkeypatch.delenv("NORA_DECISION_PROVIDER", raising=False)
        assert resolve_decision_provider() == ""
        assert DEFAULT_DECISION_PROVIDER == ""

    def test_mock_when_named(self, monkeypatch):
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "mock")
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
    query_analyzer_arm,
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

    @pytest.mark.parametrize("arm", ["decision", "confidence-routed"])
    def test_decision_without_a_named_provider_degrades_to_rules(
        self, monkeypatch, arm
    ):
        """No implicit mock on a served path."""
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, arm)
        monkeypatch.delenv("NORA_DECISION_PROVIDER", raising=False)
        assert isinstance(make_query_analyzer_from_knob(), MockQueryAnalyzer)

    def test_decision_with_the_mock_named(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "mock")
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
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "mock")
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


class TestResolveDecisionApiKey:
    """Auth for the decision endpoint.

    `SystemOneDecisionProvider` has always accepted an `api_key`, and
    nothing could supply one: no env var, no resolver, and
    `_build_decision_provider` passed only base_url / model / timeout.
    The Kev reference server is **open unless `KEV_API_KEY` is set**
    (`kev-runbook.md`), so the moment a key is put on a network-exposed
    decision server, classification degrades to the rules with a warning
    and keeps serving. Silent by design, which is what made it easy to
    miss. Found by `/drift-check dev-full` (DRIFT-3).
    """

    def test_default_is_empty(self, monkeypatch):
        """Empty means "send unauthenticated", which is what an open
        in-corp server wants. It must not become a literal empty Bearer
        header."""
        monkeypatch.delenv(DECISION_API_KEY_ENV_VAR, raising=False)
        assert resolve_decision_api_key() == ""

    def test_env_var_selects(self, monkeypatch):
        monkeypatch.setenv(DECISION_API_KEY_ENV_VAR, "sek-1")
        assert resolve_decision_api_key() == "sek-1"

    def test_cli_beats_env(self, monkeypatch):
        monkeypatch.setenv(DECISION_API_KEY_ENV_VAR, "sek-1")
        assert resolve_decision_api_key(cli_value="sek-2") == "sek-2"

    def test_surrounding_whitespace_is_stripped(self, monkeypatch):
        """A key pasted from a terminal or a secret file commonly carries
        a trailing newline, which would travel into the header."""
        monkeypatch.setenv(DECISION_API_KEY_ENV_VAR, "  sek-1\n")
        assert resolve_decision_api_key() == "sek-1"

    def test_the_key_reaches_the_provider(self, monkeypatch):
        """The whole point of DRIFT-3: the resolver existing is not the
        fix, the provider receiving the value is."""
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "systemone")
        monkeypatch.setenv("NORA_DECISION_BASE_URL", "http://example.test")
        monkeypatch.setenv(DECISION_API_KEY_ENV_VAR, "sek-1")
        analyzer = make_query_analyzer_from_knob()
        assert analyzer._provider._api_key == "sek-1"

    def test_no_key_configured_leaves_the_provider_unauthenticated(
        self, monkeypatch
    ):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "systemone")
        monkeypatch.setenv("NORA_DECISION_BASE_URL", "http://example.test")
        monkeypatch.delenv(DECISION_API_KEY_ENV_VAR, raising=False)
        analyzer = make_query_analyzer_from_knob()
        assert analyzer._provider._api_key == ""


class TestFactoryNeverRaisesOnConfig:
    """The served path degrades; only the resolvers raise."""

    @pytest.mark.parametrize("env", [
        {QUERY_ANALYZER_ENV_VAR: "decisions"},
        {QUERY_ANALYZER_ENV_VAR: "confidence-routed",
         "NORA_DECISION_PROVIDER": "mock",
         DECISION_CONFIDENCE_FLOOR_ENV_VAR: "half"},
        {QUERY_ANALYZER_ENV_VAR: "confidence-routed",
         "NORA_DECISION_PROVIDER": "mock",
         DECISION_CONFIDENCE_FLOOR_ENV_VAR: "1.5"},
        {QUERY_ANALYZER_ENV_VAR: "decision",
         "NORA_DECISION_PROVIDER": "jev"},
    ])
    def test_bad_config_degrades_to_rules(self, monkeypatch, caplog, env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with caplog.at_level("WARNING"):
            got = make_query_analyzer_from_knob()
        assert isinstance(got, MockQueryAnalyzer)
        assert "falling back to rule-based classification" in caplog.text

    def test_floor_is_irrelevant_to_the_decision_arm(self, monkeypatch):
        """An invalid floor only matters to the arm that reads it."""
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "mock")
        monkeypatch.setenv(DECISION_CONFIDENCE_FLOOR_ENV_VAR, "half")
        assert isinstance(
            make_query_analyzer_from_knob(), DecisionQueryAnalyzer
        )


class TestQueryAnalyzerArm:
    """`query_analyzer_arm()` reports what the factory builds, after
    degradation — the value /healthz exposes."""

    @pytest.mark.parametrize("env,expected", [
        ({}, "rules"),
        ({QUERY_ANALYZER_ENV_VAR: "rules"}, "rules"),
        ({QUERY_ANALYZER_ENV_VAR: "llm"}, "rules"),
        ({QUERY_ANALYZER_ENV_VAR: "nonsense"}, "rules"),
        ({QUERY_ANALYZER_ENV_VAR: "decision"}, "rules"),
        ({QUERY_ANALYZER_ENV_VAR: "decision",
          "NORA_DECISION_PROVIDER": "mock"}, "decision"),
        ({QUERY_ANALYZER_ENV_VAR: "decision",
          "NORA_DECISION_PROVIDER": "systemone"}, "rules"),
        ({QUERY_ANALYZER_ENV_VAR: "decision",
          "NORA_DECISION_PROVIDER": "systemone",
          "NORA_DECISION_BASE_URL": "http://example.test"}, "decision"),
        ({QUERY_ANALYZER_ENV_VAR: "confidence-routed",
          "NORA_DECISION_PROVIDER": "mock"}, "confidence-routed"),
        ({QUERY_ANALYZER_ENV_VAR: "confidence-routed",
          "NORA_DECISION_PROVIDER": "mock",
          DECISION_CONFIDENCE_FLOOR_ENV_VAR: "half"}, "rules"),
    ])
    def test_matches_what_the_factory_builds(
        self, monkeypatch, env, expected
    ):
        for k in (QUERY_ANALYZER_ENV_VAR, "NORA_DECISION_PROVIDER",
                  "NORA_DECISION_BASE_URL",
                  DECISION_CONFIDENCE_FLOOR_ENV_VAR):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert query_analyzer_arm() == expected
        by_class = {
            MockQueryAnalyzer: "rules",
            DecisionQueryAnalyzer: "decision",
            ConfidenceRoutedQueryAnalyzer: "confidence-routed",
        }
        assert by_class[type(make_query_analyzer_from_knob())] == expected

    def test_does_no_network_io(self, monkeypatch):
        import urllib.request

        def _refuse(*a, **k):
            raise AssertionError("query_analyzer_arm must not do I/O")

        monkeypatch.setattr(urllib.request, "urlopen", _refuse)
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "systemone")
        monkeypatch.setenv("NORA_DECISION_BASE_URL", "http://example.test")
        assert query_analyzer_arm() == "decision"


class TestSiraQueryServiceWiring:
    """nora-store reports the arm on /healthz and keeps a non-rules
    analyzer off the event loop."""

    def test_healthz_reports_the_arm(self, monkeypatch):
        import core.src.store_service.service as svc

        monkeypatch.delenv(QUERY_ANALYZER_ENV_VAR, raising=False)
        assert svc.healthz()["query_analyzer"] == "rules"
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "mock")
        assert svc.healthz()["query_analyzer"] == "decision"

    def _scope_thread(self, monkeypatch, analyzer):
        """Run the multi-cell path up to its scope step; return the thread
        the analyzer ran on. No cells are loaded, so the call stops with a
        503 straight after scoping."""
        import asyncio
        import threading

        from fastapi import HTTPException

        import core.src.store_service.service as svc

        seen = {}
        inner = analyzer.analyze

        def analyze(query):
            seen["thread"] = threading.get_ident()
            return inner(query)

        analyzer.analyze = analyze
        monkeypatch.setattr(
            svc, "make_query_analyzer_from_knob", lambda: analyzer
        )
        monkeypatch.setattr(svc, "_cells", {})

        async def run():
            seen["loop_thread"] = threading.get_ident()
            with pytest.raises(HTTPException):
                await svc._multi_cell_query(
                    svc._SiraQueryRequest(query="summarize sms"), 5
                )

        asyncio.run(run())
        return seen["thread"], seen["loop_thread"]

    def test_rules_runs_inline(self, monkeypatch):
        ran_on, loop = self._scope_thread(monkeypatch, MockQueryAnalyzer())
        assert ran_on == loop

    def test_decision_runs_off_the_event_loop(self, monkeypatch):
        ran_on, loop = self._scope_thread(
            monkeypatch, DecisionQueryAnalyzer(MockDecisionProvider())
        )
        assert ran_on != loop
