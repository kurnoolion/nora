"""Tests for the answer-intent eval (strand sira-intent, design §8).

Covers:
- The committed set: 10 per intent, unique ids, placeholders filled, no
  real operator name in the committed text.
- The scorer: accuracy, per-intent precision/recall, the change bar.
- `classify_cli --target intent` on the rules and mock-decision arms.
"""

from __future__ import annotations

import re
from collections import Counter

from core.src.eval.intent_eval import (
    DEFAULT_INTENT_SET_PATH,
    IntentSample,
    load_intent_set,
    run_intent,
)
from core.src.query.schema import Intent, QueryIntent


def test_committed_set_has_at_least_ten_per_intent_and_unique_ids():
    samples = load_intent_set()
    counts = Counter(s.intent for s in samples)
    assert set(counts) == set(Intent) and min(counts.values()) >= 10
    assert len({s.sample_id for s in samples}) == len(samples)


def test_committed_set_covers_pasted_bug_reports_as_compliance():
    bugs = [s for s in load_intent_set() if s.sample_id.startswith("kb")]
    assert len(bugs) >= 6 and all(s.intent is Intent.COMPLIANCE for s in bugs)


def test_placeholders_are_filled_and_committed_text_names_no_operator():
    raw = DEFAULT_INTENT_SET_PATH.read_text(encoding="utf-8").lower()
    assert not re.search(r"\b(verizon|t-mobile|tmobile|at&t|vzw|tmo|att)\b", raw)
    assert all("{MNO_" not in s.query for s in load_intent_set())


class _Fixed:
    """Answers from a script keyed by query."""

    def __init__(self, answers):
        self._answers = answers

    def analyze(self, query):
        return QueryIntent(raw_query=query, intent=self._answers[query])


def _samples(*pairs):
    return [IntentSample(f"s{i}", q, want) for i, (q, want) in enumerate(pairs)]


def test_report_metrics_and_change_bar():
    samples = _samples(("a", Intent.CHANGE), ("b", Intent.CHANGE),
                       ("c", Intent.FACT), ("d", Intent.FACT))
    got = {"a": Intent.CHANGE, "b": Intent.FACT, "c": Intent.FACT, "d": Intent.CHANGE}
    r = run_intent(_Fixed(got), samples, arm="test")
    assert r.accuracy == 0.5
    assert r.per_class[Intent.CHANGE] == {"precision": 0.5, "recall": 0.5, "support": 2}
    assert not r.meets_change_bar
    perfect = run_intent(_Fixed({q.query: q.intent for q in samples}), samples, arm="test")
    assert perfect.accuracy == 1.0 and perfect.meets_change_bar


def test_precision_of_a_never_predicted_class_is_zero_not_a_crash():
    samples = _samples(("a", Intent.LIST))
    r = run_intent(_Fixed({"a": Intent.OTHER}), samples, arm="test")
    assert r.per_class[Intent.LIST]["precision"] == 0.0


def test_cli_rules_arm_runs_on_the_intent_target(capsys):
    from core.src.eval.classify_cli import main
    assert main(["--target", "intent", "--arm", "rules"]) == 0
    out = capsys.readouterr().out
    assert "set=intent-dev-v1" in out and "change_precision=" in out


def test_cli_mock_decision_arm_asks_only_the_intent_question(capsys, monkeypatch):
    monkeypatch.delenv("NORA_DECISION_PROVIDER", raising=False)
    from core.src.eval.classify_cli import main
    assert main(["--target", "intent", "--arm", "confidence-routed"]) == 0
    out = capsys.readouterr().out
    assert "intent: model answered" in out
