"""Golden eval runners (FR-38, strand golden-eval).

Stage 1 — retrieval recall: POST the sample query to a serving stack's
``/sira-query`` endpoint (black-box over HTTP, D-DRAFT-2) and score the
fraction of ground-truth req_ids present in the returned results. One run
per stack URL is the release A/B.

Stage 2 — golden similarity: regenerate a response from Stage-1's retrieved
chunks through the production ``QueryPipeline`` via ``pinned_chunk_ids``
(D-DRAFT-3 — same synthesis prompt as nora-web by construction), then have
an on-prem LLM judge score it against the expert's golden response
(D-DRAFT-4 — versioned judge prompt; scores comparable only within one
version).

Stdlib ``urllib`` only — same posture as the llm module; no httpx outside
the module that owns that boundary.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .golden import (
    STATUS_DRAFT,
    STATUS_GOLDEN_READY,
    GoldenEvalError,
    GoldenSample,
    GroundTruthEntry,
    runs_dir,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300.0


# ─── HTTP (module-level so tests can monkeypatch) ───────────────────


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def query_stack(
    stack_url: str,
    query: str,
    top_k: int | None = None,
    label: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict:
    """POST /sira-query on a stack. Raises GoldenEvalError (GEV-E002) on
    any transport or protocol failure — a sample is never silently skipped.
    """
    payload: dict = {"query": query}
    if top_k:
        payload["top_k"] = top_k
    if label:
        payload["label"] = label
    url = stack_url.rstrip("/") + "/sira-query"
    try:
        body = _post_json(url, payload, timeout)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise GoldenEvalError(
            "GEV-E002", f"stack {stack_url} /sira-query failed: {exc}"
        ) from exc
    if not isinstance(body.get("results"), list):
        raise GoldenEvalError(
            "GEV-E002", f"stack {stack_url} returned no results list"
        )
    return body


def fetch_healthz(stack_url: str, timeout: float = 30.0) -> dict | None:
    """Best-effort /healthz snapshot for run-report provenance (D-DRAFT-2).
    Returns None when unreachable — provenance is optional, scoring isn't.
    """
    try:
        return _get_json(stack_url.rstrip("/") + "/healthz", timeout)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        logger.warning("healthz snapshot unavailable from %s", stack_url)
        return None


# ─── Ground-truth matching ──────────────────────────────────────────


def _entry_matches(entry: GroundTruthEntry, row: dict) -> bool:
    """A result row satisfies an entry when req_ids match and every
    non-empty qualifier matches the row's field. A row without mno/release
    fields (legacy single-dataset stacks) is treated as unqualified —
    req_id alone decides. ``plan`` is authoring metadata, never matched.
    """
    if row.get("req_id") != entry.req_id:
        return False
    for attr in ("mno", "release"):
        want = getattr(entry, attr)
        have = row.get(attr)
        if want and have and want != have:
            return False
    return True


def match_ground_truth(
    ground_truth: list[GroundTruthEntry], results: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Match each ground-truth entry against ranked result rows.

    Returns (hits, misses). A hit records the best (lowest) matching rank:
    ``{"req_id", "mno", "release", "rank"}``. Entries match independently —
    row exclusivity is deliberately not enforced (a bare entry and a
    qualified entry for the same req_id may both match one row; validation
    already rejects true duplicates).
    """
    hits: list[dict] = []
    misses: list[dict] = []
    for entry in ground_truth:
        best_rank: int | None = None
        for i, row in enumerate(results):
            if _entry_matches(entry, row):
                rank = int(row.get("rank", i + 1))
                if best_rank is None or rank < best_rank:
                    best_rank = rank
        record = {
            "req_id": entry.req_id,
            "mno": entry.mno,
            "release": entry.release,
        }
        if best_rank is None:
            misses.append(record)
        else:
            hits.append({**record, "rank": best_rank})
    return hits, misses


def recall_at(hits: list[dict], gt_count: int, k: int) -> float:
    """Fraction of ground-truth entries hit at rank <= k."""
    if gt_count <= 0:
        return 0.0
    return sum(1 for h in hits if h["rank"] <= k) / gt_count


# ─── Stage 1 ────────────────────────────────────────────────────────


