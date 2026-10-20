"""Decision-model-backed query analysis.

Design §2 partitions `QueryIntent` by output shape: `query_type` and
`doc_type_scope` are closed-option choices a decision model owns, while
`entities`, `concepts`, `releases` and `standards_refs` are free-text spans
no decision model can emit. `DecisionQueryAnalyzer` is that join.

`ConfidenceRoutedQueryAnalyzer` is the same join under a routing policy:
it keeps the model's label only where the model's calibrated confidence
clears a floor, and takes the rule-based label everywhere else. Two names
because they are two policies, not two implementations — the floor is the
only thing that differs, and `DecisionQueryAnalyzer` is the floor-free
case.

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

QUERY_TYPE_DESCRIPTIONS: dict[QueryType, str] = {
    QueryType.SINGLE_DOC:
        "A lookup whose answer lives inside one named specification or "
        "plan document.",
    QueryType.CROSS_DOC:
        "The answer spans several specification documents, e.g. 'all "
        "requirements for X'.",
    QueryType.CROSS_MNO_COMPARISON:
        "Compares two or more mobile network operators.",
    QueryType.RELEASE_DIFF:
        "Compares two releases or quarters of the same specification: "
        "what changed.",
    QueryType.STANDARDS_COMPARISON:
        "Compares an operator requirement against a 3GPP or industry "
        "standard.",
    QueryType.TRACEABILITY:
        "Links requirements to the test cases that verify them, or asks "
        "about coverage.",
    QueryType.FEATURE_LEVEL:
        "Asks for all requirements belonging to one named device feature.",
    QueryType.SUMMARIZE:
        "Asks for an overview, explanation or summary rather than one "
        "specific fact.",
    QueryType.FACT:
        "Asks for one specific value: a number, a timer value, a default, "
        "a cause code.",
    QueryType.GENERAL:
        "Anything that fits none of the other categories.",
}
"""One gloss per `QueryType`, carried as the wire format's `criteria`
values.

**These are load-bearing, not documentation.** Measured on
`classify-dev-v1` against a live Kev-4B: bare option identifiers scored
0.460, the same options glossed scored 0.760 — the difference between
"worse than the rules" and "level with them". A bare `feature_level`
asks the model to guess what the identifier means; the gloss is where
NORA's taxonomy, which is the thing generic weights do not know, is
actually stated.

