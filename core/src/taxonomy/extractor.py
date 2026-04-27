"""Document-level feature extraction (TDD 5.7, Step 1).

Feeds plan metadata + section headings to the LLM to extract
telecom features and concepts from each requirement document.
"""

from __future__ import annotations

import json
import logging

from src.llm.base import LLMProvider
from src.parser.structural_parser import RequirementTree
from src.taxonomy.schema import DocumentFeatures, Feature

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


class FeatureExtractor:
    """Extract telecom features from requirement documents using an LLM.

    Uses the LLMProvider protocol — any conforming provider works.
    """

    def __init__(self, llm: LLMProvider):
        self._llm = llm

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
