"""Knowledge Graph schema definitions (TDD 6.1–6.2).

Defines node types, edge types, and helper functions for creating
typed graph elements on a NetworkX DiGraph.

Node types implemented (skipping Test_Plan / Test_Case — Step 4 deferred):
  MNO, Release, Plan, Requirement, Standard_Section, Feature

Edge types cover organizational, within-doc, cross-doc, standards,
and feature relationships.
"""

from __future__ import annotations

from enum import Enum


# ── Node types ────────────────────────────────────────────────────


class NodeType(str, Enum):
    MNO = "MNO"
    RELEASE = "Release"
    PLAN = "Plan"
    REQUIREMENT = "Requirement"
    STANDARD_SECTION = "Standard_Section"
    FEATURE = "Feature"


# ── Edge types ────────────────────────────────────────────────────


class EdgeType(str, Enum):
    # Organizational
    HAS_RELEASE = "has_release"
    CONTAINS_PLAN = "contains_plan"

    # Within-document
    PARENT_OF = "parent_of"
    BELONGS_TO = "belongs_to"

    # Cross-document (same MNO+release)
    DEPENDS_ON = "depends_on"
    SHARED_STANDARD = "shared_standard"

    # Standards
    REFERENCES_STANDARD = "references_standard"
    PARENT_SECTION = "parent_section"

    # Feature
    MAPS_TO = "maps_to"
    FEATURE_DEPENDS_ON = "feature_depends_on"


# ── Node ID conventions ──────────────────────────────────────────
#
# Deterministic, globally unique node IDs:
#   MNO:          "mno:VZW"
#   Release:      "release:VZW:2026_feb"
#   Plan:         "plan:VZW:2026_feb:LTEDATARETRY"
#   Requirement:  "req:VZ_REQ_LTEDATARETRY_7748"   (req_id is globally unique)
#   Std Section:  "std:24.301:11:5.5.1.2.5"        (spec:release_num:section)
#   Feature:      "feature:IMS_REGISTRATION"
#


def mno_id(mno: str) -> str:
    return f"mno:{mno}"


def release_id(mno: str, release: str) -> str:
    return f"release:{mno}:{release}"


def plan_id(mno: str, release: str, plan: str) -> str:
    return f"plan:{mno}:{release}:{plan}"


def req_id(requirement_id: str) -> str:
    return f"req:{requirement_id}"


def std_section_id(spec: str, release_num: int, section: str) -> str:
    return f"std:{spec}:{release_num}:{section}"


def std_spec_id(spec: str, release_num: int) -> str:
    """ID for a spec-level node (no section)."""
    return f"std:{spec}:{release_num}"


def feature_id(fid: str) -> str:
    return f"feature:{fid}"