@dataclass
class Stage1Result:
    """Per-sample Stage-1 outcome. Full detail (req_ids, ranks) is run-dir
    material — compact summaries derive counts/percentages only (NFR-8).
    """

    sample_id: str
    recall: float
    hits: list[dict] = field(default_factory=list)
    misses: list[dict] = field(default_factory=list)
    retrieved: int = 0
    effective_top_k: int = 0
    mode: str = ""
    resolved_cells: list[str] = field(default_factory=list)
    # Every retrieved req_id in rank order (deduped) — Stage-2 pins these,
    # not just the ground-truth hits (D-DRAFT-3).
    retrieved_req_ids: list[str] = field(default_factory=list)

    @property
    def gt_count(self) -> int:
        return len(self.hits) + len(self.misses)

    def recall_at(self, k: int) -> float:
        return recall_at(self.hits, self.gt_count, k)

    def to_dict(self) -> dict:
        return {
            "sample_id": self.sample_id,
            "recall": round(self.recall, 4),
            "recall_at_5": round(self.recall_at(5), 4),
            "recall_at_10": round(self.recall_at(10), 4),
            "hits": self.hits,
            "misses": self.misses,
            "retrieved": self.retrieved,
            "effective_top_k": self.effective_top_k,
            "mode": self.mode,
            "resolved_cells": self.resolved_cells,
            "retrieved_req_ids": self.retrieved_req_ids,
        }


def run_stage1(
    sample: GoldenSample,
    stack_url: str,
    top_k: int | None = None,
    label: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Stage1Result:
    """Score one sample's retrieval recall against one stack.

    Requires non-empty ground truth (GEV-W001 otherwise — status gating
    across a whole run belongs to the batch runner, but an entry-less
    sample can't be scored at all).
    """
    if not sample.ground_truth:
        raise GoldenEvalError(
            "GEV-W001",
            f"sample {sample.sample_id} has no ground truth; not scorable",
        )
    body = query_stack(
        stack_url, sample.query, top_k=top_k, label=label, timeout=timeout
    )
    results = body["results"]
    hits, misses = match_ground_truth(sample.ground_truth, results)
    total = len(sample.ground_truth)
    seen: set[str] = set()
    retrieved_req_ids: list[str] = []
    for row in results:
        rid = str(row.get("req_id", ""))
        if rid and rid not in seen:
            seen.add(rid)
            retrieved_req_ids.append(rid)
    return Stage1Result(
        sample_id=sample.sample_id,
        recall=(len(hits) / total) if total else 0.0,
        hits=hits,
        misses=misses,
        retrieved=len(results),
        effective_top_k=int(body.get("effective_top_k", body.get("top_k", 0))),
        mode=str(body.get("mode", "")),
        resolved_cells=[str(c) for c in body.get("resolved_cells", [])],
        retrieved_req_ids=retrieved_req_ids,
    )


# ─── Stage 2 ────────────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_JUDGE_FILE_RE = re.compile(r"^judge_v(\d+)\.txt$")


def load_judge_prompt(
    version: str | None = None, prompts_dir: Path | None = None
) -> tuple[str, str]:
    """Return ``(version, prompt_text)``. Default: the highest
    ``judge_v<N>.txt`` under the committed prompts dir. Passing an explicit
    version pins it (needed to reproduce an old report's scoring).
    """
    pdir = prompts_dir or _PROMPTS_DIR
    if version:
        path = pdir / f"judge_{version}.txt"
        if not path.is_file():
            raise GoldenEvalError(
                "GEV-E004", f"judge prompt {path.name} not found in {pdir}"
            )
        return version, path.read_text(encoding="utf-8")
    best: tuple[int, Path] | None = None
    for p in pdir.glob("judge_v*.txt"):
        m = _JUDGE_FILE_RE.match(p.name)
        if m and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), p)
    if best is None:
        raise GoldenEvalError(
            "GEV-E004", f"no judge_v<N>.txt prompt found in {pdir}"
        )
    return f"v{best[0]}", best[1].read_text(encoding="utf-8")


def _parse_judge_verdict(raw: str) -> dict:
    """Extract the JSON verdict from a judge completion. Raises ValueError
    on anything unusable (caller wraps into GEV-E004).
    """
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in judge response")
    data = json.loads(raw[start : end + 1])
    score = data.get("score")
    if not isinstance(score, (int, float)):
        raise ValueError("judge verdict has no numeric score")
    return {
        "score": max(0.0, min(10.0, float(score))),
        "missing": [str(s) for s in data.get("missing", [])],
        "contradicting": [str(s) for s in data.get("contradicting", [])],
    }


