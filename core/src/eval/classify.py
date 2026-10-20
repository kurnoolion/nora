"""Query-classification evaluation harness.

Strand jev-experimentation, phase 1. Measures a query analyzer against a
labelled set and reports a per-class confusion matrix weighted by how far
apart the confused classes sit in the pipeline's per-type retrieval
configuration. See ``docs/compact/strands/jev-experimentation/
decision-model-design.md`` §6.

The harness instantiates analyzers directly. It reads no corpus, needs no
``<env_dir>``, and does not touch the serving path.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.src.env.config import (
    DEFAULT_BM25_WEIGHT_BY_TYPE,
    is_broad_query_type,
)
from core.src.query.pipeline import (
    _TYPE_DISABLE_GROUPING,
    _TYPE_MAX_DISTANCE,
    _TYPE_REWRITE_ENABLED,
)
from core.src.query.schema import DocTypeScope, QueryType

DEFAULT_SET_PATH = Path(__file__).resolve().parent / "classify_set.json"


@dataclass(frozen=True)
class ClassifySample:
    """One labelled query. Labels are human-read intent, not rule output."""

    sample_id: str
    query: str
    query_type: QueryType
    doc_type_scope: DocTypeScope


def load_classify_set(path: Path | None = None) -> list[ClassifySample]:
    """Load a labelled classification set from JSON.

    Args:
        path: Set file. ``None`` loads the committed dev set.

    Returns:
        Samples in file order.

    Raises:
        ValueError: A label is not a member of its enum.
    """
    payload = json.loads((path or DEFAULT_SET_PATH).read_text(encoding="utf-8"))
    return [
        ClassifySample(
            sample_id=s["sample_id"],
            query=s["query"],
            query_type=QueryType(s["query_type"]),
            doc_type_scope=DocTypeScope(s["doc_type_scope"]),
        )
        for s in payload["samples"]
    ]


# ── Divergence weighting ───────────────────────────────────────

KNOB_NAMES: tuple[str, ...] = (
    "top_k_bucket",
    "bm25_weight",
    "rewrite_enabled",
    "max_distance",
    "disable_grouping",
)
"""The per-type retrieval knobs a QueryType selects.

Rerank is deliberately absent: pipeline's ``_TYPE_RERANK_ENABLED`` is empty
by design, so every class resolves to the same default and the column
would contribute 0.0 to every pairwise distance. A test asserts the map
is still empty, so adding an entry forces this tuple to grow.
"""


def _knob_row(query_type: QueryType) -> tuple:
    """The five knob values a QueryType selects, read from the live
    sources rather than copied — see this module's docstring."""
    return (
        "broad" if is_broad_query_type(query_type.value) else "narrow",
        DEFAULT_BM25_WEIGHT_BY_TYPE.get(query_type.value, 0.0),
        _TYPE_REWRITE_ENABLED.get(query_type, False),
        _TYPE_MAX_DISTANCE.get(query_type),
        query_type in _TYPE_DISABLE_GROUPING,
    )


KNOB_PROFILE: dict[QueryType, tuple] = {t: _knob_row(t) for t in QueryType}


def divergence_cost(expected: QueryType, predicted: QueryType) -> float:
    """Downstream cost of predicting ``predicted`` when the truth is
    ``expected``, as the fraction of retrieval knobs that differ.

    0.0 means the two classes select an identical retrieval
    configuration, so the mislabel costs nothing downstream. 1.0 means
    every knob is inverted. Symmetric by construction.
    """
    a, b = KNOB_PROFILE[expected], KNOB_PROFILE[predicted]
    differing = sum(1 for x, y in zip(a, b) if x != y)
    return differing / len(KNOB_NAMES)


