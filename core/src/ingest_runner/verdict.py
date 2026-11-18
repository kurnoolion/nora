"""The eval gate rule (strand design §6, D-DRAFT-4; revised in strand
history-fidelity).

Stage 1 only, scored on the ingest's own cell: each sample's recall
counts only its ground-truth entries of that cell (the cell's MNO, with
the cell's release or none), and samples with no such entry are left out.
The ingest runs the eval several times (`eval_runs`); the gate takes the
mean. Pass when no run has a `GEV-E` error and the mean is at least the
baseline minus the tolerance. The baseline is the same cell score, as a
mean over its runs, of the last ingest of this cell whose gate passed;
before any, the configured `baseline_recall`; then the newest passed
`golden-stage1` gate in this stack's own registry, scored on the cell;
before any of those, the first run passes and seeds the baseline. A cell with no ground truth in
the golden set passes with a `no gold` detail. The golden dir is shared by
every stack on a host, so its newest run may be another stack's: only a
run this stack's registry gated counts.
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


def run_refs(ref: str) -> list[str]:
    """The run ids in a stored ref: one id, or several comma-joined."""
    return [r.strip() for r in str(ref or "").split(",") if r.strip()]


def cell_recall(report: dict, mno: str, release: str) -> float | None:
    """Mean Stage-1 recall over the samples with ground truth in the cell
    ``mno``/``release``, each sample scored on those entries alone (an
    entry with no release counts for every release of its MNO); None when
    no sample has any. A report without per-entry hits and misses (an
    older format) is scored whole, as ``recall_avg``."""
    s1 = [r for r in report.get("stage1") or [] if isinstance(r, dict)]
    if s1 and not any("hits" in r or "misses" in r for r in s1):
        return recall_avg(report)
    per: list[float] = []
    for r in s1:
        def mine(e) -> bool:
            return (isinstance(e, dict) and str(e.get("mno", "")) == mno
                    and str(e.get("release", "") or "") in ("", release))
        hits = [e for e in r.get("hits") or [] if mine(e)]
        misses = [e for e in r.get("misses") or [] if mine(e)]
        if hits or misses:
            per.append(len(hits) / (len(hits) + len(misses)))
    return sum(per) / len(per) if per else None


def mean_cell_recall(golden_dir: str | Path, refs: list[str], mno: str,
                     release: str) -> tuple[float | None, float, int, list[str]]:
    """``(mean, spread, runs, errors)`` of ``cell_recall`` over the runs in
    ``refs`` (spread = max − min; runs = how many scored); errors are the
    `GEV-E` codes of every run. Unreadable reports are skipped."""
    scores: list[float] = []
    errors: list[str] = []
    for ref in refs:
        try:
            report = read_report(golden_dir, ref)
        except (OSError, json.JSONDecodeError):
            continue
        errors += gev_errors(report)
        v = cell_recall(report, mno, release)
        if v is not None:
            scores.append(v)
    if not scores:
        return None, 0.0, 0, errors
    return (round(sum(scores) / len(scores), 6), round(max(scores) - min(scores), 6),
            len(scores), errors)


def gev_errors(report: dict) -> list[str]:
    return [str(e.get("code", "")) for e in report.get("errors") or []
            if str(e.get("code", "")).startswith("GEV-E")]


def read_report(golden_dir: str | Path, run_id: str) -> dict:
    p = Path(golden_dir) / "runs" / run_id / "report.json"
    return json.loads(p.read_text(encoding="utf-8"))


def gated_recall(registry_root: str | Path, golden_dir: str | Path,
                 exclude: str = "", mno: str = "",
                 release: str = "") -> tuple[float, str] | None:
    """`(recall, run_ref)` of the newest passed `golden-stage1` gate in the
    registry under `registry_root` whose run reports under `golden_dir`
    have Stage-1 results, other than `exclude` (the runs being judged);
    None when there is none, or no readable registry. With `mno` the
    recall is the cell score (`mean_cell_recall`), else `recall_avg` of
    the gate's first run."""
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
    skip = set(run_refs(exclude))
    for ref in refs:
        ids = [r for r in run_refs(ref) if r not in skip]
        if not ids:
            continue
        if mno:
            avg = mean_cell_recall(golden_dir, ids, mno, release)[0]
        else:
            try:
                avg = recall_avg(read_report(golden_dir, ids[0]))
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
