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
    text_chars: int | None = None,
) -> dict:
    """POST /sira-query on a stack. Raises GoldenEvalError (GEV-E002) on
    any transport or protocol failure — a sample is never silently skipped.

    ``text_chars`` opts into full row text (the service's Path-B knob) —
    the SIRA-lane Stage-2 synthesizes over the rows Stage-1 actually
    retrieved, so those rows must carry their chunk text.
    """
    payload: dict = {"query": query}
    if top_k:
        payload["top_k"] = top_k
    if label:
        payload["label"] = label
    if text_chars:
        payload["text_chars"] = text_chars
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


# ─── Stack stamp ────────────────────────────────────────────────────


@dataclass
class StackStamp:
    """Runtime-captured identity of the stack a run scored against.

    Stack NAMES (nora1/nora2) are mutable pointers — a stack gets
    repointed, rebuilt, or re-promoted and keeps its name, and serve
    labels may be REUSED for cosmetic re-promotions. The stamp
    dereferences the pointer at run time and freezes the result into
    the run report, so two rows are comparable exactly when their
    stamps say so — variant identity is captured from the live stack,
    not from documentation about it (recipe files stay the authoring
    convention; the stamp is the attribution record).

    Fields are best-effort: captured from /healthz where the stack
    advertises them (canonical keys below), from caller-supplied
    knowledge otherwise, and left empty when unavailable — an empty
    field means "not comparable on this axis", never a guess.

      * ``serve_label``      — label NAME (human context; reusable, so
                               not identity by itself).
      * ``data_fingerprint`` — content digest of the served cells,
                               advertised by the stack (healthz key
                               ``data_fingerprint``). The real data
                               identity: survives label reuse.
      * ``code_version``     — git sha / image version of the serving
                               code (healthz key ``code_version``).
      * ``sira_prompt_scheme`` — enrichment prompt scheme baked into
                               the SIRA build the served data came from
                               (e.g. a single corpus-derived prompt vs
                               per-MNO/per-plan prompts). Explanatory:
                               the fingerprint already separates the
                               data; the scheme names WHY it differs.
      * ``answer_prompt_version`` — the answer-generation prompt used
                               for Stage-2 regeneration (caller-known;
                               not the SIRA-side prompts).
      * ``llm_identity``     — the model that generates Stage-2
                               responses (the judge is stamped
                               separately on the report).
      * ``retrieval_knobs``  — request-shaping settings (top_k, ...).
      * ``fallback_used_snapshot`` — the stack's refusal-fallback
                               use counter at run time (per-ROW
                               fallback attribution needs the
                               generation path to expose which model
                               answered — deferred until it does).
    """

    serve_label: str = ""
    data_fingerprint: str = ""
    # Diagnostic, NOT part of the comparability keys: the combined
    # fingerprint carries comparability; the per-cell map answers
    # "which cell changed" when two runs' keys differ. Captured into
    # the stamp (field-found: published-but-not-captured values keep
    # turning out to matter — same shape as the knobs gap).
    data_fingerprint_cells: dict = field(default_factory=dict)
    code_version: str = ""
    sira_prompt_scheme: str = ""
    answer_prompt_version: str = ""
    llm_identity: str = ""
    retrieval_knobs: dict = field(default_factory=dict)
    fallback_used_snapshot: int | None = None
    # Eval-SET identity (field-found: the golden set drifted mid-strand
    # as curation landed, so byte-identical stack stamps had scored
    # DIFFERENT sample sets — the stack-side false-comparable reopened
    # on the data-in side; third instance of the
    # published-but-not-captured pattern). Two digests, not one: a
    # golden-response re-curation must invalidate Stage-2
    # comparability WITHOUT falsely invalidating Stage-1, which never
    # reads responses.
    eval_set_count: int = 0
    eval_set_digest: str = ""          # non-draft: (id, query, ground truth)
    eval_set_stage2_digest: str = ""   # golden-ready: + golden_response

    _HEALTHZ_KEYS = {
        "serve_label": "serve_label",
        "data_fingerprint": "data_fingerprint",
        "code_version": "code_version",
        "sira_prompt_scheme": "sira_prompt_scheme",
    }

    # Fallback knob harvest for stacks that predate the owned
    # ``retrieval_knobs`` healthz sub-dict (the sub-dict is the
    # contract — the service owns its knob list; this whitelist only
    # keeps older deployments from stamping an empty knob set, which
    # field validation showed declares different stacks comparable).
    _LEGACY_KNOB_KEYS = (
        "max_df_ratio", "max_df_absolute", "expansion_weight",
        "query_enrich_enabled", "query_enrich_temperature",
        "default_top_k", "rerank_top_n", "rerank_enabled",
        "rerank_backend", "rerank_batch_size", "rerank_batch_max_tokens",
        "rerank_max_tokens", "fusion_balanced", "scale_topk_by_cells",
        "fanout_enabled", "fanout_per_hit", "use_latest_runs",
        "query_enrich_run_pinned", "rerank_run_pinned",
    )

    @classmethod
    def from_healthz(
        cls,
        healthz: dict | None,
        *,
        top_k: int | None = None,
        answer_prompt_version: str = "",
        llm_identity: str = "",
        sira_prompt_scheme: str = "",
    ) -> "StackStamp":
        """Build a stamp from a healthz snapshot + caller-known fields.

        Caller-supplied values win over healthz (the caller knows what
        it configured; healthz fills what only the stack knows). Keys
        the stack does not advertise yet stay empty — the serving side
        adopts the canonical keys incrementally.
        """
        h = healthz or {}
        stamp = cls(
            answer_prompt_version=answer_prompt_version,
            llm_identity=llm_identity or str(h.get("shim_model") or ""),
            sira_prompt_scheme=(
                sira_prompt_scheme or str(h.get("sira_prompt_scheme") or "")
            ),
        )
        for attr, key in cls._HEALTHZ_KEYS.items():
            if not getattr(stamp, attr):
                val = h.get(key)
                if isinstance(val, (str, int)):
                    setattr(stamp, attr, str(val))
        cells_map = h.get("data_fingerprint_cells")
        if isinstance(cells_map, dict):
            stamp.data_fingerprint_cells = dict(cells_map)
        knobs = h.get("retrieval_knobs")
        if isinstance(knobs, dict) and knobs:
            stamp.retrieval_knobs.update(knobs)
        else:
            for key in cls._LEGACY_KNOB_KEYS:
                if key in h and isinstance(h[key], (str, int, float, bool)):
                    stamp.retrieval_knobs[key] = h[key]
        if top_k is not None:
            # The per-run request override — kept distinct from the
            # stack's own default_top_k knob.
            stamp.retrieval_knobs["top_k_requested"] = top_k
        fb = h.get("refusal_fallback")
        if isinstance(fb, dict) and isinstance(fb.get("used"), int):
            stamp.fallback_used_snapshot = fb["used"]
        return stamp

    def set_eval_set(self, samples: list) -> None:
        """Freeze the scored sample set's identity into the stamp.

        Called with the samples the run will score. Stage-1 identity
        covers every non-draft sample's (id, query, ground truth);
        Stage-2 identity covers golden-ready samples plus their
        curated responses. Canonical JSON, sorted by sample_id —
        stable across load order and dict churn.
        """
        import hashlib

        def _digest(rows: list) -> str:
            blob = json.dumps(rows, sort_keys=True, ensure_ascii=False)
            return hashlib.sha256(blob.encode()).hexdigest()

        scorable = sorted(
            (s for s in samples if s.status != STATUS_DRAFT),
            key=lambda s: s.sample_id,
        )
        self.eval_set_count = len(scorable)
        self.eval_set_digest = _digest([
            [s.sample_id, s.query,
             [e.to_dict() for e in s.ground_truth]]
            for s in scorable
        ]) if scorable else ""
        golden_ready = [s for s in scorable if s.status == STATUS_GOLDEN_READY]
        self.eval_set_stage2_digest = _digest([
            [s.sample_id, s.query,
             [e.to_dict() for e in s.ground_truth],
             s.golden_response or ""]
            for s in golden_ready
        ]) if golden_ready else ""

    def _knobs_key(self) -> tuple:
        return tuple(sorted(
            (k, str(v)) for k, v in self.retrieval_knobs.items()
        ))

    def stage1_key(self) -> tuple:
        """Two runs' Stage-1 scores are comparable iff these match.

        The fingerprint is the data identity; the label name is only
        the fallback when the stack doesn't advertise one (weaker —
        label reuse can alias different content under one key). The
        eval-set digest makes a changed golden set break comparability
        the same way changed served data does.
        """
        return (
            self.data_fingerprint or self.serve_label,
            self.code_version,
            self._knobs_key(),
            self.eval_set_digest,
        )

    def stage2_key(self) -> tuple:
        """Stage-2 comparability: Stage-1 axes plus the generation
        side and the golden-response-bearing set identity. The judge
        version lives on the report and must also match — callers
        compare (stage2_key, judge_version)."""
        return self.stage1_key() + (
            self.answer_prompt_version,
            self.llm_identity,
            self.eval_set_stage2_digest,
        )

    def to_dict(self) -> dict:
        return {
            "serve_label": self.serve_label,
            "data_fingerprint": self.data_fingerprint,
            "data_fingerprint_cells": self.data_fingerprint_cells,
            "code_version": self.code_version,
            "sira_prompt_scheme": self.sira_prompt_scheme,
            "answer_prompt_version": self.answer_prompt_version,
            "llm_identity": self.llm_identity,
            "retrieval_knobs": self.retrieval_knobs,
            "fallback_used_snapshot": self.fallback_used_snapshot,
            "eval_set_count": self.eval_set_count,
            "eval_set_digest": self.eval_set_digest,
            "eval_set_stage2_digest": self.eval_set_stage2_digest,
        }

    def compact_line(self) -> str:
        """One redaction-safe line for the GEV block — identifiers and
        short digests only, empty fields omitted, never content."""
        parts = []
        if self.data_fingerprint:
            parts.append(f"fp={self.data_fingerprint[:12]}")
        elif self.serve_label:
            parts.append(f"label={self.serve_label}")
        if self.data_fingerprint_cells:
            parts.append(f"cells={len(self.data_fingerprint_cells)}")
        if self.code_version:
            parts.append(f"code={self.code_version[:12]}")
        if self.sira_prompt_scheme:
            parts.append(f"scheme={self.sira_prompt_scheme}")
        if self.answer_prompt_version:
            parts.append(f"aprompt={self.answer_prompt_version}")
        if self.llm_identity:
            parts.append(f"llm={self.llm_identity}")
        if self.retrieval_knobs:
            # Digest, not a dump — the harvested knob set runs to ~20
            # entries; the full dict lives in report.json. Same digest
            # = same knob tuple, which is what the line is for.
            import hashlib
            digest = hashlib.sha256(
                repr(self._knobs_key()).encode()
            ).hexdigest()[:8]
            parts.append(f"knobs={len(self.retrieval_knobs)}@{digest}")
        if self.eval_set_digest:
            parts.append(
                f"set={self.eval_set_count}@{self.eval_set_digest[:8]}"
            )
        if self.fallback_used_snapshot is not None:
            parts.append(f"fb_used={self.fallback_used_snapshot}")
        return "id: " + (" ".join(parts) if parts else "(unstamped)")


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
    # TRANSIENT (never serialized — proprietary chunk text stays out of
    # to_dict): the retrieved rows with their text, retained only when
    # ``want_row_text=True`` so the SIRA-lane Stage-2 synthesizes over
    # exactly what retrieval served.
    retrieved_rows: list[dict] = field(default_factory=list)

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
    want_row_text: bool = False,
) -> Stage1Result:
    """Score one sample's retrieval recall against one stack.

    Requires non-empty ground truth (GEV-W001 otherwise — status gating
    across a whole run belongs to the batch runner, but an entry-less
    sample can't be scored at all).

    ``want_row_text=True`` requests full row text and retains the
    retrieved rows on the result (transient, never serialized) so a
    SIRA-lane Stage-2 can synthesize over exactly what retrieval served.
    """
    if not sample.ground_truth:
        raise GoldenEvalError(
            "GEV-W001",
            f"sample {sample.sample_id} has no ground truth; not scorable",
        )
    body = query_stack(
        stack_url, sample.query, top_k=top_k, label=label, timeout=timeout,
        text_chars=_STAGE2_ROW_TEXT_CHARS if want_row_text else None,
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
    result = Stage1Result(
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
    if want_row_text:
        result.retrieved_rows = [
            {
                "req_id": str(r.get("req_id", "")),
                "title": str(r.get("title", "") or ""),
                "text": str(r.get("text", "") or ""),
                "mno": str(r.get("mno", "") or ""),
                "release": str(r.get("release", "") or ""),
            }
            for r in results if r.get("req_id")
        ]
    return result


# ─── Stage 2 ────────────────────────────────────────────────────────

# Per-row text cap requested from the serving stack when Stage-2 will
# synthesize over the retrieved rows (matches the service's own rerank
# text budget order of magnitude).
_STAGE2_ROW_TEXT_CHARS = 4000


class SiraRowsPipeline:
    """Duck-typed Stage-2 pipeline for the SIRA-only lane.

    The legacy Stage-2 path builds a ``QueryPipeline`` from
    ``out/graph`` + ``out/vectorstore`` — artifacts the SIRA-only lane
    never produces, which made Stage-2 structurally unrunnable on
    SIRA-serving stacks (field-found: all golden-ready samples skipped
    silently). This pipeline needs neither: it synthesizes with the
    SAME production ``LLMSynthesizer`` prompt over the rows Stage-1
    actually retrieved (full text via the service's ``text_chars``
    knob) — production-faithful by construction, since what retrieval
    served is what an answer would be built from. Context enrichment
    uses an empty graph (the established RAG-only degradation).

    Duck-type contract (same as ``QueryPipeline`` for the runner):
    ``query(text, pinned_chunk_ids=...) -> QueryResponse``. The runner
    binds each sample's retrieved rows via ``bind_rows`` before its
    Stage-2 call.
    """

    def __init__(self, llm, max_context_chars: int = 30000):
        import networkx as nx

        from core.src.query.context_builder import ContextBuilder
        from core.src.query.synthesizer import LLMSynthesizer

        self._builder = ContextBuilder(nx.DiGraph())
        self._synth = LLMSynthesizer(llm, max_tokens=max_context_chars // 4)
        self._max_context_chars = max_context_chars
        self._rows: list[dict] = []

    def bind_rows(self, rows: list[dict]) -> None:
        self._rows = rows or []

    def query(self, text: str, pinned_chunk_ids: list[str] | None = None):
        from core.src.query.schema import QueryIntent, QueryType, RetrievedChunk

        by_id = {f"req:{r['req_id']}": r for r in self._rows if r.get("req_id")}
        order = [p for p in (pinned_chunk_ids or []) if p in by_id]
        chunks = [
            RetrievedChunk(
                chunk_id=pid,
                text=(
                    f"{by_id[pid]['title']}\n{by_id[pid]['text']}".strip()
                ),
                metadata={
                    "req_id": by_id[pid]["req_id"],
                    "mno": by_id[pid]["mno"],
                    "release": by_id[pid]["release"],
                },
            )
            for pid in order
            if (by_id[pid]["text"] or by_id[pid]["title"])
        ]
        if not chunks:
            raise GoldenEvalError(
                "GEV-E003",
                "no retrieved-row text bound for synthesis — Stage-1 must "
                "run with want_row_text (rows carry text via text_chars)",
            )
        context = self._builder.build(
            text, chunks, QueryType.GENERAL,
            max_context_chars=self._max_context_chars,
        )
        intent = QueryIntent(raw_query=text, query_type=QueryType.GENERAL)
        return self._synth.synthesize(context, intent)


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
    stamp: StackStamp | None = None
    # Stage-2 execution status (field-found defect: a Stage-2 setup
    # failure downgraded to Stage-1-only with only a stdout line — the
    # REPORT carried no marker, so "Stage-2 never ran" was
    # indistinguishable from "ran and scored nothing").
    # stage2_mode: "" (not attempted) | "pipeline" | "sira-rows".
    stage2_mode: str = ""
    stage2_skip_reason: str = ""
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
            "stamp": self.stamp.to_dict() if self.stamp else None,
            "stage2_mode": self.stage2_mode,
            "stage2_skip_reason": self.stage2_skip_reason,
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
        if self.stamp:
            lines.append(self.stamp.compact_line())
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
            mode = f" mode={self.stage2_mode}" if self.stage2_mode else ""
            lines.append(
                f"s2: n={len(scores)} judge_avg={sum(scores)/len(scores):.1f} "
                f"judge_med={statistics.median(scores):.1f}{mode}"
            )
        elif self.stage2_skip_reason:
            lines.append(f"s2: SKIPPED ({self.stage2_skip_reason})")
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
    answer_prompt_version: str = "",
    llm_identity: str = "",
    sira_prompt_scheme: str = "",
    stage2_skip_reason: str = "",
) -> GoldenRunReport:
    """Batch run against one stack. Stage-1 for every non-draft sample;
    Stage-2 additionally for golden-ready samples when a pipeline + judge
    are supplied. Per-sample failures are recorded in ``errors`` and the
    run continues — recorded, never silent (fail-loud posture).

    ``answer_prompt_version`` / ``llm_identity`` / ``sira_prompt_scheme``
    feed the stack stamp — caller-known identity the stack cannot
    advertise (or advertises less authoritatively than the caller's own
    config). ``stage2_skip_reason`` records WHY Stage-2 was not
    attempted (caller-known setup failure) so the report — not just
    stdout — distinguishes "never ran" from "ran and scored nothing".
    """
    healthz = fetch_healthz(stack_url)
    report = GoldenRunReport(
        stack_label=stack_label,
        stack_url=stack_url,
        started_at=started_at,
        judge_version=judge_prompt[0] if judge_prompt else "",
        healthz=healthz,
        stamp=StackStamp.from_healthz(
            healthz,
            top_k=top_k,
            answer_prompt_version=answer_prompt_version,
            llm_identity=llm_identity,
            sira_prompt_scheme=sira_prompt_scheme,
        ),
    )
    report.stamp.set_eval_set(samples)
    stage2_enabled = bool(query_pipeline and judge and judge_prompt)
    sira_rows_mode = stage2_enabled and hasattr(query_pipeline, "bind_rows")
    if stage2_enabled:
        report.stage2_mode = "sira-rows" if sira_rows_mode else "pipeline"
    else:
        report.stage2_skip_reason = (
            stage2_skip_reason or "stage-2 prerequisites not supplied"
        )
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
                sample, stack_url, top_k=top_k, label=label, timeout=timeout,
                want_row_text=sira_rows_mode,
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
            if sira_rows_mode:
                query_pipeline.bind_rows(s1.retrieved_rows)
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