def profile_digest() -> str:
    """Short digest of the knob profile.

    Two reports are comparable on weighted cost only when their digests
    agree: re-tuning a per-type knob changes what a confusion costs.
    """
    payload = json.dumps(
        {t.value: [str(v) for v in KNOB_PROFILE[t]] for t in QueryType},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ── Running and reporting ──────────────────────────────────────


@dataclass
class ClassifyResult:
    """One sample's outcome under one arm."""

    sample_id: str
    query: str
    expected: QueryType
    predicted: QueryType
    expected_scope: DocTypeScope
    predicted_scope: DocTypeScope
    cost: float
    latency_ms: float
    stable: bool

    @property
    def correct(self) -> bool:
        return self.expected is self.predicted

    @property
    def scope_correct(self) -> bool:
        return self.expected_scope is self.predicted_scope


@dataclass
class ClassifyReport:
    """One arm's results over one labelled set.

    Keeps design §6's three questions separate: ``accuracy`` and
    ``weighted_cost`` answer "is it more accurate", the latency fields
    answer "is it faster", and ``deterministic`` answers "does it work".
    """

    arm: str = ""
    set_id: str = ""
    profile: str = ""
    repeats: int = 1
    results: list[ClassifyResult] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.correct for r in self.results) / len(self.results)

    @property
    def weighted_cost(self) -> float:
        """Mean divergence cost. Lower is better; 0.0 means every
        mislabel was downstream-free."""
        if not self.results:
            return 0.0
        return sum(r.cost for r in self.results) / len(self.results)

    @property
    def scope_accuracy(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.scope_correct for r in self.results) / len(self.results)

    @property
    def deterministic(self) -> bool:
        return all(r.stable for r in self.results)

    @property
    def latency_p50_ms(self) -> float:
        if not self.results:
            return 0.0
        return statistics.median(r.latency_ms for r in self.results)

    @property
    def latency_p95_ms(self) -> float:
        if not self.results:
            return 0.0
        ordered = sorted(r.latency_ms for r in self.results)
        idx = min(len(ordered) - 1, int(0.95 * len(ordered)))
        return ordered[idx]

    def confusion(self) -> dict[QueryType, dict[QueryType, int]]:
        """Expected (row) against predicted (column), all ten classes
        present whether or not they occur."""
        matrix = {a: {b: 0 for b in QueryType} for a in QueryType}
        for r in self.results:
            matrix[r.expected][r.predicted] += 1
        return matrix

    def per_class(self) -> dict[QueryType, dict[str, float]]:
        matrix = self.confusion()
        out = {}
        for t in QueryType:
            support = sum(matrix[t].values())
            predicted = sum(matrix[a][t] for a in QueryType)
            hits = matrix[t][t]
            out[t] = {
                "support": support,
                "recall": hits / support if support else 0.0,
                "precision": hits / predicted if predicted else 0.0,
            }
        return out

    def matrix_text(self) -> str:
        """Fixed-width confusion matrix. Carries class names and counts
        only — no query text, so it is chat-pasteable (NFR-8)."""
        matrix = self.confusion()
        labels = [t.value[:8] for t in QueryType]
        head = "expected \\ predicted".ljust(22) + " ".join(
            lbl.rjust(8) for lbl in labels
        )
        lines = [head]
        for t in QueryType:
            cells = " ".join(str(matrix[t][b]).rjust(8) for b in QueryType)
            lines.append(t.value[:8].ljust(22) + cells)
        stats = self.per_class()
        lines.append("")
        lines.append("class".ljust(22) + "support".rjust(9)
                     + "recall".rjust(9) + "precision".rjust(11))
        for t in QueryType:
            s = stats[t]
            lines.append(
                t.value[:8].ljust(22)
                + str(int(s["support"])).rjust(9)
                + f"{s['recall']:.2f}".rjust(9)
                + f"{s['precision']:.2f}".rjust(11)
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "arm": self.arm,
            "set_id": self.set_id,
            "profile": self.profile,
            "repeats": self.repeats,
            "accuracy": self.accuracy,
            "weighted_cost": self.weighted_cost,
            "scope_accuracy": self.scope_accuracy,
            "deterministic": self.deterministic,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "confusion": {
                a.value: {b.value: n for b, n in row.items()}
                for a, row in self.confusion().items()
            },
            "per_class": {
                t.value: stats for t, stats in self.per_class().items()
            },
            "results": [
                {
                    "sample_id": r.sample_id,
                    "expected": r.expected.value,
                    "predicted": r.predicted.value,
                    "expected_scope": r.expected_scope.value,
                    "predicted_scope": r.predicted_scope.value,
                    "cost": r.cost,
                    "latency_ms": r.latency_ms,
                    "stable": r.stable,
                }
                for r in self.results
            ],
        }


def run_classify(
    analyzer,
    samples: list[ClassifySample],
    repeats: int = 1,
    arm: str = "",
    set_id: str = "",
) -> ClassifyReport:
    """Run one analyzer over a labelled set.

    Args:
        analyzer: Any object with ``analyze(query) -> QueryIntent``.
        samples: Labelled samples.
        repeats: Runs per sample. >1 probes determinism; the FIRST run's
            prediction is the one scored, so the score does not depend on
            how many repeats were asked for.
        arm: Label for the report ("rules", "llm", ...).
        set_id: Labelled set's id, stamped for provenance.

    Returns:
        A report stamped with the current knob-profile digest.
    """
    results: list[ClassifyResult] = []
    for sample in samples:
        predictions = []
        latencies = []
        for _ in range(max(1, repeats)):
            started = time.perf_counter()
            intent = analyzer.analyze(sample.query)
            latencies.append((time.perf_counter() - started) * 1000.0)
            predictions.append((intent.query_type, intent.doc_type_scope))
        predicted, predicted_scope = predictions[0]
        results.append(
            ClassifyResult(
                sample_id=sample.sample_id,
                query=sample.query,
                expected=sample.query_type,
                predicted=predicted,
                expected_scope=sample.doc_type_scope,
                predicted_scope=predicted_scope,
                cost=divergence_cost(sample.query_type, predicted),
                latency_ms=statistics.median(latencies),
                stable=len(set(predictions)) == 1,
            )
        )
    return ClassifyReport(
        arm=arm,
        set_id=set_id,
        profile=profile_digest(),
        repeats=max(1, repeats),
        results=results,
    )
