"""Query analyzer (TDD 7.1).

Extracts structured intent from a natural language query:
entities, concepts, MNOs, releases, query type, standards refs,
likely features, plan IDs.

Two implementations:
  - MockQueryAnalyzer: keyword-based, no LLM required (PoC default)
  - LLMQueryAnalyzer: uses LLMProvider for more accurate extraction
"""

from __future__ import annotations

import json
import logging
import re

from core.src.query.schema import (
    QueryIntent,
    QueryType,
    DocTypeScope,
)

logger = logging.getLogger(__name__)

# ── MNO aliases ─────────────────────────────────────────────────

_MNO_ALIASES: dict[str, str] = {
    "verizon": "VZW",
    "vzw": "VZW",
    "vz": "VZW",
    "t-mobile": "TMO",
    "tmobile": "TMO",
    "tmo": "TMO",
    "at&t": "ATT",
    "att": "ATT",
}

# Token-boundary patterns per alias: short aliases ("att", "vz", "tmo") are
# substrings of ordinary words (attach, pattern, attribute...) — a bare
# `alias in query` check silently scoped queries to the wrong MNO cell set.
# Boundaries are "not adjacent to [a-z0-9]" rather than \b so "at&t" and
# "t-mobile" still match as written.
_MNO_ALIAS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"), mno)
    for alias, mno in _MNO_ALIASES.items()
]

def mno_surface_forms(mno: str) -> list[str]:
    """Every written form of ``mno`` the analyzer recognises (lower case):
    the aliases that resolve to it, plus the code itself. Longest first,
    so a caller stripping them removes the full name before a shorter
    form inside it. Used by the golden eval's text check to ignore an
    operator-name-only edit between releases."""
    forms = {a for a, m in _MNO_ALIASES.items() if m == mno} | {mno.lower()}
    return sorted(forms, key=lambda f: (-len(f), f))


# ── Plan name aliases ───────────────────────────────────────────

_PLAN_ALIASES: dict[str, str] = {
    "data retry": "LTEDATARETRY",
    "dataretry": "LTEDATARETRY",
    "lte data retry": "LTEDATARETRY",
    "sms": "LTESMS",
    "lte sms": "LTESMS",
    "at command": "LTEAT",
    "at commands": "LTEAT",
    "band 13": "LTEB13NAC",
    "b13": "LTEB13NAC",
    "nac": "LTEB13NAC",
    "network access": "LTEB13NAC",
    "ota": "LTEOTADM",
    "otadm": "LTEOTADM",
    "device management": "LTEOTADM",
}

# ── Feature keyword mapping ────────────────────────────────────

_FEATURE_KEYWORDS: dict[str, list[str]] = {
    "DATA_RETRY": ["data retry", "retry", "throttle", "throttling", "backoff"],
    "SMS": ["sms", "short message", "messaging", "mo-sms", "mt-sms"],
    "BAND_SELECTION": ["band 13", "b13", "nac", "band selection", "earfcn"],
    "OTA_DM": ["ota", "over-the-air", "device management", "otadm", "fota"],
    "AT_COMMANDS": ["at command", "at+", "modem", "test automation"],
    "IMS_REGISTRATION": ["ims", "volte", "sip", "p-cscf", "ims registration"],
    "EPS_MOBILITY": ["attach", "detach", "tau", "eps", "emm", "mobility"],
    "BEARER_MANAGEMENT": ["bearer", "qos", "qci", "pdn", "eps bearer"],
    "TIMER_MANAGEMENT": ["timer", "t3402", "t3411", "t3412", "t3417", "backoff timer"],
    "SIM_MANAGEMENT": ["sim", "uicc", "euicc", "isim", "usim", "esim"],
    "ERROR_HANDLING": ["error", "reject", "cause code", "failure", "emm cause", "esm cause"],
    "PLMN_SELECTION": ["plmn", "network selection", "roaming", "hplmn"],
    "HANDOVER": ["handover", "handoff", "reselection", "cell reselection"],
    "APN_PROVISIONING": ["apn", "access point", "provisioning"],
    "RF_ANTENNA": ["antenna", "mimo", "rf", "radio", "diversity"],
    "TEST_COMPLIANCE": ["test", "compliance", "certification"],
}

