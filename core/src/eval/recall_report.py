"""Silver recall report over the Ask page's feedback DB (strand recall-metrics, design §7).

Averages the ``silver_recall`` numbers each SIRA answer stored in its
``lane_config`` (how deep in the BM25 fetch the cited requirements sat), per
intent and over time. Each k is averaged over the rows that reached it: an
answer that fetched 20 has no @30 or @40. Writes a timestamped folder with
``run.json`` (the numbers plus time, git commit, window and filters; never
the DB path or any question text) and ``report.html`` (the chart).

The DB is read as data with sqlite, read-only; this module does not import
``web`` (web depends on eval). Reports come from live usage, so
``reports/recall/`` is gitignored.

CLI: ``python -m core.src.eval.recall_report --db <env_dir>/state/nora_test_feedback.db
[--since 7d] [--bucket day|week] [--intent I] [--stack S] [--out-root DIR]``.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORTS_DIR = REPO_ROOT / "reports" / "recall"
SLUG = "silver"
TEMPLATE = Path(__file__).resolve().parent / "recall_report_template.html"
PLACEHOLDER = "/*__RUN__*/null"


def parse_since(text: str) -> timedelta:
    """``7d`` or ``36h`` as a timedelta."""
    m = re.fullmatch(r"(\d+)([dh])", text.strip())
    if not m:
        raise ValueError(f"--since takes <N>d or <N>h, got {text!r}")
    n = int(m.group(1))
    return timedelta(days=n) if m.group(2) == "d" else timedelta(hours=n)


def load(db: Path, *, since: datetime, intent: "str | None" = None,
         stack: "str | None" = None) -> "tuple[list[dict], int]":
    """SIRA rows asked since ``since`` that carry silver recall, oldest first,
    and how many matching SIRA rows did not (older rows, failed answers,
    no citations)."""
    since_s = since.astimezone(timezone.utc).isoformat(timespec="seconds")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        raw = con.execute("SELECT timestamp, lane_config FROM test_feedback"
                          " WHERE lane = 'sira' AND timestamp >= ? ORDER BY timestamp, id",
                          (since_s,)).fetchall()
    finally:
        con.close()
    rows, skipped = [], 0
    for ts, lc_text in raw:
        try:
            lc = json.loads(lc_text) if lc_text else {}
        except ValueError:
            lc = {}
        row_intent = str(lc.get("intent") or "unknown")
        if (intent and row_intent != intent) or (stack and lc.get("stack") != stack):
            continue
        sr = lc.get("silver_recall")
        if not sr:
            skipped += 1
            continue
        rows.append({"ts": ts, "intent": row_intent, "stack": lc.get("stack"), "sr": sr})
    return rows, skipped


def _mean(xs: list) -> "float | None":
    return round(sum(xs) / len(xs), 4) if xs else None


def _group(rows: list[dict]) -> dict:
    by_k: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        for k, v in r["sr"]["recall"].items():
            by_k[k].append(v)
    ks = sorted(by_k, key=int)
    return {"n": len(rows),
            "recall": {k: _mean(by_k[k]) for k in ks},
            "n_k": {k: len(by_k[k]) for k in ks},
            "cited": _mean([r["sr"]["cited"] for r in rows]),
            "outside": _mean([r["sr"]["outside"] for r in rows]),
            "budget_cut": _mean([1.0 if r["sr"]["packed"] < r["sr"]["fetched"] else 0.0 for r in rows])}


def summarize(rows: list[dict]) -> list[dict]:
    """One group per intent (alphabetical), then ``all``."""
    by_intent: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_intent[r["intent"]].append(r)
    out = [{"intent": i, **_group(by_intent[i])} for i in sorted(by_intent)]
    if rows:
        out.append({"intent": "all", **_group(rows)})
    return out


def _bucket(ts: str, bucket: str) -> str:
    d = datetime.fromisoformat(ts).astimezone(timezone.utc).date()
    if bucket == "week":
        d = d - timedelta(days=d.weekday())
    return d.isoformat()


def series(rows: list[dict], bucket: str) -> list[dict]:
    """Per (bucket, intent) groups, plus ``all`` per bucket; buckets are UTC
    days, or weeks named by their Monday."""
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        b = _bucket(r["ts"], bucket)
        cells[(b, r["intent"])].append(r)
        cells[(b, "all")].append(r)
    return [{"bucket": b, "intent": i, **_group(cells[(b, i)])} for b, i in sorted(cells)]


def build_run(rows: list[dict], *, skipped: int, since: str, bucket: str, intent: "str | None",
              stack: "str | None", created_at: datetime, git: dict) -> dict:
    return {"meta": {"report": SLUG, "created_at": created_at.isoformat(timespec="seconds"),
                     "since": since, "bucket": bucket, "filters": {"intent": intent, "stack": stack},
                     "rows": len(rows), "skipped": skipped, "git": git},
            "summary": summarize(rows), "series": series(rows, bucket)}


def render_html(run: dict) -> str:
    data = json.dumps(run, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace(PLACEHOLDER, data)


def write_run(run: dict, out_root: Path) -> Path:
    stamp = datetime.fromisoformat(run["meta"]["created_at"]).strftime("%Y-%m-%d-%H%M")
    out = Path(out_root) / f"{stamp}_{SLUG}_{run['meta']['since']}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "run.json").write_text(json.dumps(run, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "report.html").write_text(render_html(run), encoding="utf-8")
    return out


def _git() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                                capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT,
                                    capture_output=True, text=True, check=True).stdout.strip())
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def _pct(v: float) -> int:
    """Whole percent, halves rounded up (as the report page and the answer card do)."""
    return int(v * 100 + 0.5)


def _table(summary: list[dict]) -> str:
    ks = sorted({k for g in summary for k in g["recall"]}, key=int)
    head = f"{'intent':<12}{'n':>5}" + "".join(f"{'@' + k:>8}" for k in ks) + f"{'cited':>8}{'outside':>9}{'cut':>6}"
    lines = [head]
    for g in summary:
        cells = "".join(f"{_pct(g['recall'][k]):>7}%" if k in g["recall"] else f"{'-':>8}" for k in ks)
        lines.append(f"{g['intent']:<12}{g['n']:>5}{cells}{g['cited']:>8.1f}{g['outside']:>9.1f}"
                     f"{_pct(g['budget_cut']):>5}%")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", type=Path, required=True, help="the feedback DB (<env_dir>/state/nora_test_feedback.db)")
    ap.add_argument("--since", default="7d", help="window: <N>d or <N>h (default 7d)")
    ap.add_argument("--bucket", choices=("day", "week"), default="day")
    ap.add_argument("--intent", default=None, help="only this intent")
    ap.add_argument("--stack", default=None, help="only this stack (lane_config.stack)")
    ap.add_argument("--out-root", type=Path, default=REPORTS_DIR, help="folder that gets one subfolder per run")
    args = ap.parse_args(argv)

    if not args.db.is_file():
        print("recall_report: --db is not a file", file=sys.stderr)
        return 2
    try:
        window = parse_since(args.since)
    except ValueError as e:
        print(f"recall_report: {e}", file=sys.stderr)
        return 2
    rows, skipped = load(args.db, since=datetime.now(timezone.utc) - window,
                         intent=args.intent, stack=args.stack)
    run = build_run(rows, skipped=skipped, since=args.since, bucket=args.bucket, intent=args.intent,
                    stack=args.stack, created_at=datetime.now().replace(microsecond=0), git=_git())
    out = write_run(run, args.out_root)
    print(_table(run["summary"]) if rows else "no rows with silver recall in the window")
    print(f"{len(rows)} rows, {skipped} SIRA rows skipped (no silver recall)\n{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
