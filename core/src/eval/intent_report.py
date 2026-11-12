"""Intent confidence report (strand sira-intent, design §8).

One decision-model pass over a labelled intent set, then every confidence
floor and confidence bin scored offline. Writes a timestamped folder with
``run.json`` (the numbers plus what correlates a run: time, git commit,
dataset hash, decision provider and model; never the endpoint) and ``report.html`` (the charts).

Rows carry sample ids and labels only, never query text, so a report is
safe to commit for a synthetic set.

CLI: ``python -m core.src.eval.intent_report [--set PATH] [--out-root DIR]
[--floor F]``. Needs ``NORA_DECISION_PROVIDER`` (and, for ``systemone``,
``NORA_DECISION_BASE_URL``); a decision call that fails stops the run
rather than scoring the rules under the model's name.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from core.src.eval.intent_eval import CHANGE_BAR, DEFAULT_INTENT_SET_PATH, IntentSample, load_intent_set
from core.src.query.schema import Intent

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORTS_DIR = REPO_ROOT / "reports" / "intent"
SLUG = "kev-floor"
TEMPLATE = Path(__file__).resolve().parent / "intent_report_template.html"
PLACEHOLDER = "/*__RUN__*/null"
FLOORS = (0.0, 0.2, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 0.9)
BIN_EDGES = (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0)


@dataclass(frozen=True)
class Row:
    sample_id: str
    expected: Intent
    rules: Intent
    model: "Intent | None"          # None: no usable answer
    confidence: "float | None"


def collect(samples: list[IntentSample], provider, rules_analyzer=None, questions=None) -> list[Row]:
    """The rules intent and the model's own intent and confidence per sample.

    Raises:
        DecisionError: A decision call failed.
    """
    from core.src.decision.analyzer import SIRA_QUESTIONS
    from core.src.query.analyzer import MockQueryAnalyzer

    rules_analyzer = rules_analyzer or MockQueryAnalyzer()
    questions = questions or SIRA_QUESTIONS
    rows = []
    for s in samples:
        answer = next((a for a in provider.decide(s.query, questions) if a.question_id == "intent"), None)
        try:
            model = Intent(answer.value) if answer is not None else None
        except ValueError:
            model = None
        rows.append(Row(s.sample_id, s.intent, rules_analyzer.analyze(s.query).intent, model,
                        answer.confidence if answer is not None else None))
    return rows


def _routed(r: Row, floor: float) -> Intent:
    return r.model if r.model is not None and r.confidence >= floor else r.rules


def sweep(rows: list[Row], floors=FLOORS) -> list[dict]:
    """Accuracy, change precision/recall and the model's share per floor."""
    out = []
    for f in floors:
        pred = [_routed(r, f) for r in rows]
        tp = sum(p is Intent.CHANGE and r.expected is Intent.CHANGE for p, r in zip(pred, rows))
        pp = sum(p is Intent.CHANGE for p in pred)
        ap = sum(r.expected is Intent.CHANGE for r in rows)
        prec, rec = (tp / pp if pp else 0.0), (tp / ap if ap else 0.0)
        out.append({"floor": f,
                    "accuracy": round(sum(p is r.expected for p, r in zip(pred, rows)) / len(rows), 4),
                    "change_precision": round(prec, 4), "change_recall": round(rec, 4),
                    "change_bar": prec >= CHANGE_BAR and rec >= CHANGE_BAR,
                    "model_decides": sum(r.model is not None and r.confidence >= f for r in rows)})
    return out


def bins(rows: list[Row], edges=BIN_EDGES) -> list[dict]:
    """Model and rules accuracy per model-confidence bin (last bin closed)."""
    out = []
    for i, (lo, hi) in enumerate(zip(edges, edges[1:])):
        last = i == len(edges) - 2
        b = [r for r in rows if r.model is not None
             and lo <= r.confidence and (r.confidence <= hi if last else r.confidence < hi)]
        if b:
            out.append({"lo": lo, "hi": hi, "n": len(b),
                        "model_accuracy": round(sum(r.model is r.expected for r in b) / len(b), 4),
                        "rules_accuracy": round(sum(r.rules is r.expected for r in b) / len(b), 4)})
    return out


