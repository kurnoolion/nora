"""Corrections module — profile + taxonomy editing, diff, and compact FIX reports.

Storage convention (per environment):
    <doc_root>/output/profile/<profile-name>.json   — auto-generated
    <doc_root>/output/taxonomy/taxonomy.json         — auto-generated
    <doc_root>/corrections/profile.json              — engineer's edited copy
    <doc_root>/corrections/taxonomy.json             — engineer's edited copy

The pipeline (src/pipeline/stages.py) already picks up corrections/*.json and
copies them over the auto-generated output on the next run.
"""

from core.src.corrections.schema import FixReport
from core.src.corrections.store import CorrectionStore
from core.src.corrections.compactor import profile_fix_report, taxonomy_fix_report

__all__ = [
    "FixReport",
    "CorrectionStore",
    "profile_fix_report",
    "taxonomy_fix_report",
]
