"""Corrections module — profile + taxonomy editing, diff, and compact FIX reports.

Storage convention (per environment, D-022):
    <env_dir>/out/profile/<profile-name>.json    — auto-generated
    <env_dir>/out/taxonomy/taxonomy.json         — auto-generated
    <env_dir>/corrections/profile.json           — engineer's edited copy
    <env_dir>/corrections/taxonomy.json          — engineer's edited copy

The pipeline (core/src/pipeline/stages.py) already picks up corrections/*.json
and copies them over the auto-generated output on the next run.
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