def summarize(sweep_rows: list[dict], bin_rows: list[dict], current_floor: float) -> dict:
    """The numbers the report's text states."""
    best = max(s["accuracy"] for s in sweep_rows)
    top = [s["floor"] for s in sweep_rows if s["accuracy"] == best]
    bar = [s["floor"] for s in sweep_rows if s["change_bar"]]
    rules_only = [s for s in sweep_rows if s["model_decides"] == 0]
    crossover = None
    for i, b in enumerate(bin_rows):
        if all(x["model_accuracy"] >= x["rules_accuracy"] for x in bin_rows[i:]):
            crossover = b["lo"]
            break
    return {"best_accuracy": best, "plateau": [min(top), max(top)],
            "change_bar_from": min(bar) if bar else None,
            "rules_accuracy": rules_only[0]["accuracy"] if rules_only else None,
            "model_accuracy": sweep_rows[0]["accuracy"],
            "at_current": min(sweep_rows, key=lambda s: abs(s["floor"] - current_floor)),
            "crossover": crossover}


def build_run(rows: list[Row], *, set_id: str, dataset_sha: str, floor: float, decision: dict,
              created_at: datetime, git: dict) -> dict:
    sw, bn = sweep(rows), bins(rows)
    return {"meta": {"report": SLUG, "created_at": created_at.isoformat(timespec="seconds"),
                     "set_id": set_id, "dataset_sha256": dataset_sha, "n": len(rows),
                     "floor": floor, "decision": decision, "git": git},
            "summary": summarize(sw, bn, floor), "sweep": sw, "bins": bn,
            "rows": [{**asdict(r), "expected": r.expected.value, "rules": r.rules.value,
                      "model": r.model.value if r.model else None} for r in rows]}


def render_html(run: dict) -> str:
    data = json.dumps(run, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace(PLACEHOLDER, data)


def write_run(run: dict, out_root: Path) -> Path:
    stamp = datetime.fromisoformat(run["meta"]["created_at"]).strftime("%Y-%m-%d-%H%M")
    out = Path(out_root) / f"{stamp}_{SLUG}_{run['meta']['set_id']}"
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


def main(argv=None) -> int:
    from core.src.decision.base import DecisionError
    from core.src.env.config import resolve_decision_confidence_floor
    from core.src.query.analyzer import _build_decision_provider

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--set", type=Path, default=DEFAULT_INTENT_SET_PATH, help="labelled intent set")
    ap.add_argument("--out-root", type=Path, default=REPORTS_DIR, help="folder that gets one subfolder per run")
    ap.add_argument("--floor", type=float, default=None, help="the floor in use (default: NORA_DECISION_CONFIDENCE_FLOOR or 0.50)")
    args = ap.parse_args(argv)

    name = (os.environ.get("NORA_DECISION_PROVIDER") or "").strip()
    provider = _build_decision_provider(name) if name else None
    if provider is None:
        print("intent_report: set NORA_DECISION_PROVIDER (systemone needs NORA_DECISION_BASE_URL too)", file=sys.stderr)
        return 2
    raw = args.set.read_bytes()
    try:
        rows = collect(load_intent_set(args.set), provider)
    except DecisionError as e:
        print(f"intent_report: decision call failed, nothing written ({e})", file=sys.stderr)
        return 1
    # Provider and model only: reports are committed, the endpoint is not.
    decision = {"provider": name, "model": getattr(provider, "model", None)}
    run = build_run(rows, set_id=json.loads(raw)["set_id"], dataset_sha=hashlib.sha256(raw).hexdigest(),
                    floor=args.floor if args.floor is not None else resolve_decision_confidence_floor(),
                    decision=decision, created_at=datetime.now().replace(microsecond=0), git=_git())
    out = write_run(run, args.out_root)
    s = run["summary"]
    print(f"{out}\nbest accuracy {s['best_accuracy']:.3f} at floors {s['plateau'][0]:.2f}-{s['plateau'][1]:.2f}; "
          f"at floor {run['meta']['floor']:.2f}: {s['at_current']['accuracy']:.3f}, "
          f"change bar {'met' if s['at_current']['change_bar'] else 'not met'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
