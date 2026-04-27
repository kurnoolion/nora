"""Mock LLM provider for testing without API keys.

Produces deterministic, keyword-based feature extractions from
requirement document headings. Results are plausible enough to
exercise the full taxonomy pipeline and verify output structure.

Replace with a real provider (Anthropic, OpenAI, or internal) for
production-quality feature extraction.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# Telecom feature keywords → feature definitions
# Each entry: (keywords_to_match, feature_id, feature_name, description, keywords_list)
_FEATURE_CATALOG = [
    (
        ["data retry", "retry", "data connection", "pdn connectivity"],
        "DATA_RETRY", "LTE Data Retry",
        "Data connection retry logic including timer management and throttling",
        ["data retry", "PDN", "throttle", "timer", "ESM", "EMM"],
    ),
    (
        ["sms", "short message", "messaging"],
        "SMS", "SMS over LTE",
        "Short message service procedures over LTE including MO/MT SMS and SMS over IMS",
        ["SMS", "MO-SMS", "MT-SMS", "SMS over IMS", "SMS over SGs"],
    ),
    (
        ["band 13", "b13", "nac", "network access", "band selection"],
        "BAND_SELECTION", "LTE Band Selection and NAC",
        "Band selection and network access control for LTE bands",
        ["Band 13", "NAC", "band selection", "frequency", "EARFCN"],
    ),
    (
        ["ota", "over-the-air", "device management", "otadm", "fota"],
        "OTA_DM", "Over-The-Air Device Management",
        "OTA device management including firmware updates and configuration",
        ["OTA", "FOTA", "device management", "OMA-DM", "firmware"],
    ),
    (
        ["at command", "at+", "modem"],
        "AT_COMMANDS", "AT Command Interface",
        "AT command interface for modem control and device management",
        ["AT command", "modem", "serial", "AT+"],
    ),
    (
        ["ims", "volte", "voice", "sip", "registration"],
        "IMS_REGISTRATION", "IMS Registration",
        "IMS network registration including initial, re-registration, and de-registration",
        ["IMS", "SIP", "P-CSCF", "REGISTER", "VoLTE"],
    ),
    (
        ["attach", "detach", "eps", "emm", "mobility"],
        "EPS_MOBILITY", "EPS Mobility Management",
        "EPS attach, detach, and mobility management procedures",
        ["EMM", "attach", "detach", "TAU", "EPS"],
    ),
    (
        ["bearer", "qos", "qci", "eps bearer"],
        "BEARER_MANAGEMENT", "EPS Bearer Management",
        "Default and dedicated EPS bearer establishment, modification, and release",
        ["bearer", "QoS", "QCI", "ESM", "PDN"],
    ),
    (
        ["timer", "t3", "backoff"],
        "TIMER_MANAGEMENT", "Timer Management",
        "Protocol timer management including NAS timers and backoff timers",
        ["timer", "T3402", "T3411", "T3412", "backoff"],
    ),
    (
        ["sim", "uicc", "euicc", "isim", "usim"],
        "SIM_MANAGEMENT", "SIM/UICC Management",
        "SIM, UICC, and eSIM management including profile provisioning",
        ["SIM", "UICC", "eUICC", "ISIM", "USIM", "eSIM"],
    ),
    (
        ["antenna", "mimo", "rf", "radio"],
        "RF_ANTENNA", "RF and Antenna",
        "RF requirements and antenna specifications including MIMO",
        ["antenna", "MIMO", "RF", "radio", "diversity"],
    ),
    (
        ["plmn", "network selection", "roaming"],
        "PLMN_SELECTION", "PLMN Selection",
        "PLMN selection and network registration procedures",
        ["PLMN", "network selection", "roaming", "HPLMN", "EHPLMN"],
    ),
    (
        ["handover", "handoff", "reselection", "cell"],
        "HANDOVER", "Handover and Cell Reselection",
        "Inter-frequency, inter-RAT handover and cell reselection procedures",
        ["handover", "reselection", "inter-RAT", "IRAT", "cell"],
    ),
    (
        ["apn", "access point", "provisioning"],
        "APN_PROVISIONING", "APN Provisioning",
        "Access Point Name configuration and provisioning",
        ["APN", "access point", "provisioning", "PDN"],
    ),
    (
        ["error", "reject", "cause code", "failure"],
        "ERROR_HANDLING", "Error and Reject Handling",
        "NAS error handling, reject cause codes, and failure recovery",
        ["error", "reject", "cause code", "EMM cause", "ESM cause"],
    ),
    (
        ["test", "compliance", "certification"],
        "TEST_COMPLIANCE", "Test and Compliance",
        "Device compliance testing and certification requirements",
        ["test", "compliance", "certification", "test plan"],
    ),
]


class MockLLMProvider:
    """Mock LLM provider that extracts features using keyword matching.

    Satisfies the LLMProvider protocol. Produces deterministic results
    by matching section headings against a catalog of known telecom features.
    """

    def __init__(self):
        self._call_count = 0

    def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        self._call_count += 1
        logger.debug(f"MockLLMProvider call #{self._call_count} ({len(prompt)} chars)")

        # Detect what kind of extraction is being requested
        if "table of contents" in prompt.lower() or "section headings" in prompt.lower():
            return self._extract_features(prompt)
        if "consolidat" in prompt.lower() or "unif" in prompt.lower():
            return self._consolidate_features(prompt)

        return json.dumps({"error": "unrecognized prompt type"})

    def _extract_features(self, prompt: str) -> str:
        """Match section headings against feature catalog."""
        prompt_lower = prompt.lower()

        primary = []
        referenced = []
        concepts = []

        for keywords, feat_id, name, desc, kw_list in _FEATURE_CATALOG:
            match_count = sum(1 for kw in keywords if kw in prompt_lower)
            if match_count > 0:
                entry = {
                    "feature_id": feat_id,
                    "name": name,
                    "description": desc,
                    "keywords": kw_list,
                    "confidence": min(0.5 + match_count * 0.15, 0.95),
                }
                # Primary if multiple keyword matches, referenced if just one
                if match_count >= 2:
                    primary.append(entry)
                else:
                    referenced.append(entry)

                concepts.extend(kw_list[:2])

        # If nothing matched as primary, promote the best referenced
        if not primary and referenced:
            primary.append(referenced.pop(0))

        return json.dumps({
            "primary_features": primary,
            "referenced_features": referenced[:5],
            "key_concepts": sorted(set(concepts))[:15],
        }, indent=2)

    def _consolidate_features(self, prompt: str) -> str:
        """Pass through — consolidation is done algorithmically, not by LLM."""
        return json.dumps({"note": "consolidation handled algorithmically"})

    @property
    def call_count(self) -> int:
        return self._call_count
