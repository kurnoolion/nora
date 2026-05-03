"""Evaluation test question set with ground truth (TDD 9.4).

18 questions across 5 categories:
  - Single-doc factual (4)
  - Cross-doc dependency (4)
  - Feature-level (4)
  - Standards comparison (3)
  - Traceability / entity lookup (3)

Each question has:
  - Ground truth: expected plan IDs, expected req IDs (subset),
    expected features, expected standards refs
  - Evaluation hints for scoring
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GroundTruth:
    """Expected results for a test question."""

    # Plans that MUST appear in the results
    expected_plans: list[str] = field(default_factory=list)

    # Requirement IDs that SHOULD appear (subset — not exhaustive)
    expected_req_ids: list[str] = field(default_factory=list)

    # Features that should be relevant
    expected_features: list[str] = field(default_factory=list)

    # Standards specs that should be referenced
    expected_standards: list[str] = field(default_factory=list)

    # Minimum number of distinct plans in results (for cross-doc)
    min_plans: int = 1

    # Minimum number of relevant chunks expected
    min_chunks: int = 1

    # Key concepts that should appear in the answer
    expected_concepts: list[str] = field(default_factory=list)


@dataclass
class EvalQuestion:
    """A test question for evaluation."""

    id: str
    category: str
    question: str
    ground_truth: GroundTruth
    description: str = ""


# ─── Category 1: Single-doc factual (4 questions) ──────────────────

Q_SINGLE_01 = EvalQuestion(
    id="single_01",
    category="single_doc",
    question="What is the T3402 timer behavior in VZW data retry?",
    description="Timer T3402 is specific to LTEDATARETRY plan",
    ground_truth=GroundTruth(
        expected_plans=["LTEDATARETRY"],
        expected_req_ids=[
            "VZ_REQ_LTEDATARETRY_2377",   # TIMER T3402 section
            "VZ_REQ_LTEDATARETRY_7743",   # Actual Timer T3402 timer behavior requirement
        ],
        expected_features=["TIMER_MANAGEMENT"],
        expected_standards=["3GPP TS 24.301"],
        expected_concepts=["t3402", "plmn"],
    ),
)

Q_SINGLE_02 = EvalQuestion(
    id="single_02",
    category="single_doc",
    question="What is the generic throttling algorithm for data retry?",
    description="Throttling algorithm defined in LTEDATARETRY section 1.3.3",
    ground_truth=GroundTruth(
        expected_plans=["LTEDATARETRY"],
        expected_req_ids=[
            "VZ_REQ_LTEDATARETRY_2376",  # GENERIC THROTTLING ALGORITHM
            "VZ_REQ_LTEDATARETRY_7732",  # Generic Throttling Algorithm detail
            "VZ_REQ_LTEDATARETRY_23757",  # GENERAL RULES
            "VZ_REQ_LTEDATARETRY_7733",  # General Rules 
            "VZ_REQ_LTEDATARETRY_23758",  # ALGORITHM DETAILS
            "VZ_REQ_LTEDATARETRY_7734",  # Algorithm Details - Notes
            "VZ_REQ_LTEDATARETRY_7735",  # Algorithm Details
            "VZ_REQ_LTEDATARETRY_23759",  # PER SYSTEM NATURE OF THROTTLING
        ],
        expected_features=["DATA_RETRY"],
        expected_concepts=["throttl"],
    ),
)

Q_SINGLE_03 = EvalQuestion(
    id="single_03",
    category="single_doc",
    question="What AT commands must VZW LTE devices support?",
    description="AT command interface requirements in LTEAT",
    ground_truth=GroundTruth(
        expected_plans=["LTEAT"],
        expected_req_ids=[
            "VZ_REQ_LTEAT_21030",   # LTE devices shall support an interface capable of interpreting AT commands 
            "VZ_REQ_LTEAT_21032",   # LTE devices shall support all AT commands listed in sections 3.1.2 and 3.1.3. 
            "VZ_REQ_LTEAT_21033",   # In addition, LTE devices should support all mandatory and optional AT commands pertaining to the devices end us
            "VZ_REQ_LTEAT_21034",   # Additional AT Commands Required All Device TypesStandards ReferenceDescriptionAT Command3GPP TS 27.0 
            "VZ_REQ_LTEAT_21035",   # Additional AT Commands Required Handset Devices OnlyStandards ReferenceDescriptionAT Command3GPP TS 
        ],
        expected_features=["AT_COMMANDS"],
        expected_standards=["3GPP TS 27.007"],
    ),
)

Q_SINGLE_04 = EvalQuestion(
    id="single_04",
    category="single_doc",
    question="What are the FOTA requirements for VZW OTA device management?",
    description="Firmware Over The Air in LTEOTADM",
    ground_truth=GroundTruth(
        expected_plans=["LTEOTADM"],
        expected_req_ids=[
            "VZ_REQ_LTEOTADM_37788",  # Firmware Over The Air (FOTA)
        ],
        expected_features=["OTA_DM"],
        expected_concepts=["firmware"],
    ),
)

# ─── Category 2: Cross-doc dependency (4 questions) ────────────────

Q_CROSS_01 = EvalQuestion(
    id="cross_01",
    category="cross_doc",
    question="What are all the SMS over IMS requirements?",
    description="SMS over IMS spans LTESMS (primary) and LTEB13NAC (referenced)",
    ground_truth=GroundTruth(
        expected_plans=["LTESMS", "LTEB13NAC"],
        expected_req_ids=[
            "VZ_REQ_LTESMS_30258",     # SMS OVER IMS - OVERVIEW
            "VZ_REQ_LTESMS_29575",    # SMS over IMS - overview
            "VZ_REQ_LTEB13NAC_6336",    # SMS over IMS Support
            "VZ_REQ_LTESMS_29576",    # MO SMS
            "VZ_REQ_LTESMS_29578",    # MT SMS
            "VZ_REQ_LTESMS_4105999311951475",    # TEXT-TO-988
        ],
        expected_features=["SMS"],
        min_plans=2,
        expected_concepts=["sms over ims"],
    ),
)

Q_CROSS_02 = EvalQuestion(
    id="cross_02",
    category="cross_doc",
    question="What are the PDN connectivity requirements?",
    description="PDN connectivity spans LTEB13NAC and LTEDATARETRY",
    ground_truth=GroundTruth(
        expected_plans=["LTEB13NAC", "LTEDATARETRY"],
        expected_req_ids=[
            "VZ_REQ_LTEB13NAC_22715",        # PDN SUPPORT 
            "VZ_REQ_LTEB13NAC_6308",        # UE PDN SUPPORT 
            "VZ_REQ_LTEB13NAC_6309",        # UE BEARER AND PDN SUPPORT 
            "VZ_REQ_LTEDATARETRY_7765",        # If the UE receives a 'PDN CONNECTIVITY REJECT' message after sending a 'PDN CONNE
            "VZ_REQ_LTEDATARETRY_7766",        #  Non-IMS PDN
        ],
        expected_features=["BEARER_MANAGEMENT"],
        min_plans=2,
        expected_concepts=["pdn connect"],
    ),
)

Q_CROSS_03 = EvalQuestion(
    id="cross_03",
    category="cross_doc",
    question=(
        "What application directed SMS "
        "and what mode it will not supported"
    ),
    description="Application directed behavior inin LTESMS + LTEB13NAC , cross-doc relationship",
    ground_truth=GroundTruth(
        expected_plans=["LTESMS", "LTEB13NAC"],
        expected_req_ids=[
            "VZ_REQ_LTESMS_30314",   # APPLICATION DIRECTED SMS
            "VZ_REQ_LTESMS_29571",   # When a MT SMS message arrives via the SMS over IMS method, the device shall remove the SIP headers and decode th
            "VZ_REQ_LTESMS_29572",   # If the device receives a MO SMS from the UICC, the device shall accept the 3GPP formatted SMS message from the UE
            "VZ_REQ_LTEB13NAC_6443",   # MSISDN and MSISDN-based SIP URI Validity 
        ],
        expected_features=["IMS_REGISTRATION", "SMS"],
        min_plans=2,
        expected_concepts=["sms over ims"],
    ),
)

Q_CROSS_04 = EvalQuestion(
    id="cross_04",
    category="cross_doc",
    question=(
        "What requirements exist for network detach handling "
        "across all VZW specs?"
    ),
    description="Detach spans LTEB13NAC and LTEDATARETRY",
    ground_truth=GroundTruth(
        expected_plans=["LTEB13NAC", "LTEDATARETRY"],
        expected_req_ids=[
            "VZ_REQ_LTEB13NAC_23586",       # LTE NETWORK DETACHMENT
            # _23850 was the prior expected hit but its content is
            # ATTACH retry ("ATTACH ATTEMPT COUNTER REACHES A VALUE
            # OF 5"), not detach — mis-categorized in the original
            # ground-truth curation. Replaced with the substantive
            # detach-content req at sec 1.4.3.3.1.1.
            "VZ_REQ_LTEB13NAC_6374",     # LTE NETWORK DETACHMENT
            "VZ_REQ_LTEDATARETRY_7760",     # RE-ATTACH NOT REQUIRED AND EMM CAUSE CODE
            "VZ_REQ_LTEDATARETRY_23853",     # 
            "VZ_REQ_LTEDATARETRY_7759",     #
        ],
        expected_features=["EPS_MOBILITY"],
        min_plans=2,
        expected_concepts=["detach"],
    ),
)

# ─── Category 3: Feature-level (4 questions) ───────────────────────

Q_FEATURE_01 = EvalQuestion(
    id="feature_01",
    category="feature_level",
    question="What are all the requirements related to data retry?",
    description="DATA_RETRY feature: primary in LTEDATARETRY, LTEB13NAC, LTEOTADM",
    ground_truth=GroundTruth(
        expected_plans=["LTEDATARETRY", "LTEB13NAC"],
        expected_features=["DATA_RETRY"],
        min_plans=2,
        min_chunks=3,
        expected_concepts=["retry"],
    ),
)

Q_FEATURE_02 = EvalQuestion(
    id="feature_02",
    category="feature_level",
    question="What are all the requirements related to error handling?",
    description="ERROR_HANDLING feature spans 4 plans",
    ground_truth=GroundTruth(
        expected_plans=["LTEB13NAC", "LTEDATARETRY"],
        expected_features=["ERROR_HANDLING"],
        min_plans=2,
        min_chunks=3,
        expected_concepts=["error", "cause code"],
    ),
)

Q_FEATURE_03 = EvalQuestion(
    id="feature_03",
    category="feature_level",
    question="What are all the bearer management requirements?",
    description="BEARER_MANAGEMENT: primary in LTEB13NAC, LTEDATARETRY",
    ground_truth=GroundTruth(
        expected_plans=["LTEB13NAC", "LTEDATARETRY"],
        expected_features=["BEARER_MANAGEMENT"],
        min_plans=2,
        expected_concepts=["bearer"],
    ),
)

Q_FEATURE_04 = EvalQuestion(
    id="feature_04",
    category="feature_level",
    question="What are all the PLMN selection requirements?",
    description="PLMN_SELECTION referenced in LTEB13NAC and LTEDATARETRY",
    ground_truth=GroundTruth(
        expected_plans=["LTEB13NAC", "LTEDATARETRY"],
        expected_features=["PLMN_SELECTION"],
        min_plans=2,
        expected_concepts=["plmn"],
    ),
)

# ─── Category 4: Standards comparison (3 questions) ─────────────────

Q_STANDARDS_01 = EvalQuestion(
    id="standards_01",
    category="standards_comparison",
    question="How does VZW T3402 differ from 3GPP TS 24.301?",
    description="T3402 timer behavior vs 3GPP standard",
    ground_truth=GroundTruth(
        expected_plans=["LTEDATARETRY"],
        expected_req_ids=["VZ_REQ_LTEDATARETRY_2377"],
        expected_features=["TIMER_MANAGEMENT"],
        expected_standards=["3GPP TS 24.301"],
        expected_concepts=["t3402"],
    ),
)

Q_STANDARDS_02 = EvalQuestion(
    id="standards_02",
    category="standards_comparison",
    question=(
        "What 3GPP TS 24.301 sections are referenced "
        "in the attach reject handling?"
    ),
    description="Attach reject in LTEDATARETRY references 3GPP 24.301 sections",
    ground_truth=GroundTruth(
        expected_plans=["LTEDATARETRY"],
        expected_req_ids=[
            "VZ_REQ_LTEDATARETRY_23802",
            "VZ_REQ_LTEDATARETRY_7754",
        ],
        expected_standards=["3GPP TS 24.301"],
        expected_concepts=["attach reject"],
    ),
)

Q_STANDARDS_03 = EvalQuestion(
    id="standards_03",
    category="standards_comparison",
    question=(
        "Which VZW requirements reference 3GPP TS 36.331?"
    ),
    description="TS 36.331 is referenced primarily in LTEB13NAC",
    ground_truth=GroundTruth(
        expected_plans=["LTEB13NAC"],
        expected_standards=["3GPP TS 36.331"],
    ),
)

# ─── Category 5: Traceability / entity lookup (3 questions) ────────

Q_TRACE_01 = EvalQuestion(
    id="trace_01",
    category="traceability",
    question="What is requirement VZ_REQ_LTEDATARETRY_7754?",
    description="Direct entity lookup by requirement ID",
    ground_truth=GroundTruth(
        expected_plans=["LTEDATARETRY"],
        expected_req_ids=["VZ_REQ_LTEDATARETRY_7754"],
        min_chunks=1,
        expected_concepts=["attach reject", "cause code"],
    ),
)

Q_TRACE_02 = EvalQuestion(
    id="trace_02",
    category="traceability",
    question="What are the requirements related to IMS registration throttling?",
    description="IMS registration throttling in LTEB13NAC section 1.3.2.10.6",
    ground_truth=GroundTruth(
        expected_plans=["LTEB13NAC"],
        expected_req_ids=[
            "VZ_REQ_LTEB13NAC_6454",   # IMS Registration Timer Expires while Throttling
            # _23532 (sec 1.3.2.10.6.9 throttling-across-system-
            # transitions parent) was the prior expected hit but is
            # struck-through in source — parser correctly drops it.
            # Replaced with the live equivalent at the next subsection.
            "VZ_REQ_LTEB13NAC_6455",   # IMS Reg/Re-Reg Throttling Across System Transitions
        ],
        expected_features=["IMS_REGISTRATION"],
        expected_concepts=["ims registr", "throttl"],
    ),
)

Q_TRACE_03 = EvalQuestion(
    id="trace_03",
    category="traceability",
    question=(
        "What requirements mention cause code 22 in VZW specifications?"
    ),
    description="Cause code 22 entity lookup across plans",
    ground_truth=GroundTruth(
        expected_plans=["LTEDATARETRY"],
        expected_req_ids=[
            # _7754 was the prior expected hit but its content is
            # about cause code 42 ("'42: Severe Network Failure'"),
            # not 22 — mis-curation. The corpus has 5 reqs that
            # actually mention cause code 22; the canonical pair
            # below covers the two distinct NAS rejection paths
            # (ATTACH vs TAU) where cause 22 fires.
            "VZ_REQ_LTEDATARETRY_7795",   # ATTACH REJECT cause code 22 (sec 1.4.3.1.1.10)
            "VZ_REQ_LTEDATARETRY_7761",   # TAU REJECT cause code 22 (sec 1.4.3.4.1.1)
        ],
        expected_concepts=["cause code"],
    ),
)


# ─── All questions ──────────────────────────────────────────────────

ALL_QUESTIONS: list[EvalQuestion] = [
    # Single-doc (4)
    Q_SINGLE_01, Q_SINGLE_02, Q_SINGLE_03, Q_SINGLE_04,
    # Cross-doc (4)
    Q_CROSS_01, Q_CROSS_02, Q_CROSS_03, Q_CROSS_04,
    # Feature-level (4)
    Q_FEATURE_01, Q_FEATURE_02, Q_FEATURE_03, Q_FEATURE_04,
    # Standards comparison (3)
    Q_STANDARDS_01, Q_STANDARDS_02, Q_STANDARDS_03,
    # Traceability (3)
    Q_TRACE_01, Q_TRACE_02, Q_TRACE_03,
]

QUESTIONS_BY_CATEGORY: dict[str, list[EvalQuestion]] = {}
for q in ALL_QUESTIONS:
    QUESTIONS_BY_CATEGORY.setdefault(q.category, []).append(q)
