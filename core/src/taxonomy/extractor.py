"""Document-level feature extraction (TDD 5.7, Step 1).

Feeds plan metadata + section headings to the LLM to extract
telecom features and concepts from each requirement document.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

from core.src.llm.base import LLMProvider
from core.src.llm.openai_provider import (
    FINAL_ANSWER_MARKER,
    REASONING_SENTINEL_ENABLED,
)
from core.src.parser.structural_parser import Requirement, RequirementTree
from core.src.taxonomy.schema import DocumentFeatures, Feature

logger = logging.getLogger(__name__)


class LLMParseError(RuntimeError):
    """LLM response could not be parsed as feature JSON.

    Raised instead of returning empty DocumentFeatures so callers can
    distinguish "extraction failed" (retry later) from "document genuinely
    has no features" — an empty success would poison the taxonomy cache.
    """


SYSTEM_PROMPT = """\
You are a telecom domain expert specializing in 3GPP LTE/5G device \
requirements and MNO (Mobile Network Operator) compliance specifications.

Your task is to analyze requirement document structure and extract the \
telecom features and capabilities covered by each document.

Always respond with valid JSON matching the requested schema. No markdown \
fencing, no commentary outside the JSON."""

# Sentinel instruction — appended only for models whose untagged
# chain-of-thought leaks into the answer (NORA_LLM_REASONING_SENTINEL=1);
# _strip_reasoning in the provider drops everything up to the marker.
# Same opt-in pattern as web/routes/playground.py.
if REASONING_SENTINEL_ENABLED:
    SYSTEM_PROMPT += (
        "\n\nOUTPUT FORMAT: You may reason first if needed, but you MUST then "
        f"print a line containing exactly {FINAL_ANSWER_MARKER} and put ONLY "
        f"the JSON object after it. Anything before {FINAL_ANSWER_MARKER} is "
        "discarded."
    )

EXTRACTION_PROMPT_TEMPLATE = """\
Analyze the following {mno} requirement document and extract the telecom \
features it covers.
{corpus_context}
Document metadata:
- Plan ID: {plan_id}
- Plan Name: {plan_name}
- MNO: {mno}
- Release: {release}
- Version: {version}

Section headings (table of contents):
{toc}

Instructions:
1. Identify the PRIMARY telecom features/capabilities this document defines \
requirements for. These are the main topics the document is about.
2. Identify REFERENCED features — other telecom capabilities this document \
depends on or mentions but are primarily defined in other documents.
3. Extract KEY CONCEPTS: specific protocols, interfaces, timers, procedures, \
cause codes, or standards mentioned in the headings.

For each feature, provide:
- feature_id: A short uppercase identifier (e.g., "IMS_REGISTRATION", "DATA_RETRY")
- name: Human-readable name
- description: One sentence describing what this feature covers
- keywords: List of specific telecom terms associated with this feature
- confidence: 0.0-1.0 how confident you are this is a real feature (not noise)

