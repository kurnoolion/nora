"""The eval gate rule (strand design §6, D-DRAFT-4).

Stage 1 only. Pass when the run has no `GEV-E` error and `recall_avg`
is at least the baseline minus the tolerance. The baseline is the
Stage-1 result of the last ingest whose gate passed; before any, the run
of the newest passed `golden-stage1` gate in this stack's own registry;
before any of those, the first run passes and seeds the baseline. The
golden dir is shared by every stack on a host, so its newest run may be
another stack's: only a run this stack's registry gated counts.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def recall_avg(report: dict) -> float | None:
    s1 = report.get("stage1") or []
    recalls = [float(r.get("recall", 0.0)) for r in s1 if isinstance(r, dict)]
    if not recalls:
        return None
    return sum(recalls) / len(recalls)


def gev_errors(report: dict) -> list[str]:
    return [str(e.get("code", "")) for e in report.get("errors") or []
            if str(e.get("code", "")).startswith("GEV-E")]


def read_report(golden_dir: str | Path, run_id: str) -> dict:
    p = Path(golden_dir) / "runs" / run_id / "report.json"
    return json.loads(p.read_text(encoding="utf-8"))


def gated_recall(registry_root: str | Path, golden_dir: str | Path,
                 exclude: str = "") -> tuple[float, str] | None:
    """`(recall_avg, run_ref)` of the newest passed `golden-stage1` gate
    in the registry under `registry_root` whose run report under
    `golden_dir` has Stage-1 results, other than `exclude` (the run being
    judged); None when there is none, or no readable registry."""
    reg = Path(registry_root) / "registry.db"
    if not reg.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{reg}?mode=ro", uri=True)
        try:
            refs = [r[0] for r in con.execute(
                "SELECT run_ref FROM gate_run WHERE kind = 'golden-stage1' AND verdict = 'pass' "
                "AND run_ref != '' ORDER BY id DESC")]
        finally:
            con.close()
    except sqlite3.Error:
        return None
    for ref in refs:
        if ref == exclude:
            continue
        try:
            avg = recall_avg(read_report(golden_dir, ref))
        except (OSError, json.JSONDecodeError):
            continue
        if avg is not None:
            return avg, ref
    return None


def decide(recall: float | None, baseline: float | None, tolerance: float,
           errors: list[str]) -> tuple[str, str]:
    """`(verdict, detail)`; verdict is `pass` or `fail`."""
    if errors:
        return "fail", f"eval errors: {', '.join(sorted(set(errors)))}"
    if recall is None:
        return "fail", "no Stage-1 results in the run"
    if baseline is None:
        return "pass", f"recall_avg={recall:.2f}; no baseline yet — this run seeds it"
    floor = baseline - tolerance
    if recall >= floor:
        return "pass", f"recall_avg={recall:.2f} >= baseline {baseline:.2f} - {tolerance:.2f}"
    return "fail", f"recall_avg={recall:.2f} < baseline {baseline:.2f} - {tolerance:.2f}"
