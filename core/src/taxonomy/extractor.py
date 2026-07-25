"""Document-level feature extraction (TDD 5.7, Step 1).

Feeds plan metadata + section headings to the LLM to extract
telecom features and concepts from each requirement document.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.src.llm.base import LLMProvider
from core.src.parser.structural_parser import RequirementTree
from core.src.taxonomy.schema import DocumentFeatures, Feature

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a telecom domain expert specializing in 3GPP LTE/5G device \
requirements and MNO (Mobile Network Operator) compliance specifications.

Your task is to analyze requirement document structure and extract the \
telecom features and capabilities covered by each document.

Always respond with valid JSON matching the requested schema. No markdown \
fencing, no commentary outside the JSON."""

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
        """Parse the LLM JSON response into DocumentFeatures."""
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
            logger.warning(f"Failed to parse LLM response for {plan_id}: {e}")
            logger.debug(f"Raw response: {text[:500]}")
            return DocumentFeatures()

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
