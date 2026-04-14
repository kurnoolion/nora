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

from src.query.schema import (
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
        """Extract MNO references."""
        mnos = []
        for alias, mno in _MNO_ALIASES.items():
            if alias in q_lower and mno not in mnos:
                mnos.append(mno)
        return mnos

    def _extract_releases(self, query: str) -> list[str]:
        """Extract release references."""
        releases = []
        for pattern, fixed in _RELEASE_PATTERNS:
            m = pattern.search(query)
            if m:
                if fixed:
                    releases.append(fixed)
                else:
                    releases.append(m.group(0).strip())
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

        # Traceability
        if any(w in q_lower for w in ["test case", "test plan", "coverage", "traceability"]):
            return QueryType.TRACEABILITY

        # Feature-level
        if any(w in q_lower for w in ["all requirements", "all reqs", "everything about", "related to"]):
            return QueryType.FEATURE_LEVEL

        # Cross-doc (multi-plan keywords)
        cross_doc_signals = 0
        for alias in _PLAN_ALIASES:
            if alias in q_lower:
                cross_doc_signals += 1
        if cross_doc_signals >= 2:
            return QueryType.CROSS_DOC

        # Default: single doc if a plan is identified, general otherwise
        if self._extract_plan_ids(q_lower):
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

        prompt = f"""Analyze this query and extract structured intent as JSON:

Query: "{query}"

Return JSON with these fields:
- entities: list of named entities (requirement IDs like VZ_REQ_*, timer names like T3402, cause codes)
- concepts: list of telecom concepts mentioned (e.g., "attach reject", "IMS registration")
- mnos: list of MNO codes ("VZW", "TMO", "ATT") mentioned or implied
- releases: list of release references (e.g., "Feb 2026", "latest")
- query_type: one of "single_doc", "cross_doc", "cross_mno_comparison", "release_diff", "standards_comparison", "traceability", "feature_level", "general"
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
                query_type=QueryType(data.get("query_type", "general")),
                doc_type_scope=DocTypeScope(data.get("doc_type_scope", "requirements")),
                standards_refs=data.get("standards_refs", []),
                likely_features=data.get("likely_features", []),
                plan_ids=data.get("plan_ids", []),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"LLM analysis failed ({e}) — falling back to keyword analysis")
            return self._fallback.analyze(query)
