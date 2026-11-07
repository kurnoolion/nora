"""Answer-intent eval (strand sira-intent, design §8).

Scores an analyzer's ``QueryIntent.intent`` against a labelled set and
reports accuracy, per-intent precision and recall, and the change bar:
Kev beats the rules when ``change`` precision and recall are each at least
``CHANGE_BAR`` and overall accuracy is no worse than the rules arm's. The
second half needs two runs, so it is read off two reports, not decided here.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.src.eval.classify import _fill_placeholders
from core.src.query.schema import Intent

DEFAULT_INTENT_SET_PATH = Path(__file__).resolve().parent / "intent_set.json"
CHANGE_BAR = 0.9


@dataclass(frozen=True)
class IntentSample:
    """One labelled query. Labels are human-read intent, not rule output."""

    sample_id: str
    query: str
    intent: Intent


def load_intent_set(path: Path | None = None) -> list[IntentSample]:
    """Load a labelled intent set, operator placeholders filled in.

    Raises:
        ValueError: A label is not an ``Intent``.
    """
    payload = json.loads((path or DEFAULT_INTENT_SET_PATH).read_text(encoding="utf-8"))
    return [
        IntentSample(s["sample_id"], _fill_placeholders(s["query"]), Intent(s["intent"]))
        for s in payload["samples"]
    ]


@dataclass(frozen=True)
class IntentResult:
    sample_id: str
    query: str
    expected: Intent
    predicted: Intent
    latency_ms: float

    @property
    def correct(self) -> bool:
        return self.expected is self.predicted


@dataclass
class IntentReport:
    arm: str
    set_id: str = ""
    results: list[IntentResult] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return sum(r.correct for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def per_class(self) -> dict[Intent, dict]:
        out = {}
        for i in Intent:
            tp = sum(r.expected is i and r.predicted is i for r in self.results)
            predicted = sum(r.predicted is i for r in self.results)
            support = sum(r.expected is i for r in self.results)
            out[i] = {
                "precision": tp / predicted if predicted else 0.0,
                "recall": tp / support if support else 0.0,
                "support": support,
            }
        return out

    @property
    def meets_change_bar(self) -> bool:
        c = self.per_class[Intent.CHANGE]
        return c["precision"] >= CHANGE_BAR and c["recall"] >= CHANGE_BAR

    def _latency(self, q: float) -> float:
        xs = sorted(r.latency_ms for r in self.results)
        return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0.0

    def summary_text(self) -> str:
        c = self.per_class[Intent.CHANGE]
        return (
            f"accuracy={self.accuracy:.3f} "
            f"change_precision={c['precision']:.2f} change_recall={c['recall']:.2f} "
            f"change_bar={'met' if self.meets_change_bar else 'not met'} "
            f"p50={self._latency(0.5):.1f}ms p95={self._latency(0.95):.1f}ms"
        )

    def matrix_text(self) -> str:
        """Per-intent precision/recall, the confusion rows, and the misses
        by sample id (ids, not text, so the block is safe to paste)."""
        names = [i.value for i in Intent]
        w = max(len(n) for n in names) + 1
        lines = [f"{'':{w}}" + " ".join(f"{n[:6]:>6}" for n in names) + "   prec  rec"]
        pc = self.per_class
        for e in Intent:
            row = [sum(r.expected is e and r.predicted is p for r in self.results) for p in Intent]
            lines.append(f"{e.value:{w}}" + " ".join(f"{n:>6}" for n in row)
                         + f"   {pc[e]['precision']:.2f} {pc[e]['recall']:.2f}")
        misses = [f"{r.sample_id}:{r.expected.value}->{r.predicted.value}"
                  for r in self.results if not r.correct]
        lines.append("misses: " + (", ".join(misses) if misses else "none"))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "arm": self.arm,
            "set_id": self.set_id,
            "accuracy": self.accuracy,
            "meets_change_bar": self.meets_change_bar,
            "per_class": {i.value: v for i, v in self.per_class.items()},
            "results": [
                {"sample_id": r.sample_id, "expected": r.expected.value,
                 "predicted": r.predicted.value, "latency_ms": r.latency_ms}
                for r in self.results
            ],
        }


def run_intent(analyzer, samples: list[IntentSample], *, arm: str, set_id: str = "") -> IntentReport:
    """Run ``analyzer.analyze`` over ``samples`` and score the intent field."""
    report = IntentReport(arm=arm, set_id=set_id)
    for s in samples:
        t = time.perf_counter()
        predicted = analyzer.analyze(s.query).intent
        report.results.append(IntentResult(
            s.sample_id, s.query, s.intent, predicted, (time.perf_counter() - t) * 1000))
    return report