Respond with this exact JSON structure:
{{
  "primary_features": [
    {{
      "feature_id": "...",
      "name": "...",
      "description": "...",
      "keywords": ["..."],
      "confidence": 0.9
    }}
  ],
  "referenced_features": [
    {{
      "feature_id": "...",
      "name": "...",
      "description": "...",
      "keywords": ["..."],
      "confidence": 0.7
    }}
  ],
  "key_concepts": ["concept1", "concept2", "..."]
}}"""


def _first_json_object(text: str) -> dict | None:
    """First balanced, parseable {...} in `text`, or None.

    String-aware brace scan so braces inside JSON strings don't miscount.
    """
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
                    return obj if isinstance(obj, dict) else None
        start = text.find("{", start + 1)
    return None


def split_tree_by_plan(tree: RequirementTree) -> list[RequirementTree]:
    """Split a multi-plan tree into one subtree per requirement plan_id.

    Some MNOs publish ONE document whose chapters are each a plan; the
    parser keeps that as a single tree with an empty tree-level plan_id but
    a correct per-requirement plan_id (D-DRAFT-1). Taxonomy is per-plan —
    its output files, downstream lookup, and consolidation all key on
    plan_id — so such a tree is split before extraction: one subtree per
    distinct plan, each carrying only that plan's requirements, plan_name
    taken from the group's first section heading (the chapter title).

    Single-plan trees (≤1 distinct non-empty per-req plan_id) pass through
    unchanged, except that a blank tree-level plan_id is promoted to the
    one plan its requirements declare. In a multi-plan tree, requirements
    with an empty plan_id (front matter, un-prefixed chapters) belong to
    no plan and are dropped with a logged count.
    """
    groups: dict[str, list[Requirement]] = {}
    for r in tree.requirements:
        groups.setdefault(r.plan_id, []).append(r)
    plan_ids = [p for p in groups if p]

    if len(plan_ids) <= 1:
        if plan_ids and not tree.plan_id:
            return [dataclasses.replace(tree, plan_id=plan_ids[0])]
        return [tree]

    n_unplanned = len(groups.get("", []))
    if n_unplanned:
        logger.warning(
            f"  {tree.plan_id or '<multi-plan doc>'}: dropping {n_unplanned} "
            "requirement(s) with no plan_id from taxonomy extraction"
        )
    return [
        dataclasses.replace(
            tree,
            plan_id=pid,
            plan_name=(groups[pid][0].title or pid),
            requirements=groups[pid],
        )
        for pid in sorted(plan_ids)
    ]


def resolve_corpus_overview(overview_dir: str | Path | None, mno: str) -> Path | None:
    """Resolve the per-MNO corpus-overview file, highest version wins.

    Looks for `corpus_overview_<MNO>_<version>.txt` under `overview_dir`
    (the artifact the derive-sira-prompts skill writes once per MNO).
    Returns None when the dir is unset/missing, the MNO is empty, or no
    file matches — callers treat that as "no corpus context" (fail-soft).
    """
    if not overview_dir or not mno:
        return None
    d = Path(overview_dir)
    if not d.is_dir():
        return None
    matches = sorted(d.glob(f"corpus_overview_{mno}_*.txt"))
    return matches[-1] if matches else None


class FeatureExtractor:
    """Extract telecom features from requirement documents using an LLM.

    Uses the LLMProvider protocol — any conforming provider works.

    `overview_dir` optionally points at per-MNO corpus-overview files
    (`corpus_overview_<MNO>_<version>.txt`); when the document's MNO has
    one, its text is inserted as a "Corpus context" section in the
    extraction prompt. Absent dir or file → prompt identical to before.
    """

    def __init__(self, llm: LLMProvider, overview_dir: str | Path | None = None):
        self._llm = llm
        self._overview_dir = overview_dir

    def extract(self, tree: RequirementTree) -> DocumentFeatures:
        """Extract features from a single parsed requirement tree."""
        toc = self._build_toc(tree)
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(
            mno=tree.mno or "Unknown",
            plan_id=tree.plan_id,
            plan_name=tree.plan_name,
            release=tree.release,
            version=tree.version,
            toc=toc,
            corpus_context=self._build_corpus_context(tree.mno),
        )

        logger.info(f"Extracting features from {tree.plan_id}")
        response = self._llm.complete(
            prompt=prompt,
            system=SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=4096,
        )

        features = self._parse_response(response, tree.plan_id)
        features.plan_id = tree.plan_id
        features.plan_name = tree.plan_name
        features.mno = tree.mno
        features.release = tree.release

        logger.info(
            f"  {tree.plan_id}: {len(features.primary_features)} primary, "
            f"{len(features.referenced_features)} referenced, "
            f"{len(features.key_concepts)} concepts"
        )
        return features

    def _build_corpus_context(self, mno: str) -> str:
        """Corpus-context prompt section for the doc's MNO, or ""."""
        if not self._overview_dir:
            return ""
        path = resolve_corpus_overview(self._overview_dir, mno)
        if path is None:
            logger.warning(
                f"TAX-W003: No corpus overview for MNO '{mno}' under "
                f"{self._overview_dir} — extracting without corpus context"
            )
            return ""
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            logger.warning(f"TAX-W003: Corpus overview {path.name} is empty — skipped")
            return ""
        logger.info(f"  Corpus context: {path.name} ({len(text)} chars)")
        return (
            "\nCorpus context — an overview of the full requirement corpus "
            "this document belongs to:\n" + text + "\n"
        )

    @staticmethod
    def _build_toc(tree: RequirementTree) -> str:
        """Build a table of contents string from the requirement tree."""
        lines = []
        for req in tree.requirements:
            indent = "  " * (req.section_number.count(".") - 1)
            lines.append(f"{indent}{req.section_number} {req.title}")
            # Limit depth to keep prompt manageable
            if len(lines) > 200:
                lines.append("  ... (truncated)")
                break
        return "\n".join(lines)

    @staticmethod
    def _parse_response(response: str, plan_id: str) -> DocumentFeatures:
        """Parse the LLM JSON response into DocumentFeatures.

        Raises LLMParseError when no JSON object can be recovered — never
        returns an empty-success for an unparseable response.
        """
        # Strip markdown fencing if present
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            # Tolerant fallback: chatty/reasoning models may wrap the JSON in
            # prose the provider-level strip didn't catch. Take the first
            # balanced {...} that parses (same posture as profile_debug and
            # sandbox/enrich_batching).
            data = _first_json_object(text)
            if data is None:
                logger.warning(f"Failed to parse LLM response for {plan_id}: {e}")
                logger.debug(f"Raw response: {text[:500]}")
                raise LLMParseError(
                    f"unparseable LLM response for {plan_id}: {e}"
                ) from e

        primary = [
            Feature(**f)
            for f in data.get("primary_features", [])
            if isinstance(f, dict)
        ]
        referenced = [
            Feature(**f)
            for f in data.get("referenced_features", [])
            if isinstance(f, dict)
        ]
        concepts = data.get("key_concepts", [])

        return DocumentFeatures(
            primary_features=primary,
            referenced_features=referenced,
            key_concepts=concepts if isinstance(concepts, list) else [],
        )
