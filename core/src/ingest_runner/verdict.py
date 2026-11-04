"""The eval gate rule (strand design §6, D-DRAFT-4).

Stage 1 only. Pass when the run has no `GEV-E` error and `recall_avg`
is at least the baseline minus the tolerance. The baseline is the
Stage-1 result of the last ingest whose gate passed; before any, the
newest recorded golden run with Stage-1 results; before any of those,
the first run passes and seeds the baseline.
"""

from __future__ import annotations

import json
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


def latest_recorded_recall(golden_dir: str | Path, exclude: str = "") -> tuple[float, str] | None:
    """The newest run under `<golden_dir>/runs/` with Stage-1 results,
    other than `exclude` (the run being judged): `(recall_avg, run_id)`,
    or None."""
    runs = Path(golden_dir) / "runs"
    if not runs.is_dir():
        return None
    for d in sorted((p for p in runs.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        if d.name == exclude:
            continue
        rp = d / "report.json"
        if not rp.is_file():
            continue
        try:
            rep = json.loads(rp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        avg = recall_avg(rep)
        if avg is not None:
            return avg, d.name
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