@dataclass
class Stage2Result:
    """Per-sample Stage-2 outcome. `missing` / `contradicting` lists are
    run-dir material only — compact summaries carry numbers (NFR-8).
    """

    sample_id: str
    score: float | None
    judge_version: str = ""
    missing: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)
    pinned_count: int = 0
    candidate_chars: int = 0
    skipped_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "sample_id": self.sample_id,
            "score": self.score,
            "judge_version": self.judge_version,
            "missing": self.missing,
            "contradicting": self.contradicting,
            "pinned_count": self.pinned_count,
            "candidate_chars": self.candidate_chars,
            "skipped_reason": self.skipped_reason,
        }


def run_stage2(
    sample: GoldenSample,
    stage1: Stage1Result,
    query_pipeline,
    judge,
    judge_prompt: tuple[str, str],
) -> Stage2Result:
    """Regenerate from Stage-1's retrieved chunks and judge vs golden.

    ``query_pipeline`` is a `QueryPipeline` (duck-typed: `.query(text,
    pinned_chunk_ids=...) -> QueryResponse`); ``judge`` is an `LLMProvider`.
    """
    if not (sample.golden_response or "").strip():
        raise GoldenEvalError(
            "GEV-W001",
            f"sample {sample.sample_id} has no golden_response; "
            "Stage-2 not scorable",
        )
    version, template = judge_prompt
    if not stage1.retrieved_req_ids:
        return Stage2Result(
            sample_id=sample.sample_id,
            score=None,
            judge_version=version,
            skipped_reason="stage1 retrieved nothing to synthesize from",
        )
    pinned = [f"req:{rid}" for rid in stage1.retrieved_req_ids]
    response = query_pipeline.query(sample.query, pinned_chunk_ids=pinned)
    candidate = (getattr(response, "answer", "") or "").strip()
    if not candidate:
        raise GoldenEvalError(
            "GEV-E003",
            f"pinned synthesis produced no answer for {sample.sample_id} "
            f"(first pin {pinned[0]}) — are the chunks in the NORA store?",
        )
    prompt = (
        template.replace("<<QUERY>>", sample.query)
        .replace("<<GOLDEN>>", sample.golden_response or "")
        .replace("<<CANDIDATE>>", candidate)
    )
    try:
        raw = judge.complete(prompt, temperature=0.0)
        verdict = _parse_judge_verdict(raw)
    except GoldenEvalError:
        raise
    except Exception as exc:
        raise GoldenEvalError(
            "GEV-E004",
            f"judge failed for {sample.sample_id}: {exc}",
        ) from exc
    return Stage2Result(
        sample_id=sample.sample_id,
        score=verdict["score"],
        judge_version=version,
        missing=verdict["missing"],
        contradicting=verdict["contradicting"],
        pinned_count=len(pinned),
        candidate_chars=len(candidate),
    )


# ─── Run report ─────────────────────────────────────────────────────


@dataclass
class GoldenRunReport:
    """One stack's run. Full detail goes to the run dir (proprietary);
    `compact_report()` is the chat-pasteable GEV block (counts only).
    """

    stack_label: str
    stack_url: str
    started_at: str
    judge_version: str = ""
    healthz: dict | None = None
    stage1: list[Stage1Result] = field(default_factory=list)
    stage2: list[Stage2Result] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def run_id(self) -> str:
        ts = re.sub(r"[^0-9T]", "", self.started_at)
        return f"{ts}-{self.stack_label}"

    def _judge_scores(self) -> list[float]:
        return [r.score for r in self.stage2 if r.score is not None]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "stack_label": self.stack_label,
            "stack_url": self.stack_url,
            "started_at": self.started_at,
            "judge_version": self.judge_version,
            "healthz": self.healthz,
            "stage1": [r.to_dict() for r in self.stage1],
            "stage2": [r.to_dict() for r in self.stage2],
            "errors": self.errors,
        }

    def compact_report(self, env_name: str = "") -> str:
        """The GEV compact block (strand golden-eval design note). No
        sample content — counts, percentages, and error codes only.
        """
        lines = [
            f"GEV {env_name or 'standalone'} {self.stack_label} "
            f"{self.started_at} judge={self.judge_version or '-'}"
        ]
        if self.stage1:
            n = len(self.stage1)
            avg = sum(r.recall for r in self.stage1) / n
            r5 = sum(r.recall_at(5) for r in self.stage1) / n
            r10 = sum(r.recall_at(10) for r in self.stage1) / n
            full = sum(1 for r in self.stage1 if r.recall >= 1.0)
            zero = sum(1 for r in self.stage1 if r.recall == 0.0)
            lines.append(
                f"s1: n={n} recall_avg={avg:.2f} r@5={r5:.2f} "
                f"r@10={r10:.2f} full={full} zero={zero}"
            )
        else:
            lines.append("s1: n=0")
        scores = self._judge_scores()
        if scores:
            lines.append(
                f"s2: n={len(scores)} judge_avg={sum(scores)/len(scores):.1f} "
                f"judge_med={statistics.median(scores):.1f}"
            )
        else:
            lines.append("s2: n=0")
        if self.errors:
            counts: dict[str, int] = {}
            for e in self.errors:
                counts[e["code"]] = counts.get(e["code"], 0) + 1
            lines.append(
                "err: " + ", ".join(
                    f"{c}({n})" for c, n in sorted(counts.items())
                )
            )
        else:
            lines.append("err: none")
        return "\n".join(lines)


