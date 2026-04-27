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
            "VZ_REQ_LTEDATARETRY_7742",   # TIMER T3402 section
            "VZ_REQ_LTEDATARETRY_2377",   # T3402 on a PLMN basis
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
            "VZ_REQ_LTEDATARETRY_7731",  # GENERIC THROTTLING ALGORITHM
            "VZ_REQ_LTEDATARETRY_2376",  # Generic Throttling Algorithm detail
            "VZ_REQ_LTEDATARETRY_7735",  # PER SYSTEM NATURE OF THROTTLING
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
            "VZ_REQ_LTEAT_33081",   # SOFTWARE SPECIFICATIONS
            "VZ_REQ_LTEAT_21030",   # AT interface disabled by default
            "VZ_REQ_LTEAT_21031",   # support all AT commands
        ],
        expected_features=["AT_COMMANDS"],
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
            "VZ_REQ_LTESMS_30284",     # SMS OVER IMS
            "VZ_REQ_LTEB13NAC_23507",  # SMS over IMS Support
        ],
        expected_features=["SMS"],
        min_plans=2,
        expected_concepts=["sms over ims"],
    ),
)

Q_CROSS_02 = EvalQuestion(
    id="cross_02",
    category="cross_doc",
    question="What are the PDN connectivity requirements across all VZW plans?",
    description="PDN connectivity spans LTEB13NAC and LTEDATARETRY",
    ground_truth=GroundTruth(
        expected_plans=["LTEB13NAC", "LTEDATARETRY"],
        expected_req_ids=[
            "VZ_REQ_LTEB13NAC_6309",        # PDN CONNECTIONS
            "VZ_REQ_LTEDATARETRY_23892",     # PDN CONNECTIVITY REQUEST
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
        "What are the IMS registration requirements and "
        "how do they relate to SMS?"
    ),
    description="IMS registration in LTEB13NAC + LTESMS, cross-doc relationship",
    ground_truth=GroundTruth(
        expected_plans=["LTEB13NAC", "LTESMS"],
        expected_req_ids=[
            "VZ_REQ_LTEB13NAC_23513",  # IMS REGISTRATION REQUIREMENTS
            "VZ_REQ_LTESMS_30259",     # IMS REGISTRATION (in SMS context)
        ],
        expected_features=["IMS_REGISTRATION", "SMS"],
        min_plans=2,
        expected_concepts=["ims registr"],
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
            "VZ_REQ_LTEB13NAC_6373",       # LTE NETWORK DETACHMENT
            "VZ_REQ_LTEDATARETRY_23850",    # DETACH REQUEST
            "VZ_REQ_LTEDATARETRY_7758",     # DETACH REQUEST ON UE POWER DOWN
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
            "VZ_REQ_LTEB13NAC_23532",  # IMS REGISTRATION THROTTLING
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
            "VZ_REQ_LTEDATARETRY_7754",  # Attach reject cause code 22
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
