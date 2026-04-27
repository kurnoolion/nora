"""Data models for standards ingestion (TDD 5.6).

Covers reference collection, spec metadata, parsed sections,
and extracted content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class SpecReference:
    """A single reference to a standards spec from a requirement document."""
    spec: str = ""              # e.g., "24.301"
    release: str = ""           # e.g., "Release 11"
    sections: list[str] = field(default_factory=list)  # e.g., ["5.5.1.2.5"]
    source_plan: str = ""       # e.g., "LTEDATARETRY"
    source_reqs: list[str] = field(default_factory=list)  # req sections referencing this


@dataclass
class AggregatedSpecRef:
    """Aggregated reference for a unique (spec, release) pair."""
    spec: str = ""
    release: str = ""
    release_num: int = 0        # numeric release (e.g., 11)
    sections: list[str] = field(default_factory=list)  # all referenced sections
    source_plans: list[str] = field(default_factory=list)
    ref_count: int = 0          # total reference count across all plans


@dataclass
class StandardsReferenceIndex:
    """Complete index of all standards references from ingested documents."""
    specs: list[AggregatedSpecRef] = field(default_factory=list)
    total_refs: int = 0
    total_unique_specs: int = 0
    source_documents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path) -> StandardsReferenceIndex:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            specs=[AggregatedSpecRef(**s) for s in data.get("specs", [])],
            total_refs=data.get("total_refs", 0),
            total_unique_specs=data.get("total_unique_specs", 0),
            source_documents=data.get("source_documents", []),
        )


@dataclass
class SpecSection:
    """A parsed section from a 3GPP spec document."""
    number: str = ""            # e.g., "5.5.1.2.5"
    title: str = ""             # e.g., "Attach procedure"
    depth: int = 0              # nesting depth
    text: str = ""              # full section text content
    parent_number: str = ""     # parent section number
    children: list[str] = field(default_factory=list)  # child section numbers


@dataclass
class SpecDocument:
    """A parsed 3GPP specification document."""
    spec_number: str = ""       # e.g., "24.301"
    title: str = ""             # e.g., "Non-Access-Stratum (NAS) protocol for EPS"
    version: str = ""           # e.g., "11.7.0"
    release: str = ""           # e.g., "Release 11"
    release_num: int = 0
    source_file: str = ""       # path to source DOC/DOCX
    sections: list[SpecSection] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path) -> SpecDocument:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            spec_number=data.get("spec_number", ""),
            title=data.get("title", ""),
            version=data.get("version", ""),
            release=data.get("release", ""),
            release_num=data.get("release_num", 0),
            source_file=data.get("source_file", ""),
            sections=[SpecSection(**s) for s in data.get("sections", [])],
        )

    def get_section(self, number: str) -> SpecSection | None:
        """Find a section by its number."""
        for s in self.sections:
            if s.number == number:
                return s
        return None

    def get_section_with_ancestors(self, number: str) -> list[SpecSection]:
        """Get a section and all its ancestors up to root."""
        result = []
        current = number
        while current:
            sec = self.get_section(current)
            if sec:
                result.append(sec)
                current = sec.parent_number
            else:
                break
        result.reverse()
        return result


@dataclass
class ExtractedSpecContent:
    """Content extracted from a spec for a specific set of referenced sections."""
    spec_number: str = ""
    release: str = ""
    release_num: int = 0
    version: str = ""
    spec_title: str = ""
    referenced_sections: list[SpecSection] = field(default_factory=list)
    context_sections: list[SpecSection] = field(default_factory=list)
    total_sections_in_spec: int = 0
    source_plans: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path) -> ExtractedSpecContent:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            spec_number=data.get("spec_number", ""),
            release=data.get("release", ""),
            release_num=data.get("release_num", 0),
            version=data.get("version", ""),
            spec_title=data.get("spec_title", ""),
            referenced_sections=[
                SpecSection(**s) for s in data.get("referenced_sections", [])
            ],
            context_sections=[
                SpecSection(**s) for s in data.get("context_sections", [])
            ],
            total_sections_in_spec=data.get("total_sections_in_spec", 0),
            source_plans=data.get("source_plans", []),
        )
