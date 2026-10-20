"""Decision-model-backed query analysis.

Design §2 partitions `QueryIntent` by output shape: `query_type` and
`doc_type_scope` are closed-option choices a decision model owns, while
`entities`, `concepts`, `releases` and `standards_refs` are free-text spans
no decision model can emit. `HybridQueryAnalyzer` is that join.

The three closed-vocabulary multi-selects — `mnos`, `likely_features`,
`plan_ids` — are decision-model-ownable too, as one bool question per
candidate, but stay on the rules here so the first measurement has one
variable (design §2, phase-1 scope note).
"""

from __future__ import annotations

import logging
from dataclasses import replace

from core.src.decision.base import DecisionQuestion
from core.src.query.analyzer import MockQueryAnalyzer
from core.src.query.schema import DocTypeScope, QueryIntent, QueryType

logger = logging.getLogger(__name__)

QUERY_TYPE_QUESTION = DecisionQuestion(
    question_id="query_type",
    kind="choice",
    question=(
        "Which retrieval shape does this question want? Pick the single "
        "best match for the asker's intent."
    ),
    options=tuple(t.value for t in QueryType),
)
"""Options generated from the enum, never hand-listed.

Hand-listing is exactly how `LLMQueryAnalyzer`'s prompt came to omit
`summarize` and `fact` (design §2.1). A typed option set is a schema, not
prose, and this is where that property is realised: adding a `QueryType`
member extends the question with no edit here.
"""

DOC_SCOPE_QUESTION = DecisionQuestion(
    question_id="doc_type_scope",
    kind="choice",
    question=(
        "Which document types should be searched to answer this question?"
    ),
    options=tuple(s.value for s in DocTypeScope),
)

CLASSIFICATION_QUESTIONS: tuple[DecisionQuestion, ...] = (
    QUERY_TYPE_QUESTION,
    DOC_SCOPE_QUESTION,
)
"""Both questions in one request — parallel questions against one shared
state is the mechanism the wire format exists for, so two round trips
would waste it."""


class HybridQueryAnalyzer:
    """Decision model for the choice fields, regex for the spans.

    Falls back to the rule-based analyzer in full whenever the provider
    raises, and per field whenever an answer is absent or unparseable,
    mirroring `LLMQueryAnalyzer`'s self-fallback posture. A classifier
    outage degrades classification; it does not fail the query.
    """

    def __init__(self, provider, fallback=None) -> None:
        self._provider = provider
        self._fallback = fallback or MockQueryAnalyzer()

    def analyze(self, query: str) -> QueryIntent:
        """Analyze a query into structured intent.

        Args:
            query: Raw user query. Also the decision model's ``state``.

        Returns:
            A ``QueryIntent``. Same shape the rule-based analyzer returns —
            ``QueryIntent`` is the contract and does not change.
        """
        base = self._fallback.analyze(query)
        try:
            answers = self._provider.decide(query, CLASSIFICATION_QUESTIONS)
        except Exception as e:  # provider is third-party code
            logger.warning(
                "Decision provider failed (%s) — falling back to "
                "rule-based classification", e,
            )
            return base

        by_id = {a.question_id: a for a in answers}
        query_type = _coerce(
            by_id.get("query_type"), QueryType, base.query_type
        )
        doc_type_scope = _coerce(
            by_id.get("doc_type_scope"), DocTypeScope, base.doc_type_scope
        )
        logger.info(
            "Decision analysis: type=%s scope=%s model=%s",
            query_type.value, doc_type_scope.value,
            getattr(self._provider, "model", "?"),
        )
        return replace(
            base, query_type=query_type, doc_type_scope=doc_type_scope
        )


def _coerce(answer, enum_cls, fallback):
    """Parse one answer into its enum, keeping the rule's value when the
    answer is absent or not a member.

    A missing answer must not blank the field — the rule-based value is a
    better answer than a default.
    """
    if answer is None:
        return fallback
    try:
        return enum_cls(answer.value)
    except (ValueError, TypeError):
        logger.warning(
            "Decision provider returned %r for %s, which is not a %s — "
            "keeping the rule-based value",
            answer.value, answer.question_id, enum_cls.__name__,
        )
        return fallback
