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
from dataclasses import dataclass
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
