"""Answer intent for the SIRA path of the Ask page (strand sira-intent).

nora-web classifies a question once, first, before choosing a lane
(design §4, D-DRAFT-3). The classifier is the one ``NORA_QUERY_ANALYZER``
selects (``rules`` by default, which reproduces today's routing), asked
only the intent question (``SIRA_QUESTIONS``). MNO and release always come
from the rules extractors.

``NORA_SIRA_INTENT_PROMPTS`` (default off) turns on the per-intent prompts
and the strict ``change`` routing: a change question the History lane is
enabled for but cannot serve is an error instead of a SIRA answer.
"""

from __future__ import annotations

import functools
import os

from core.src.query.schema import QueryIntent

PROMPTS_SWITCH = "NORA_SIRA_INTENT_PROMPTS"
_ON = {"1", "true", "yes", "on"}


def prompts_enabled() -> bool:
    """The ``NORA_SIRA_INTENT_PROMPTS`` switch, read per request; default off."""
    return os.getenv(PROMPTS_SWITCH, "").strip().lower() in _ON


@functools.lru_cache(maxsize=1)
def analyzer():
    """The intent analyzer, built once per process from the knob.

    Never raises: a misconfigured knob or decision provider degrades to
    the rules (``make_query_analyzer_from_knob``)."""
    from core.src.decision.analyzer import SIRA_QUESTIONS
    from core.src.query.analyzer import make_query_analyzer_from_knob
    return make_query_analyzer_from_knob(questions=SIRA_QUESTIONS)


def classify(question: str) -> tuple[QueryIntent, dict]:
    """The question's ``QueryIntent`` and who decided its intent:
    ``{"intent": {"source": "model" | "rules", "confidence": float | None}}``.
    Blocking (a Kev arm makes an HTTP call); run it in a thread."""
    a = analyzer()
    if hasattr(a, "analyze_with_trace"):
        return a.analyze_with_trace(question)
    return a.analyze(question), {"intent": {"source": "rules", "confidence": None}}


def lane_fields(qi: QueryIntent, trace: dict) -> dict:
    """What the feedback row's ``lane_config`` records about the intent
    (design §7): the label, who decided it (``model`` = the decision model
    above the floor, ``rules`` otherwise), the model's confidence (``None``
    when no model answered), and the analyzer arm serving the knob."""
    from core.src.query.analyzer import query_analyzer_arm
    t = trace.get("intent") or {}
    return {
        "intent": qi.intent.value,
        "intent_source": t.get("source", "rules"),
        "intent_confidence": t.get("confidence"),
        "intent_analyzer": query_analyzer_arm(),
    }


def intent_badge(lane: str, lane_config: "dict | None") -> str:
    """The badge an answer card shows: ``lookup`` for the id_lookup lane,
    else the recorded intent; "" when none was recorded (older rows, NORA)."""
    if lane == "id_lookup":
        return "lookup"
    return str((lane_config or {}).get("intent") or "")
