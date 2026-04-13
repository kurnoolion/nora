"""Feature taxonomy data model (TDD 5.7).

Defines the output structures for feature extraction and the
unified taxonomy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class Feature:
    """A single telecom feature/capability."""
    feature_id: str = ""
    name: str = ""
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class DocumentFeatures:
    """Features extracted from a single document (TDD 5.7 Step 1)."""
    plan_id: str = ""
    plan_name: str = ""
    mno: str = ""
    release: str = ""
    primary_features: list[Feature] = field(default_factory=list)
    referenced_features: list[Feature] = field(default_factory=list)
    key_concepts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path) -> DocumentFeatures:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            plan_id=data.get("plan_id", ""),
            plan_name=data.get("plan_name", ""),
            mno=data.get("mno", ""),
            release=data.get("release", ""),
            primary_features=[Feature(**f) for f in data.get("primary_features", [])],
            referenced_features=[Feature(**f) for f in data.get("referenced_features", [])],
            key_concepts=data.get("key_concepts", []),
        )


@dataclass
class TaxonomyFeature:
    """A feature in the unified taxonomy (TDD 5.7 output)."""
    feature_id: str = ""
    name: str = ""
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    mno_coverage: dict[str, list[str]] = field(default_factory=dict)
    source_plans: list[str] = field(default_factory=list)
    depends_on_features: list[str] = field(default_factory=list)
    is_primary_in: list[str] = field(default_factory=list)
    is_referenced_in: list[str] = field(default_factory=list)


@dataclass
class FeatureTaxonomy:
    """Unified feature taxonomy across all documents (TDD 5.7 output).

    This is the final output for human review.
    """
    mno: str = ""
    release: str = ""
    features: list[TaxonomyFeature] = field(default_factory=list)
    source_documents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path) -> FeatureTaxonomy:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            mno=data.get("mno", ""),
            release=data.get("release", ""),
            features=[TaxonomyFeature(**f) for f in data.get("features", [])],
            source_documents=data.get("source_documents", []),
        )
