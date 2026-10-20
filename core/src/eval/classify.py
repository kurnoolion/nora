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

import json
from dataclasses import dataclass
from pathlib import Path

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
