"""Evaluation metrics for the query pipeline (TDD 9.4).

Computes:
  - Completeness: Did the answer cover all expected plans/documents?
  - Accuracy: Are cited requirement IDs correct?
  - Citation quality: Do answers include citations?
  - Standards integration: Is 3GPP context incorporated?
  - No hallucination: No fabricated requirement IDs

Each metric returns a score in [0.0, 1.0].
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.eval.questions import GroundTruth, EvalQuestion
from src.query.schema import QueryResponse


@dataclass
class QuestionScore:
    """Scores for a single question."""

    question_id: str
    category: str
    question: str

    # Individual metric scores (0.0 - 1.0)
    completeness: float = 0.0
    accuracy: float = 0.0
    citation_quality: float = 0.0
    standards_integration: float = 0.0
    hallucination_free: float = 1.0

    # Detail fields
    expected_plans: list[str] = field(default_factory=list)
    found_plans: list[str] = field(default_factory=list)
    expected_req_ids: list[str] = field(default_factory=list)
    found_req_ids: list[str] = field(default_factory=list)
    hallucinated_req_ids: list[str] = field(default_factory=list)
    expected_standards: list[str] = field(default_factory=list)
    found_standards: list[str] = field(default_factory=list)
    retrieved_count: int = 0
    citation_count: int = 0

    @property
    def overall(self) -> float:
        """Weighted average of all metrics."""
        weights = {
            "completeness": 0.30,
            "accuracy": 0.25,
            "citation_quality": 0.20,
            "standards_integration": 0.15,
            "hallucination_free": 0.10,
        }
        return (
            self.completeness * weights["completeness"]
            + self.accuracy * weights["accuracy"]
            + self.citation_quality * weights["citation_quality"]
            + self.standards_integration * weights["standards_integration"]
            + self.hallucination_free * weights["hallucination_free"]
        )

    def to_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "category": self.category,
            "question": self.question,
            "scores": {
                "completeness": round(self.completeness, 3),
                "accuracy": round(self.accuracy, 3),
                "citation_quality": round(self.citation_quality, 3),
                "standards_integration": round(self.standards_integration, 3),
                "hallucination_free": round(self.hallucination_free, 3),
                "overall": round(self.overall, 3),
            },
            "details": {
                "expected_plans": self.expected_plans,
                "found_plans": self.found_plans,
                "expected_req_ids": self.expected_req_ids,
                "found_req_ids": self.found_req_ids,
                "hallucinated_req_ids": self.hallucinated_req_ids,
                "expected_standards": self.expected_standards,
                "found_standards": self.found_standards,
                "retrieved_count": self.retrieved_count,
                "citation_count": self.citation_count,
            },
        }


@dataclass
class EvalReport:
    """Aggregate evaluation report."""

    scores: list[QuestionScore] = field(default_factory=list)
    mode: str = "graph_scoped"  # or "pure_rag"

    @property
    def avg_completeness(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.completeness for s in self.scores) / len(self.scores)

    @property
    def avg_accuracy(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.accuracy for s in self.scores) / len(self.scores)

    @property
    def avg_citation_quality(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.citation_quality for s in self.scores) / len(self.scores)

    @property
    def avg_standards_integration(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.standards_integration for s in self.scores) / len(self.scores)

    @property
    def avg_hallucination_free(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.hallucination_free for s in self.scores) / len(self.scores)

    @property
    def avg_overall(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.overall for s in self.scores) / len(self.scores)

    def by_category(self) -> dict[str, list[QuestionScore]]:
        result: dict[str, list[QuestionScore]] = {}
        for s in self.scores:
            result.setdefault(s.category, []).append(s)
        return result

    def category_averages(self) -> dict[str, dict[str, float]]:
        """Average scores per category."""
        result = {}
        for cat, cat_scores in self.by_category().items():
            n = len(cat_scores)
            result[cat] = {
                "completeness": sum(s.completeness for s in cat_scores) / n,
                "accuracy": sum(s.accuracy for s in cat_scores) / n,
                "citation_quality": sum(s.citation_quality for s in cat_scores) / n,
                "standards_integration": sum(s.standards_integration for s in cat_scores) / n,
                "hallucination_free": sum(s.hallucination_free for s in cat_scores) / n,
                "overall": sum(s.overall for s in cat_scores) / n,
                "count": n,
            }
        return result

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "summary": {
                "total_questions": len(self.scores),
                "avg_completeness": round(self.avg_completeness, 3),
                "avg_accuracy": round(self.avg_accuracy, 3),
                "avg_citation_quality": round(self.avg_citation_quality, 3),
                "avg_standards_integration": round(self.avg_standards_integration, 3),
                "avg_hallucination_free": round(self.avg_hallucination_free, 3),
                "avg_overall": round(self.avg_overall, 3),
            },
            "by_category": {
                cat: {k: round(v, 3) if isinstance(v, float) else v
                      for k, v in avgs.items()}
                for cat, avgs in self.category_averages().items()
            },
            "questions": [s.to_dict() for s in self.scores],
        }


import re

# Pattern for valid VZW requirement IDs
_REQ_ID_PATTERN = re.compile(r"VZ_REQ_\w+_\d+")


def score_question(
    question: EvalQuestion,
    response: QueryResponse,
) -> QuestionScore:
    """Score a pipeline response against ground truth."""
    gt = question.ground_truth
    answer = response.answer

    score = QuestionScore(
        question_id=question.id,
        category=question.category,
        question=question.question,
        expected_plans=gt.expected_plans,
        expected_req_ids=gt.expected_req_ids,
        expected_standards=gt.expected_standards,
        retrieved_count=response.retrieved_count,
        citation_count=len(response.citations),
    )

    # ── Extract plan IDs and req IDs from citations ──────────
    cited_plans = set()
    cited_req_ids = set()
    cited_standards = set()

    for c in response.citations:
        if c.plan_id:
            cited_plans.add(c.plan_id)
        if c.req_id:
            cited_req_ids.add(c.req_id)
        if c.spec:
            cited_standards.add(c.spec)

    # Also extract req IDs mentioned in the answer text
    answer_req_ids = set(_REQ_ID_PATTERN.findall(answer))
    all_found_req_ids = cited_req_ids | answer_req_ids

    # Extract plan IDs from answer metadata
    answer_plans = set()
    if response.query_intent:
        answer_plans.update(response.query_intent.plan_ids)
    answer_plans.update(cited_plans)
    # Also infer plans from req IDs (VZ_REQ_{PLAN}_{NUM})
    for rid in all_found_req_ids:
        parts = rid.split("_")
        if len(parts) >= 4:
            # VZ_REQ_PLANID_NUM
            plan = "_".join(parts[2:-1])
            answer_plans.add(plan)

    score.found_plans = sorted(answer_plans)
    score.found_req_ids = sorted(all_found_req_ids)

    # ── 1. Completeness: plan coverage ───────────────────────
    if gt.expected_plans:
        covered = sum(1 for p in gt.expected_plans if p in answer_plans)
        score.completeness = covered / len(gt.expected_plans)
    elif gt.min_plans > 1:
        score.completeness = min(1.0, len(answer_plans) / gt.min_plans)
    else:
        # No plan expectation — score based on having results
        score.completeness = 1.0 if response.retrieved_count > 0 else 0.0

    # ── 2. Accuracy: req ID recall ───────────────────────────
    if gt.expected_req_ids:
        found = sum(1 for r in gt.expected_req_ids if r in all_found_req_ids)
        score.accuracy = found / len(gt.expected_req_ids)
    else:
        # No specific req IDs expected — score based on retrieving chunks
        score.accuracy = 1.0 if response.retrieved_count >= gt.min_chunks else 0.0

    # ── 3. Citation quality ──────────────────────────────────
    if response.citations:
        # Has citations — check if they include req IDs
        has_req_citations = any(c.req_id for c in response.citations)
        has_std_citations = any(c.spec for c in response.citations)
        parts = 0
        if has_req_citations:
            parts += 1
        if has_std_citations and gt.expected_standards:
            parts += 1
        elif not gt.expected_standards:
            parts += 1  # No standards expected, don't penalize

        score.citation_quality = parts / 2.0
    else:
        score.citation_quality = 0.0

    # ── 4. Standards integration ─────────────────────────────
    if gt.expected_standards:
        # Check if expected standards appear in citations or answer
        answer_lower = answer.lower()
        found_stds = []
        for std in gt.expected_standards:
            if std.lower() in answer_lower or std in cited_standards:
                found_stds.append(std)
        score.found_standards = found_stds
        score.standards_integration = len(found_stds) / len(gt.expected_standards)
    else:
        # No standards expected — full score
        score.standards_integration = 1.0

    # ── 5. No hallucination ──────────────────────────────────
    # Check for req IDs in the answer that don't match known patterns
    # A hallucinated req ID would be one that appears but doesn't exist
    # in any of our parsed trees. For now, just check format validity.
    hallucinated = []
    for rid in answer_req_ids:
        # Extract plan from ID
        parts = rid.split("_")
        if len(parts) >= 4:
            plan = "_".join(parts[2:-1])
            known_plans = {
                "LTEDATARETRY", "LTESMS", "LTEAT",
                "LTEB13NAC", "LTEOTADM",
            }
            if plan not in known_plans:
                hallucinated.append(rid)

    score.hallucinated_req_ids = hallucinated
    score.hallucination_free = 1.0 if not hallucinated else 0.0

    return score