They are keyed by the enum rather than listed, and a test asserts every
member has one, so the §2.1 property survives: adding a `QueryType`
member fails the suite instead of silently shipping a bare option.
"""

DOC_SCOPE_DESCRIPTIONS: dict[DocTypeScope, str] = {
    DocTypeScope.REQUIREMENTS: "Search requirement specification documents.",
    DocTypeScope.TEST_CASES: "Search test case documents.",
    DocTypeScope.BOTH:
        "Search both requirement specifications and test case documents.",
}

QUERY_TYPE_QUESTION = DecisionQuestion(
    question_id="query_type",
    kind="choice",
    question=(
        "Which retrieval shape does this question want? Pick the single "
        "best match for the asker's intent."
    ),
    options=tuple(t.value for t in QueryType),
    descriptions=tuple(QUERY_TYPE_DESCRIPTIONS[t] for t in QueryType),
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
    descriptions=tuple(DOC_SCOPE_DESCRIPTIONS[s] for s in DocTypeScope),
)

CLASSIFICATION_QUESTIONS: tuple[DecisionQuestion, ...] = (
    QUERY_TYPE_QUESTION,
    DOC_SCOPE_QUESTION,
)
"""Both questions in one request — parallel questions against one shared
state is the mechanism the wire format exists for, so two round trips
would waste it."""


class DecisionQueryAnalyzer:
    """Decision model for the choice fields, regex for the spans.

    Falls back to the rule-based analyzer in full whenever the provider
    raises, and per field whenever an answer is absent or unparseable,
    mirroring `LLMQueryAnalyzer`'s self-fallback posture. A classifier
    outage degrades classification; it does not fail the query.
    """

    def __init__(self, provider, fallback=None, confidence_floor=0.0) -> None:
        self._provider = provider
        self._fallback = fallback or MockQueryAnalyzer()
        self.confidence_floor = float(confidence_floor)
        """Answers below this calibrated confidence are discarded in
        favour of the rule-based value.

        0.0 — the default — keeps every answer the model returns, which
        is what phase 2 and phase 3's `decision` arm measured. Callers
        that want routing say so by name: see
        `ConfidenceRoutedQueryAnalyzer`."""
        self.call_count = 0
        self.fallback_count = 0
        """Queries served entirely from the rule-based analyzer because
        the provider raised.

        Load-bearing, not diagnostic: per-query fallback is the right
        serving behaviour but it makes a total provider outage look like
        a successful run with the rule-based numbers. An evaluation
        harness MUST check this before recording anything under a
        `decision` label — see `eval.classify_cli`, which refuses a
        non-zero count. Found the hard way: a verified head with an
        unreachable server reported the rules baseline as the decision
        arm's result."""

        self.routed_to_model: dict[str, int] = {}
        self.routed_to_rules: dict[str, int] = {}
        """Fields served by each side, keyed by question id.

        Coverage is the number that makes a routed result readable: an
        arm that routes 2 % of fields to the model and scores well is
        reporting the rules under the model's name. Kept separate from
        `fallback_count` on purpose — a fallback means the provider was
        unreachable and the arm is contaminated, a routing means the
        provider answered and the policy declined it."""

    def analyze(self, query: str) -> QueryIntent:
        """Analyze a query into structured intent.

        Args:
            query: Raw user query. Also the decision model's ``state``.

        Returns:
            A ``QueryIntent``. Same shape the rule-based analyzer returns —
            ``QueryIntent`` is the contract and does not change.
        """
        self.call_count += 1
        base = self._fallback.analyze(query)
        try:
            answers = self._provider.decide(query, CLASSIFICATION_QUESTIONS)
        except Exception as e:  # provider is third-party code
            self.fallback_count += 1
            logger.warning(
                "Decision provider failed (%s) — falling back to "
                "rule-based classification", e,
            )
            return base

        by_id = {a.question_id: a for a in answers}
        query_type = self._field(
            "query_type", by_id, QueryType, base.query_type
        )
        doc_type_scope = self._field(
            "doc_type_scope", by_id, DocTypeScope, base.doc_type_scope
        )
        logger.info(
            "Decision analysis: type=%s scope=%s model=%s",
            query_type.value, doc_type_scope.value,
            getattr(self._provider, "model", "?"),
        )
        return replace(
            base, query_type=query_type, doc_type_scope=doc_type_scope
        )

    def _field(self, question_id, by_id, enum_cls, fallback):
        """Resolve one field, applying the confidence floor.

        Three ways the rule-based value wins: no answer arrived, the
        answer's confidence is below the floor, or the answer is not a
        member of the enum. All three are counted as routed to the rules,
        because coverage must not credit the model for a value that was
        discarded.
        """
        answer = by_id.get(question_id)
        if answer is not None and answer.confidence < self.confidence_floor:
            logger.info(
                "Routing %s to the rules: confidence %.3f below floor %.3f",
                question_id, answer.confidence, self.confidence_floor,
            )
            self._count(self.routed_to_rules, question_id)
            return fallback
        value = _coerce(answer, enum_cls, None)
        if value is None:
            self._count(self.routed_to_rules, question_id)
            return fallback
        self._count(self.routed_to_model, question_id)
        return value

    def coverage(self, question_id: str) -> float:
        """Fraction of this field's answers that came from the model.

        0.0 for a field nobody asked about — not a crash, and not 1.0:
        an unasked field was answered by the model none of the time.
        """
        model = self.routed_to_model.get(question_id, 0)
        total = model + self.routed_to_rules.get(question_id, 0)
        return model / total if total else 0.0

    @staticmethod
    def _count(counter: dict[str, int], question_id: str) -> None:
        counter[question_id] = counter.get(question_id, 0) + 1


class ConfidenceRoutedQueryAnalyzer(DecisionQueryAnalyzer):
    """The decision model where it is confident, the rules elsewhere.

    Identical machinery to `DecisionQueryAnalyzer`; the floor is the only
    difference, and it is a required argument because an unstated floor
    makes a routed result unreproducible. The separate name exists so a
    configuration reads as the policy it is — `--arm confidence-routed`
    names this class and nothing else.

    **Why this is worth a seam of its own.** Measured on `classify-dev-v1`
    against a live Kev-4B (phase-3-measurement.md): mean confidence 0.425
    on answers that were correct against 0.268 on answers that were not.
    The model ties the rules on accuracy and beats them on knowing when
    it is wrong, and an abstention signal is something the rules cannot
    produce at any price.

    **The floor is not yet a validated number.** The 0.50 that scored
    0.860 was selected on the same 50 samples it was scored on, and the
    sweep across thresholds is non-monotonic — the shape of n=50 noise,
    not of a curve. The separation above survives a held-out set; the
    specific threshold does not. Setting it is what a labelled sample of
    real traffic is for, and Kev's own card says the same: refit with
    `python -m kev.calibrate` on your own workload before trusting a
    threshold.
    """

    def __init__(self, provider, confidence_floor, fallback=None) -> None:
        super().__init__(
            provider, fallback=fallback, confidence_floor=confidence_floor
        )


def _coerce(answer, enum_cls, fallback):
    """Parse one answer into its enum, returning ``fallback`` when the
    answer is absent or not a member.

    `_field` passes ``None`` as the fallback so it can tell "the model's
    value" from "the rules' value" and count coverage honestly; a missing
    answer must not blank the field, so substituting the rule-based value
    is `_field`'s job rather than this function's.
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