def format_ab_delta(a: GoldenRunReport, b: GoldenRunReport) -> str:
    """One delta line comparing two runs (same samples, same judge version
    for a meaningful s2 delta)."""

    def _avg(vals: list[float]) -> float | None:
        return sum(vals) / len(vals) if vals else None

    ra = _avg([r.recall for r in a.stage1])
    rb = _avg([r.recall for r in b.stage1])
    ja, jb = _avg(a._judge_scores()), _avg(b._judge_scores())
    parts = []
    if ra is not None and rb is not None:
        parts.append(f"recall={rb - ra:+.2f}")
    if ja is not None and jb is not None:
        marker = "" if a.judge_version == b.judge_version else " (JUDGE MISMATCH)"
        parts.append(f"judge={jb - ja:+.1f}{marker}")
    return (
        f"delta {a.stack_label}->{b.stack_label}: "
        + (" ".join(parts) if parts else "n/a")
    )


def run_all(
    samples: list[GoldenSample],
    stack_url: str,
    stack_label: str,
    started_at: str,
    query_pipeline=None,
    judge=None,
    judge_prompt: tuple[str, str] | None = None,
    top_k: int | None = None,
    label: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> GoldenRunReport:
    """Batch run against one stack. Stage-1 for every non-draft sample;
    Stage-2 additionally for golden-ready samples when a pipeline + judge
    are supplied. Per-sample failures are recorded in ``errors`` and the
    run continues — recorded, never silent (fail-loud posture).
    """
    report = GoldenRunReport(
        stack_label=stack_label,
        stack_url=stack_url,
        started_at=started_at,
        judge_version=judge_prompt[0] if judge_prompt else "",
        healthz=fetch_healthz(stack_url),
    )
    stage2_enabled = bool(query_pipeline and judge and judge_prompt)
    for sample in samples:
        if sample.status == STATUS_DRAFT:
            report.errors.append({
                "sample_id": sample.sample_id,
                "code": "GEV-W001",
                "message": "status draft — not run",
            })
            continue
        try:
            s1 = run_stage1(
                sample, stack_url, top_k=top_k, label=label, timeout=timeout
            )
            report.stage1.append(s1)
        except GoldenEvalError as exc:
            report.errors.append({
                "sample_id": sample.sample_id,
                "code": exc.code,
                "message": exc.message,
            })
            continue
        if stage2_enabled and sample.status == STATUS_GOLDEN_READY:
            try:
                report.stage2.append(
                    run_stage2(sample, s1, query_pipeline, judge, judge_prompt)
                )
            except GoldenEvalError as exc:
                report.errors.append({
                    "sample_id": sample.sample_id,
                    "code": exc.code,
                    "message": exc.message,
                })
    return report


def write_run(env_dir: Path, report: GoldenRunReport, env_name: str = "") -> Path:
    """Persist a run under <env_dir>/eval/golden/runs/<run_id>/ — full JSON
    (proprietary detail) plus the compact text block. Returns the run dir.
    """
    rdir = runs_dir(env_dir) / report.run_id
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "report.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (rdir / "report.txt").write_text(
        report.compact_report(env_name) + "\n", encoding="utf-8"
    )
    return rdir