# ── 3GPP spec pattern ──────────────────────────────────────────

_SPEC_PATTERN = re.compile(
    r"3GPP\s+TS\s+(\d[\d.]*\d)", re.IGNORECASE
)

# ── Req ID pattern ─────────────────────────────────────────────

_REQ_ID_PATTERN = re.compile(r"VZ_REQ_\w+_\d+")

# ── Release patterns ───────────────────────────────────────────

_RELEASE_PATTERNS = [
    (re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+20(\d{2})", re.I), None),
    (re.compile(r"20(\d{2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I), None),
    (re.compile(r"latest|current", re.I), "latest"),
]


class MockQueryAnalyzer:
    """Keyword-based query analyzer (no LLM required).

    Satisfies the analyzer interface. Uses pattern matching and
    keyword lookup to extract query intent from natural language.
    """

    def analyze(self, query: str) -> QueryIntent:
        """Analyze a natural language query into structured intent."""
        q_lower = query.lower()

        entities = self._extract_entities(query)
        concepts = self._extract_concepts(q_lower)
        mnos = self._extract_mnos(q_lower)
        releases = self._extract_releases(query)
        standards_refs = self._extract_standards(query)
        likely_features = self._extract_features(q_lower)
        plan_ids = self._extract_plan_ids(q_lower)
        query_type = self._classify_query_type(q_lower, mnos, releases, standards_refs)
        doc_type_scope = self._classify_doc_scope(q_lower)

        intent = QueryIntent(
            raw_query=query,
            entities=entities,
            concepts=concepts,
            mnos=mnos,
            releases=releases,
            query_type=query_type,
            doc_type_scope=doc_type_scope,
            standards_refs=standards_refs,
            likely_features=likely_features,
            plan_ids=plan_ids,
        )

        logger.info(
            f"Query analysis: type={query_type.value}, "
            f"mnos={mnos}, features={likely_features}, "
            f"plans={plan_ids}, entities={entities[:3]}"
        )
        return intent

    def _extract_entities(self, query: str) -> list[str]:
        """Extract named entities (req IDs, timer names, etc.)."""
        entities = []

        # Requirement IDs
        for m in _REQ_ID_PATTERN.finditer(query):
            entities.append(m.group(0))

        # Timer names (T3xxx)
        for m in re.finditer(r"\b(T3\d{3})\b", query, re.I):
            entities.append(m.group(1).upper())

        # Cause codes
        for m in re.finditer(r"\bcause\s+code\s+(\d+)\b", query, re.I):
            entities.append(f"cause_code_{m.group(1)}")

        return entities

    def _extract_concepts(self, q_lower: str) -> list[str]:
        """Extract telecom concepts."""
        concepts = []
        concept_patterns = [
            "attach reject", "attach request", "attach accept",
            "detach request", "service request", "service reject",
            "tau", "tracking area update",
            "pdn connectivity", "pdn connection",
            "ims registration", "ims de-registration",
            "sms over ims", "sms over sgs",
            "cause code", "emm cause", "esm cause",
            "data retry", "retry", "throttling",
            "band 13", "network access",
            "device management", "firmware update",
        ]
        for concept in concept_patterns:
            if concept in q_lower:
                concepts.append(concept)
        return concepts

    def _extract_mnos(self, q_lower: str) -> list[str]:
        """Extract MNO references (token-boundary match — see
        _MNO_ALIAS_PATTERNS; substring matching falsely scoped queries
        containing e.g. 'attach'/'pattern' to an MNO)."""
        mnos = []
        for pattern, mno in _MNO_ALIAS_PATTERNS:
            if mno not in mnos and pattern.search(q_lower):
                mnos.append(mno)
        return mnos

    def _extract_releases(self, query: str) -> list[str]:
        """Extract release references.

        Uses ``finditer`` (not ``search``) so a release-diff query that
        names two releases — e.g. "how did VZW eSIM change from Oct 2025
        to Feb 2026?" — captures BOTH, not just the first. Matches are
        order-preserving and deduplicated. The LLM analyzer handles
        multi-release natively; this keeps the keyword fallback
        consistent (multi-mno-sira D-DRAFT-9).
        """
        releases: list[str] = []
        for pattern, fixed in _RELEASE_PATTERNS:
            for m in pattern.finditer(query):
                val = fixed if fixed else m.group(0).strip()
                if val not in releases:
                    releases.append(val)
        return releases

    def _extract_standards(self, query: str) -> list[str]:
        """Extract 3GPP spec references."""
        refs = []
        for m in _SPEC_PATTERN.finditer(query):
            refs.append(f"3GPP TS {m.group(1)}")
        return refs

    def _extract_features(self, q_lower: str) -> list[str]:
        """Match query against feature keywords."""
        features = []
        for fid, keywords in _FEATURE_KEYWORDS.items():
            for kw in keywords:
                if kw in q_lower:
                    if fid not in features:
                        features.append(fid)
                    break
        return features

    def _extract_plan_ids(self, q_lower: str) -> list[str]:
        """Match query against known plan aliases."""
        plans = []
        for alias, pid in _PLAN_ALIASES.items():
            if alias in q_lower and pid not in plans:
                plans.append(pid)
        return plans

    def _classify_query_type(
        self,
        q_lower: str,
        mnos: list[str],
        releases: list[str],
        standards_refs: list[str],
    ) -> QueryType:
        """Classify the query type based on extracted signals."""
        # Cross-MNO comparison
        if len(mnos) >= 2 or "compare" in q_lower and len(mnos) >= 1:
            return QueryType.CROSS_MNO_COMPARISON

        # Fact intent — specific-value queries. Checked early so phrases
        # like "what is the maximum X" don't get caught by definitional
        # patterns. Triggers focus on quantitative shape; broad "what is"
        # alone isn't enough (handled by the acronym-pin path D-043 or
        # falls through to SINGLE_DOC / GENERAL).
        fact_phrases = (
            "value of", "what value", "what's the value",
            "default value", "default for",
            "how many", "how long",
            "maximum value", "minimum value",
            "max value", "min value",
            "exact value", "specific value",
            "what is the limit", "what's the limit",
            "what is the threshold", "what's the threshold",
        )
        if any(p in q_lower for p in fact_phrases):
            return QueryType.FACT

        # Summarize intent — explicit survey/summary phrasing. Checked
        # before the entity-/breadth-based heuristics because "explain
        # the X authentication requirements" should route to SUMMARIZE
        # (wide retrieval + per-section breakdown), not SINGLE_DOC
        # (narrow lookup) just because it names a plan. The user wants
        # the entire topic summarized, not one specific req.
        summarize_phrases = (
            "explain ", "summarize", "summary of", "summary for",
            "describe ", "give me an overview", "overview of",
            "tell me about ", "what's the overview",
        )
        if any(p in q_lower for p in summarize_phrases):
            return QueryType.SUMMARIZE

        # Standards comparison (check BEFORE release_diff — "differ" overlaps "diff")
        if standards_refs and any(
            w in q_lower for w in ["differ", "compare", "vs", "versus", "3gpp"]
        ):
            return QueryType.STANDARDS_COMPARISON

        # Release diff
        if len(releases) >= 2 or any(
            w in q_lower for w in ["delta", "diff", "changed", "what changed"]
        ):
            return QueryType.RELEASE_DIFF

        # Traceability — test-coverage queries OR lookup-by-attribute
        # ("which requirements mention <term>?" / "where is <term>
        # mentioned?"). The "mention" / "mentioned" phrasing signals
        # an entity-search even when the answer spans multiple plans;
        # routing to TRACEABILITY (with BM25 enabled) gives the
        # specific-term surfacing that pure-dense CROSS_DOC misses.
        if any(w in q_lower for w in [
            "test case", "test plan", "coverage", "traceability",
            "mention ", "mentions ", "mentioned ",
        ]):
            return QueryType.TRACEABILITY

        # Feature-level — concept-shaped queries about a named topic.
        # Most specific so checked first.
        if any(w in q_lower for w in ["everything about", "related to"]):
            return QueryType.FEATURE_LEVEL

        # Cross-doc / list-style: the user is asking for ALL/EVERY items
        # spanning multiple specs. Catches both:
        #   "What requirements exist for X across all VZW specs?"
        #   "What are all the X requirements?" (no plan named — answer
        #    spans whatever plans contain X)
        # These need wider retrieval than a within-plan lookup; the
        # caller (QueryPipeline) uses the query type to pick top_k.
        cross_doc_phrases = (
            "across all", "across the", "in all", "across vzw",
            "across mnos", "across plans", "across specs",
            "all the requirements", "all reqs",
            "what are all", "what requirements",
        )
        if any(p in q_lower for p in cross_doc_phrases):
            return QueryType.CROSS_DOC

        # Multi-plan name signal — two or more DISTINCT plans named.
        # Counting alias hits instead counted one plan twice whenever a
        # query used two of its own aliases: "band 13 network access
        # requirements" names the single plan LTEB13NAC through both of
        # its aliases and was routed to CROSS_DOC, which widens retrieval
        # and drops the BM25 weight for what is a within-plan lookup.
        # `_extract_plan_ids` already de-duplicates by plan id, so the
        # fix is to ask it rather than to count aliases.
        plan_ids = self._extract_plan_ids(q_lower)
        if len(plan_ids) >= 2:
            return QueryType.CROSS_DOC

        # Default: single doc if a plan is identified, general otherwise
        if plan_ids:
            return QueryType.SINGLE_DOC

        return QueryType.GENERAL

    def _classify_doc_scope(self, q_lower: str) -> DocTypeScope:
        """Determine which document types to include."""
        if any(w in q_lower for w in ["test case", "test plan"]):
            if any(w in q_lower for w in ["requirement", "req"]):
                return DocTypeScope.BOTH
            return DocTypeScope.TEST_CASES
        return DocTypeScope.REQUIREMENTS


class LLMQueryAnalyzer:
    """LLM-driven query analyzer.

    Uses the LLMProvider Protocol for more accurate extraction.
    Falls back to MockQueryAnalyzer for parsing failures.
    """

    def __init__(self, llm_provider) -> None:
        self._llm = llm_provider
        self._fallback = MockQueryAnalyzer()

    def analyze(self, query: str) -> QueryIntent:
        """Analyze a query using LLM for extraction."""
        system = (
            "You are a telecom requirements query analyzer. "
            "Extract structured intent from the user's query about "
            "MNO device requirement specifications."
        )

        type_options = ", ".join(f'"{t.value}"' for t in QueryType)

        prompt = f"""Analyze this query and extract structured intent as JSON:

Query: "{query}"

Return JSON with these fields:
- entities: list of named entities (requirement IDs like VZ_REQ_*, timer names like T3402, cause codes)
- concepts: list of telecom concepts mentioned (e.g., "attach reject", "IMS registration")
- mnos: list of MNO codes ("VZW", "TMO", "ATT") mentioned or implied
- releases: list of release references (e.g., "Feb 2026", "latest")
- query_type: one of {type_options}
- doc_type_scope: "requirements", "test_cases", or "both"
- standards_refs: list of 3GPP spec references (e.g., "3GPP TS 24.301")
- likely_features: list of feature IDs from: {list(_FEATURE_KEYWORDS.keys())}
- plan_ids: list of plan IDs from: LTEDATARETRY, LTESMS, LTEAT, LTEB13NAC, LTEOTADM

Return ONLY valid JSON, no other text."""

        try:
            response = self._llm.complete(prompt, system=system, temperature=0.0)
            # Extract JSON from response
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if not json_match:
                logger.warning("LLM response has no JSON — falling back to keyword analysis")
                return self._fallback.analyze(query)

            data = json.loads(json_match.group(0))
            return QueryIntent(
                raw_query=query,
                entities=data.get("entities", []),
                concepts=data.get("concepts", []),
                mnos=data.get("mnos", []),
                releases=data.get("releases", []),
                query_type=self._typed(
                    query, data.get("query_type", "general"),
                    QueryType, "query_type",
                ),
                doc_type_scope=self._typed(
                    query, data.get("doc_type_scope", "requirements"),
                    DocTypeScope, "doc_type_scope",
                ),
                standards_refs=data.get("standards_refs", []),
                likely_features=data.get("likely_features", []),
                plan_ids=data.get("plan_ids", []),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"LLM analysis failed ({e}) — falling back to keyword analysis")
            return self._fallback.analyze(query)

    def _typed(self, query: str, raw, enum_cls, field: str):
        """One enum field, with a fallback scoped to that field.

        Constructing the enum inline discarded the **whole** answer when
        any single typed field was unparseable: an LLM that returned a
        correct ``query_type`` alongside a ``doc_type_scope`` outside the
        enum lost both, and the run silently became rule-based. Measured
        on the phase-1 labelled set, where the model answered
        ``doc_type_scope: "general"`` on 1 of 50 queries and took a
        correct ``query_type`` down with it.

        Degrading per field is what `DecisionQueryAnalyzer._coerce` already
        does, and the reason is the same: the fields are independent, so
        one bad field is not evidence against the others.
        """
        try:
            return enum_cls(raw)
        except ValueError:
            value = getattr(self._fallback.analyze(query), field)
            logger.warning(
                "LLM returned %s=%r, which is not a valid %s — using the "
                "rule-based %s=%s and keeping the rest of the LLM's answer",
                field, raw, enum_cls.__name__, field, value.value,
            )
            return value


def make_query_analyzer(llm_provider=None, decision_provider=None):
    """Select the query analyzer by NORA's standard rule.

    Decision provider supplied → ``DecisionQueryAnalyzer`` (decision model
    for ``query_type`` / ``doc_type_scope``, regex for the free-text
    spans; self-falls-back to keyword analysis on a provider failure).
    LLM provider supplied → ``LLMQueryAnalyzer`` (more accurate
    extraction; handles oblique MNO references and multi-release
    release-diff queries, and self-falls-back to keyword analysis on a
    parse failure). Neither → ``MockQueryAnalyzer`` (keyword fallback,
    no model).

    This makes query analysis follow the same configured-model-or-mock
    posture as the rest of NORA rather than defaulting to keyword
    analysis even when a model is available (multi-mno-sira D-DRAFT-9).
    The decision path mirrors that posture rather than inventing a new
    one, and the fall-through is what keeps every existing caller — all
    of which pass a single positional argument — byte-identical
    (strand jev-experimentation, phase 2).

    Args:
        llm_provider: An object satisfying the ``LLMProvider`` Protocol
            (has ``complete()``), or ``None``.
        decision_provider: An object satisfying the ``DecisionProvider``
            Protocol (has ``decide()``), or ``None``. Takes precedence
            over ``llm_provider`` when both are supplied.

    Returns:
        A query analyzer with an ``analyze(query) -> QueryIntent`` method.
    """
    if decision_provider is not None:
        # Function-local: core.src.decision.analyzer imports
        # MockQueryAnalyzer from this module, so a module-level import
        # here would be circular.
        from core.src.decision.analyzer import DecisionQueryAnalyzer

        return DecisionQueryAnalyzer(decision_provider)
    if llm_provider is not None:
        return LLMQueryAnalyzer(llm_provider)
    return MockQueryAnalyzer()


def make_query_analyzer_from_knob(
    llm_provider=None,
    decision_provider=None,
    cli_value=None,
):
    """Select the query analyzer from ``NORA_QUERY_ANALYZER``.

    The knob is four-valued — ``rules`` / ``llm`` / ``decision`` /
    ``confidence-routed`` — and defaults to ``rules``, so a caller that
    adopts this function in place of ``MockQueryAnalyzer()`` changes
    nothing for an operator who sets no knob. That is the phase-2
    identity gate.

    ``decision`` and ``confidence-routed`` share a provider and differ
    only in policy: the first keeps every answer the model returns, the
    second keeps an answer only where its calibrated confidence clears
    ``NORA_DECISION_CONFIDENCE_FLOOR`` and lets the rules serve the rest.

    Degrades to ``rules`` rather than raising whenever the selected arm
    has no usable provider: the sira lane has no ``LLMProvider`` wired
    yet, and the ``systemone`` HTTP provider does not land until phase 3.
    A misconfigured classifier must not take the query path down.

    Args:
        llm_provider: Used when the knob says ``llm``.
        decision_provider: Used when the knob says ``decision`` or
            ``confidence-routed``; a ``MockDecisionProvider`` is built
            when either is selected and none is supplied.
        cli_value: Explicit override, highest precedence.

    Returns:
        A query analyzer with an ``analyze(query) -> QueryIntent`` method.
    """
    from core.src.env.config import (
        resolve_decision_confidence_floor,
        resolve_decision_provider,
        resolve_query_analyzer,
    )

    mode = resolve_query_analyzer(cli_value=cli_value)

    if mode == "llm":
        if llm_provider is None:
            logger.warning(
                "NORA_QUERY_ANALYZER=llm but no LLMProvider was supplied "
                "— falling back to rule-based classification"
            )
            return MockQueryAnalyzer()
        return make_query_analyzer(llm_provider=llm_provider)

    if mode in ("decision", "confidence-routed"):
        if decision_provider is None:
            decision_provider = _build_decision_provider(
                resolve_decision_provider()
            )
        if decision_provider is None:
            return MockQueryAnalyzer()
        if mode == "confidence-routed":
            # Function-local for the same circular-import reason as
            # make_query_analyzer's.
            from core.src.decision.analyzer import (
                ConfidenceRoutedQueryAnalyzer,
            )

            return ConfidenceRoutedQueryAnalyzer(
                decision_provider, resolve_decision_confidence_floor()
            )
        return make_query_analyzer(decision_provider=decision_provider)

    return MockQueryAnalyzer()


def _build_decision_provider(name: str):
    """Construct a decision provider by name, or ``None`` when it cannot
    be built.

    ``None`` rather than an exception: the caller degrades to rule-based
    classification, because a misconfigured classifier must not take the
    query path down.
    """
    if name == "mock":
        from core.src.decision.mock_provider import MockDecisionProvider

        return MockDecisionProvider()

    if name == "systemone":
        from core.src.decision.base import DecisionError
        from core.src.decision.systemone_provider import (
            SystemOneDecisionProvider,
        )
        from core.src.env.config import (
            resolve_decision_api_key,
            resolve_decision_base_url,
            resolve_decision_model,
            resolve_decision_timeout,
        )

        try:
            return SystemOneDecisionProvider(
                base_url=resolve_decision_base_url(),
                model=resolve_decision_model(),
                api_key=resolve_decision_api_key(),
                timeout=resolve_decision_timeout(),
            )
        except DecisionError as e:
            logger.warning(
                "Could not build the systemone decision provider (%s) — "
                "falling back to rule-based classification", e,
            )
            return None

    logger.warning(
        "NORA_DECISION_PROVIDER=%s is not available — falling back to "
        "rule-based classification", name,
    )
    return None
