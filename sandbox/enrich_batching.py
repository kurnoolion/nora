"""Batched doc-enrichment support (strand sira-enrichment-pe).

Pure logic consumed by the SIRA doc-enrich adapter via the
batched-enrich patch: per-plan batch packing under a dual token budget,
per-plan taxonomy blocks, per-MNO doc-prompt resolution, and strict
req_id-keyed response parsing with bounded re-queue.

Installed into the SIRA clone as `scripts/enrich_batching.py` by
`sandbox/install_configs.sh` (the clone is gitignored; this file is the
tracked source of truth). Stdlib + asyncio only — no sira/aiohttp
imports, so the packing/parsing logic unit-tests without the clone.

Batch = transport, not state: the adapter's per-req trace rows remain
the resume unit; a batch failure re-queues its requirements into fresh
batches (bounded), then falls back to the legacy per-req fail path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

PLAN_RE = re.compile(r"^\*\*plan\*\*: *(.+?) *$", re.M)

# The prologue the design fixes for the taxonomy block (section 2).
TAXONOMY_PROLOGUE = (
    "The following is the feature taxonomy of the plan these requirements "
    "belong to — the features, key concepts, and keywords extracted from "
    "the plan's specification. Use it as domain context when generating "
    "search phrases.\n"
)


def plan_of(text: str) -> str:
    """Plan id stamped on a corpus row. Composite `plan_id / plan_name`
    stamps yield the plan_id part; unstamped rows yield ""."""
    m = PLAN_RE.search(text or "")
    if not m:
        return ""
    return m.group(1).split(" / ", 1)[0].strip()


def is_batched_template(template: str) -> bool:
    """A doc prompt in the batched shape (skill step-7 contract) carries
    {requirements}; the legacy single-req shape carries {doc_text}."""
    return "{requirements}" in template


def resolve_doc_prompt(prompts_dir: str, mno: str) -> str | None:
    """Highest-version `doc_requirement_<MNO>_*.txt` under prompts_dir,
    or None (caller falls back to the config's legacy prompt)."""
    if not prompts_dir or not mno:
        return None
    hits = sorted(Path(prompts_dir).glob(f"doc_requirement_{mno}_*.txt"))
    return str(hits[-1]) if hits else None


def load_taxonomy_block(taxonomy_dir: str, plan_id: str) -> str:
    """Prologue + the plan's whole `<plan_id>_features.json` content,
    or "" when unavailable (fail-soft — enrichment proceeds without)."""
    if not taxonomy_dir or not plan_id:
        return ""
    p = Path(taxonomy_dir) / f"{plan_id}_features.json"
    try:
        content = p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not content:
        return ""
    return f"{TAXONOMY_PROLOGUE}\n{content}\n"


def est_tokens(text: str, chars_per_token: float) -> int:
    return int(len(text) / chars_per_token) + 1


def format_requirements(ids: list[str], texts: list[str]) -> str:
    parts = [f"### req_id: {rid}\n{txt}\n" for rid, txt in zip(ids, texts)]
    return "\n".join(parts)


# Reasoning-sentinel — for endpoints whose UNTAGGED chain-of-thought leaks
# into `content` (no <think> tags to strip, and un-fenced draft JSON defeats
# brace-scanning). Same marker + opt-in pattern as NORA's
# core/src/llm/openai_provider.py (NORA_LLM_REASONING_SENTINEL); duplicated
# here because sandbox never imports core (D-111 boundary).
FINAL_ANSWER_MARKER = "===FINAL_ANSWER==="

SENTINEL_INSTRUCTION = (
    "\n\nOUTPUT FORMAT: You may reason first if needed, but you MUST then "
    f"print a line containing exactly {FINAL_ANSWER_MARKER} and put ONLY "
    f"the JSON object after it. Anything before {FINAL_ANSWER_MARKER} is "
    "discarded."
)


def compose_prompt(template: str, taxonomy_block: str,
                   ids: list[str], texts: list[str], max_n: int,
                   sentinel: bool = False) -> str:
    prompt = template.format(taxonomy_block=taxonomy_block,
                             requirements=format_requirements(ids, texts),
                             max_n=max_n)
    return prompt + SENTINEL_INSTRUCTION if sentinel else prompt


@dataclass
class BatchConfig:
    """Dual token budget (design section 3). The response side can bind
    before the prompt side: reserve / resp_tokens_per_req caps the reqs
    a batch may carry regardless of how small their texts are."""
    prompt_cap_tokens: int = 50_000
    context_tokens: int = 64_000
    resp_tokens_per_req: int = 90       # measured p95 86, rounded up
    chars_per_token: float = 3.5
    max_retries: int = 2                # re-queue rounds for missing/errored reqs
    batch_concurrency: int = 2
    debug_raw: bool = False             # opt-in: log response head on anomalies
                                        # (raw text carries corpus content — keep
                                        # such logs local, never in shared reports)
    reasoning_sentinel: bool = False    # opt-in: append the FINAL_ANSWER_MARKER
                                        # instruction + parse only after the marker
                                        # (untagged-thinking endpoints)

    @property
    def response_reserve(self) -> int:
        return self.context_tokens - self.prompt_cap_tokens

    @classmethod
    def from_env(cls, env: dict) -> "BatchConfig":
        def _get(name, cast, default):
            raw = env.get(f"NORA_SIRA_BATCH_{name}", "")
            try:
                return cast(raw) if raw else default
            except ValueError:
                return default
        return cls(
            prompt_cap_tokens=_get("PROMPT_CAP_TOKENS", int, 50_000),
            context_tokens=_get("CONTEXT_TOKENS", int, 64_000),
            resp_tokens_per_req=_get("RESP_TOKENS_PER_REQ", int, 90),
            chars_per_token=_get("CHARS_PER_TOKEN", float, 3.5),
            max_retries=_get("RETRIES", int, 2),
            batch_concurrency=_get("CONCURRENCY", int, 2),
            debug_raw=_get("DEBUG_RAW",
                           lambda s: s.strip().lower() not in ("", "0", "false"),
                           False),
            reasoning_sentinel=_get(
                "REASONING_SENTINEL",
                lambda s: s.strip().lower() not in ("", "0", "false"),
                False),
        )


@dataclass
class Batch:
    plan_id: str
    ids: list[str]
    texts: list[str]
    prompt_tokens: int          # estimate incl. header
    closed_by: str              # "prompt" | "response" | "end"
    oversized: bool = False


def make_plan_batches(plan_id: str, reqs: list[tuple[str, str]],
                      header_tokens: int, cfg: BatchConfig) -> list[Batch]:
    """Pack one plan's reqs (in order) into batches under BOTH budgets.
    Requirements are never fragmented; a single req whose text alone
    busts the prompt budget ships as a solo oversized batch."""
    text_budget = cfg.prompt_cap_tokens - header_tokens
    max_reqs = max(1, cfg.response_reserve // max(1, cfg.resp_tokens_per_req))
    batches: list[Batch] = []
    ids: list[str] = []
    texts: list[str] = []
    used = 0

    def _close(closed_by: str, oversized: bool = False) -> None:
        nonlocal ids, texts, used
        if ids:
            batches.append(Batch(plan_id, ids, texts, header_tokens + used,
                                 closed_by, oversized))
            ids, texts, used = [], [], 0

    for rid, text in reqs:
        t = est_tokens(text, cfg.chars_per_token)
        if t > text_budget:
            # doesn't fit even alone — solo oversized, never fragmented
            _close("prompt")
            batches.append(Batch(plan_id, [rid], [text], header_tokens + t,
                                 "prompt", oversized=True))
            continue
        if used + t > text_budget:
            _close("prompt")
        elif len(ids) >= max_reqs:
            _close("response")
        ids.append(rid)
        texts.append(text)
        used += t
    _close("end")
    return batches


def group_reqs_by_plan(items: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    plans: dict[str, list[tuple[str, str]]] = {}
    for rid, text in items:
        plans.setdefault(plan_of(text), []).append((rid, text))
    return plans


@dataclass
class ParseResult:
    phrases_by_id: dict[str, list[str]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    error: str = ""


_THINK_SPAN_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def _json_candidates(raw: str) -> list[str]:
    """Candidate JSON substrings, most-likely-final-answer first.

    Reasoning models emit thinking into `content` — sometimes including a
    fenced DRAFT JSON — before the real answer. So: strip complete
    <think>…</think> spans, then prefer fenced blocks LAST-first (the
    final answer follows the reasoning), then the outermost brace span
    (catches bare JSON after un-tagged reasoning prose)."""
    raw = _THINK_SPAN_RE.sub("", raw)
    out = [m.group(1) for m in
           re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)][::-1]
    first, last = raw.find("{"), raw.rfind("}")
    if 0 <= first < last:
        out.append(raw[first:last + 1])
    return out


def parse_batch_response(raw: str, expected_ids: list[str]) -> ParseResult:
    """Strict req_id-keyed JSON per the template's output spec. Unknown
    keys are surfaced (not applied); expected ids absent from the
    response come back in `missing` for the re-queue path."""
    raw = raw or ""
    marker_at = raw.rfind(FINAL_ANSWER_MARKER)
    if marker_at != -1:                 # sentinel present → answer follows it
        raw = raw[marker_at + len(FINAL_ANSWER_MARKER):]
    obj = None
    for cand in _json_candidates(raw):
        try:
            parsed = json.loads(cand)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            obj = parsed
            break
    if obj is None:
        return ParseResult(missing=list(expected_ids),
                           error="no JSON object in response")
    expected = set(expected_ids)
    res = ParseResult()
    for k, v in obj.items():
        if k not in expected:
            res.extra.append(str(k))
            continue
        if isinstance(v, list):
            res.phrases_by_id[k] = [str(p).strip() for p in v
                                    if isinstance(p, (str, int, float))
                                    and str(p).strip()]
    res.missing = [rid for rid in expected_ids if rid not in res.phrases_by_id]
    return res


async def run_batched(
    *,
    items: list[tuple[str, str]],
    template: str,
    max_n: int,
    taxonomy_dir: str,
    cfg: BatchConfig,
    llm_call: Callable[[str, int], Awaitable[str]],
    filter_fn: Callable[[list[str]], tuple[list[str], dict]],
    write_enrich: Callable[[str, list[str]], Awaitable[None]],
    write_kept: Callable[[dict], Awaitable[None]],
    write_failed: Callable[[dict], Awaitable[None]],
    write_batch: Callable[[dict], Awaitable[None]],
    logger: logging.Logger,
) -> dict[str, int]:
    """Drive batched enrichment over `items` ((doc_id, text), already
    resume-filtered by the caller). Per-req trace rows mirror the legacy
    statuses (ok / no_phrases / all_filtered) plus batch-path statuses
    (batch_error / missing_in_batch_response); every row carries its
    batch_id. Missing/errored reqs re-queue into fresh batches up to
    cfg.max_retries extra rounds."""
    summary = {"batches": 0, "oversized": 0, "enriched": 0, "failed": 0,
               "proposed": 0, "kept": 0, "filtered": 0, "errors": 0,
               "requeued": 0}
    tax_cache: dict[str, str] = {}
    header_cache: dict[str, int] = {}
    sem = asyncio.Semaphore(max(1, cfg.batch_concurrency))
    batch_seq = 0

    def _header(plan_id: str) -> tuple[str, int]:
        if plan_id not in header_cache:
            block = load_taxonomy_block(taxonomy_dir, plan_id)
            if not block and taxonomy_dir:
                logger.warning("No taxonomy for plan %r — enriching without",
                               plan_id)
            tax_cache[plan_id] = block
            header_cache[plan_id] = est_tokens(
                compose_prompt(template, block, [], [], max_n,
                               sentinel=cfg.reasoning_sentinel),
                cfg.chars_per_token)
        return tax_cache[plan_id], header_cache[plan_id]

    async def _fail(doc_id: str, status: str, batch_id: str, **extra) -> None:
        summary["failed"] += 1
        await write_failed({"doc_id": doc_id, "status": status,
                            "batch_id": batch_id, **extra})

    async def _run_one(batch: Batch, batch_id: str,
                       requeue: list[tuple[str, str]],
                       attempt: int) -> None:
        block, _ = _header(batch.plan_id)
        prompt = compose_prompt(template, block, batch.ids, batch.texts, max_n,
                                sentinel=cfg.reasoning_sentinel)
        t0 = time.time()
        try:
            async with sem:
                raw = await llm_call(prompt, cfg.response_reserve)
        except Exception as e:                                    # noqa: BLE001
            summary["errors"] += 1
            logger.warning("batch %s (%d reqs) LLM error: %s",
                           batch_id, len(batch.ids), e)
            if attempt < cfg.max_retries:
                requeue.extend(zip(batch.ids, batch.texts))
            else:
                for rid in batch.ids:
                    await _fail(rid, "batch_error", batch_id, error=str(e))
            await write_batch({"batch_id": batch_id, "plan": batch.plan_id,
                               "n_reqs": len(batch.ids), "status": "error",
                               "error": str(e), "attempt": attempt,
                               "ms": int((time.time() - t0) * 1000)})
            return
        ms = int((time.time() - t0) * 1000)
        res = parse_batch_response(raw, batch.ids)
        if res.extra:
            logger.warning("batch %s (plan %s): %d unknown req_ids in response",
                           batch_id, batch.plan_id, len(res.extra))
            if cfg.debug_raw:
                lines = (raw or "").splitlines()
                head = "\n".join(lines[:2])[:400]
                tail = "\n".join(lines[-2:])[:400]
                logger.warning(
                    "batch %s plan %s (NORA_SIRA_BATCH_DEBUG_RAW) marker %s — "
                    "head:\n%s\n--- tail:\n%s"
                    "\n--- requested ids (first 20): %s"
                    "\n--- unknown ids  (first 20): %s",
                    batch_id, batch.plan_id,
                    "PRESENT" if FINAL_ANSWER_MARKER in (raw or "") else "ABSENT",
                    head, tail, batch.ids[:20], res.extra[:20])
        texts_by_id = dict(zip(batch.ids, batch.texts))
        for rid, phrases in res.phrases_by_id.items():
            if not phrases:
                await _fail(rid, "no_phrases", batch_id, ms=ms)
                continue
            summary["proposed"] += len(phrases)
            kept, fstats = filter_fn(phrases)
            n_kept = fstats.get("kept", len(kept))
            summary["kept"] += n_kept
            summary["filtered"] += len(phrases) - n_kept
            if kept:
                summary["enriched"] += 1
                await write_enrich(rid, kept)
                await write_kept({"doc_id": rid, "status": "ok",
                                  "proposed": phrases, "kept": kept,
                                  "filter_stats": fstats, "ms": ms,
                                  "batch_id": batch_id})
            else:
                await _fail(rid, "all_filtered", batch_id,
                            proposed=phrases, filter_stats=fstats, ms=ms)
        if res.missing:
            if attempt < cfg.max_retries:
                summary["requeued"] += len(res.missing)
                requeue.extend((rid, texts_by_id[rid]) for rid in res.missing)
            else:
                for rid in res.missing:
                    await _fail(rid, "missing_in_batch_response", batch_id,
                                error=res.error, ms=ms)
        await write_batch({
            "batch_id": batch_id, "plan": batch.plan_id,
            "n_reqs": len(batch.ids), "status": "ok" if not res.error else "parse_error",
            "answered": len(res.phrases_by_id), "missing": len(res.missing),
            "prompt_tokens_est": batch.prompt_tokens,
            "resp_tokens_est": len(batch.ids) * cfg.resp_tokens_per_req,
            "closed_by": batch.closed_by, "oversized": batch.oversized,
            "attempt": attempt, "ms": ms})

    pending = list(items)
    attempt = 0
    while pending and attempt <= cfg.max_retries:
        round_batches: list[Batch] = []
        for plan_id, reqs in group_reqs_by_plan(pending).items():
            _, header_tokens = _header(plan_id)
            round_batches.extend(
                make_plan_batches(plan_id, reqs, header_tokens, cfg))
        for i, b in enumerate(round_batches):
            logger.info(
                "batch %d [round %d]: plan=%s reqs=%d prompt≈%d tok "
                "resp≈%d tok closed_by=%s%s",
                batch_seq + i, attempt, b.plan_id,
                len(b.ids), b.prompt_tokens,
                len(b.ids) * cfg.resp_tokens_per_req, b.closed_by,
                " OVERSIZED" if b.oversized else "")
        summary["batches"] += len(round_batches)
        summary["oversized"] += sum(1 for b in round_batches if b.oversized)
        requeue: list[tuple[str, str]] = []
        tasks = []
        for b in round_batches:
            bid = f"b{batch_seq:05d}"
            batch_seq += 1
            tasks.append(_run_one(b, bid, requeue, attempt))
        await asyncio.gather(*tasks)
        pending = requeue
        attempt += 1
    return summary
