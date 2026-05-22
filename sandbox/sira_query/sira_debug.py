"""SIRA debug CLI — diagnostic probe for the SIRA per-query stack.

Mirrors `sandbox/sira_query/service.py`'s loaders so it runs against the
same on-disk artifacts. No FastAPI; no server. Just a CLI that surfaces
what the service "sees" and how a query flows through expansion + BM25
+ optional rerank.

Subcommands:
    env                                  Print resolved config + path existence
    corpus     [--filter SUBSTR]         Corpus stats; filter by req_id substring
    phrases    [--filter SUBSTR]         Doc-enrichment phrase stats + sample
    req <req_id>  [--query "..."]        Show one req's text + phrases + BM25 score
                                          for the optional query
    query <text> [--top-n N]             Full trace: expansion → BM25 (±exp) →
                [--rerank] [--watch ID]   optional rerank. --watch ID surfaces
                                          one req's score even if it's outside top-N.
    service                              Ping the running FastAPI service

Env vars (same as service.py):
    NORA_SIRA_DB_ROOT, NORA_SIRA_DATASET, NORA_LLM_SHIM_URL,
    NORA_LLM_MODEL, NORA_SIRA_CLONE_ROOT, NORA_SIRA_MAX_DF_RATIO,
    NORA_SIRA_EXPANSION_WEIGHT, NORA_SIRA_RERANK_TOP_N

Run from the same venv as the service:

    source ~/work/nora/sandbox/activate.sh
    export NORA_SIRA_DB_ROOT=$HOME/work/nora/sandbox/adapter/out
    python -m sandbox.sira_query.sira_debug env
    python -m sandbox.sira_query.sira_debug corpus --filter VOWIFI
    python -m sandbox.sira_query.sira_debug phrases --filter VOWIFI
    python -m sandbox.sira_query.sira_debug query "Summarize WiFi Calling requirements" --rerank --top-n 50
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("sira_debug")


# ── Config (parallel to service.py) ────────────────────────────────

_DB_ROOT = os.getenv("NORA_SIRA_DB_ROOT", "")
_DATASET = os.getenv("NORA_SIRA_DATASET", "nora")
_SHIM_URL = os.getenv("NORA_LLM_SHIM_URL", "http://127.0.0.1:8030").rstrip("/")
_SHIM_MODEL = os.getenv("NORA_LLM_MODEL", "")
_SERVICE_URL = os.getenv("NORA_SIRA_QUERY_URL", "http://127.0.0.1:8040").rstrip("/")

_SIRA_CLONE_ROOT = Path(os.getenv(
    "NORA_SIRA_CLONE_ROOT",
    str(Path(__file__).resolve().parents[1] / "sira"),
))
_QUERY_PROMPT_PATH = os.getenv(
    "NORA_QUERY_PROMPT",
    "scripts/configs/enrich/prompts/query_requirement_v01.txt",
)
_RERANK_PROMPT_PATH = os.getenv(
    "NORA_RERANK_PROMPT",
    "scripts/configs/rerank/prompts/relevance_requirement_v01.txt",
)

_MAX_DF_RATIO = float(os.getenv("NORA_SIRA_MAX_DF_RATIO", "0.05"))
_EXPANSION_WEIGHT = float(os.getenv("NORA_SIRA_EXPANSION_WEIGHT", "0.5"))
_RERANK_TOP_N_DEFAULT = int(os.getenv("NORA_SIRA_RERANK_TOP_N", "20"))


# ── Resolved paths ─────────────────────────────────────────────────

def _paths() -> dict[str, Path]:
    base = Path(_DB_ROOT) / _DATASET if _DB_ROOT else Path("")
    return {
        "base": base,
        "corpus": base / "raw" / "corpus.jsonl",
        "queries": base / "raw" / "queries.jsonl",
        "qrels": base / "raw" / "qrels-test.jsonl",
        "index": base / "index" / "best",
        "doc_phrases": base / "enrichments" / "doc" / "best.jsonl",
        "query_phrases": base / "enrichments" / "query" / "best.jsonl",
        "query_prompt": _SIRA_CLONE_ROOT / _QUERY_PROMPT_PATH,
        "rerank_prompt": _SIRA_CLONE_ROOT / _RERANK_PROMPT_PATH,
    }


# ── Loaders ────────────────────────────────────────────────────────

def _load_corpus() -> tuple[list[str], dict[str, dict[str, str]]]:
    p = _paths()["corpus"]
    if not p.exists():
        raise FileNotFoundError(f"corpus.jsonl not found at {p}")
    ids: list[str] = []
    by_id: dict[str, dict[str, str]] = {}
    with open(p, encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            rid = obj["_id"]
            ids.append(rid)
            by_id[rid] = {
                "title": obj.get("title", ""),
                "text": obj.get("text", ""),
            }
    return ids, by_id


def _load_doc_phrases() -> dict[str, list[str]]:
    p = _paths()["doc_phrases"]
    if not p.exists():
        return {}
    out: dict[str, list[str]] = {}
    with open(p, encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            # SIRA writes either "doc_id" or "_id"; tolerate both
            rid = obj.get("doc_id") or obj.get("_id")
            if rid:
                out[rid] = list(obj.get("phrases") or [])
    return out


def _load_bm25():
    from bm25x import BM25
    idx = _paths()["index"]
    if not idx.exists():
        raise FileNotFoundError(f"BM25 index not found at {idx}")
    return BM25.load(str(idx))


def _load_prompt(name: str) -> str:
    p = _paths()[name]
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ── LLM call helpers (mirror service.py) ──────────────────────────

async def _llm_call(client, prompt: str, *, max_tokens: int, temperature: float) -> str:
    payload = {
        "model": _SHIM_MODEL or "sira-shim",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    resp = await client.post(f"{_SHIM_URL}/v1/chat/completions", json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"shim returned {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"] or ""


def _parse_phrases(raw: str) -> list[str]:
    end_char = {"{": "}", "[": "]"}
    for start in ("{", "["):
        idx = raw.find(start)
        if idx == -1:
            continue
        end = raw.rfind(end_char[start])
        if end <= idx:
            continue
        try:
            parsed = json.loads(raw[idx : end + 1])
            if isinstance(parsed, dict):
                parsed = parsed.get("keywords", [])
            if isinstance(parsed, list):
                return [p for p in parsed if isinstance(p, str) and p.strip()]
        except json.JSONDecodeError:
            continue
    return []


def _parse_score(raw: str) -> int:
    idx = raw.find("{")
    end = raw.rfind("}")
    if idx == -1 or end <= idx:
        return 0
    try:
        obj = json.loads(raw[idx : end + 1])
        return int(obj.get("score", 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0


# ── Subcommands ────────────────────────────────────────────────────

def cmd_env(args) -> int:
    paths = _paths()
    print("Resolved config:")
    print(f"  NORA_SIRA_DB_ROOT          = {_DB_ROOT or '(unset)'}")
    print(f"  NORA_SIRA_DATASET          = {_DATASET}")
    print(f"  NORA_LLM_SHIM_URL          = {_SHIM_URL}")
    print(f"  NORA_LLM_MODEL             = {_SHIM_MODEL or '(unset, falls to sira-shim)'}")
    print(f"  NORA_SIRA_QUERY_URL        = {_SERVICE_URL}")
    print(f"  NORA_SIRA_CLONE_ROOT       = {_SIRA_CLONE_ROOT}")
    print(f"  NORA_SIRA_MAX_DF_RATIO     = {_MAX_DF_RATIO}")
    print(f"  NORA_SIRA_EXPANSION_WEIGHT = {_EXPANSION_WEIGHT}")
    print(f"  NORA_SIRA_RERANK_TOP_N     = {_RERANK_TOP_N_DEFAULT}")
    print()
    print("Path existence:")
    for label, p in paths.items():
        marker = "OK " if p.exists() else "-- "
        size = ""
        if p.exists() and p.is_file():
            size = f"  ({p.stat().st_size:,} bytes)"
        print(f"  [{marker}] {label:14}  {p}{size}")
    return 0


def cmd_corpus(args) -> int:
    ids, by_id = _load_corpus()
    print(f"Corpus: {len(ids):,} docs total")

    if args.filter:
        sub = args.filter.upper()
        match_ids = [r for r in ids if sub in r.upper()]
        print(f"Filter '{args.filter}' matches {len(match_ids):,} docs")
        for rid in match_ids[: args.limit]:
            row = by_id[rid]
            title = row["title"][:80]
            text_preview = (row["text"][:120]).replace("\n", " ")
            print(f"  {rid}")
            print(f"    title: {title}")
            print(f"    text:  {text_preview}{'...' if len(row['text']) > 120 else ''}")
        if len(match_ids) > args.limit:
            print(f"  ... ({len(match_ids) - args.limit} more)")
    else:
        # Show plan-level histogram by req_id prefix
        from collections import Counter
        prefixes = Counter()
        for rid in ids:
            # VZ_REQ_<PLAN>_<NUM> → take <PLAN>
            parts = rid.split("_")
            plan = parts[2] if len(parts) >= 3 else "?"
            prefixes[plan] += 1
        print(f"Top {args.limit} plans by req count:")
        for plan, n in prefixes.most_common(args.limit):
            print(f"  {plan:32}  {n:,}")
    return 0


def cmd_phrases(args) -> int:
    phrases = _load_doc_phrases()
    p = _paths()["doc_phrases"]
    if not phrases:
        print(f"No doc-enrichment phrases loaded ({p} missing or empty).")
        print("The per-query SIRA service will run vanilla BM25 (raw corpus) without "
              "doc-side enrichment.")
        return 1
    print(f"Doc-enrichment phrases: {len(phrases):,} docs with phrases (from {p})")
    counts = sorted((len(v) for v in phrases.values()), reverse=True)
    if counts:
        avg = sum(counts) / len(counts)
        print(f"  avg phrases/doc: {avg:.1f}  ·  max: {counts[0]}  ·  min: {counts[-1]}")

    if args.filter:
        sub = args.filter.upper()
        match = {rid: ph for rid, ph in phrases.items() if sub in rid.upper()}
        print(f"\nFilter '{args.filter}' matches {len(match):,} doc(s)")
        for rid in list(match.keys())[: args.limit]:
            ph = match[rid]
            print(f"  {rid}  ({len(ph)} phrases)")
            for term in ph[:20]:
                print(f"    - {term}")
            if len(ph) > 20:
                print(f"    ... ({len(ph) - 20} more)")
    else:
        # Top discriminative terms by global frequency (just sanity)
        from collections import Counter
        term_freq = Counter()
        for ph in phrases.values():
            for t in ph:
                term_freq[t.lower()] += 1
        print(f"\nTop {args.limit} most-common phrases across corpus:")
        for t, n in term_freq.most_common(args.limit):
            print(f"  {n:5}  {t}")
    return 0


def cmd_req(args) -> int:
    ids, by_id = _load_corpus()
    rid = args.req_id
    if rid not in by_id:
        print(f"req_id '{rid}' not found in corpus.")
        # Suggest near-matches
        ups = rid.upper()
        near = [r for r in ids if ups in r.upper()][:5]
        if near:
            print("Near-matches:")
            for n in near:
                print(f"  {n}")
        return 1
    row = by_id[rid]
    print(f"req_id: {rid}")
    print(f"title:  {row['title']}")
    print(f"text:   ({len(row['text'])} chars)")
    print(row["text"][:1000])
    if len(row["text"]) > 1000:
        print(f"... ({len(row['text']) - 1000} more chars)")

    phrases = _load_doc_phrases()
    if rid in phrases:
        print(f"\nDoc-enrichment phrases ({len(phrases[rid])}):")
        for t in phrases[rid]:
            print(f"  - {t}")
    else:
        print("\nNo doc-enrichment phrases for this req.")

    if args.query:
        bm25 = _load_bm25()
        results = bm25.search_with_expansion(
            [args.query], [""], k=len(ids), weight=_EXPANSION_WEIGHT,
        )
        hits = list(results[0])
        # Find this req's rank/score
        for rank, (doc_idx, score) in enumerate(hits):
            if ids[doc_idx] == rid:
                print(f"\nBM25 (no expansion) for query '{args.query}':")
                print(f"  rank: {rank + 1}  ·  score: {score:.4f}")
                break
        else:
            print(f"\nBM25 (no expansion) for query '{args.query}': not in top-{len(ids)}")
    return 0


async def _run_query_trace(query: str, top_n: int, do_rerank: bool, watch_rid: str | None) -> int:
    import httpx
    ids, by_id = _load_corpus()
    bm25 = _load_bm25()
    qp_template = _load_prompt("query_prompt")
    rp_template = _load_prompt("rerank_prompt")
    max_df_absolute = max(1, int(len(ids) * _MAX_DF_RATIO))

    if not qp_template:
        print("WARNING: query enrichment prompt missing — expansion stage will skip.")

    print(f"Query: {query!r}")
    print(f"Corpus size: {len(ids):,}  ·  max_df_absolute: {max_df_absolute}  "
          f"·  expansion_weight: {_EXPANSION_WEIGHT}  ·  top_n: {top_n}")
    print()

    async with httpx.AsyncClient(timeout=300.0) as client:
        # Stage 1: query expansion
        kept_phrases: list[str] = []
        rejected_phrases: list[str] = []
        expansion_terms = ""
        if qp_template:
            print("[1/3] Query enrichment via LLM ...")
            t0 = time.time()
            try:
                prompt = qp_template.format(doc_text=query, max_n=4)
                raw = await _llm_call(client, prompt, max_tokens=512, temperature=0.4)
                proposed = _parse_phrases(raw)
                print(f"      proposed ({len(proposed)}): {proposed}")
                kept_phrases, rejected_phrases = bm25.filter_query_expansion(
                    query, proposed, max_df_absolute,
                )
                print(f"      kept     ({len(kept_phrases)}): {kept_phrases}")
                print(f"      rejected ({len(rejected_phrases)}): {rejected_phrases}")
                kept_stems: list[str] = []
                for p in kept_phrases:
                    kept_stems.extend(bm25.tokenize(p))
                expansion_terms = " ".join(kept_stems) if kept_stems else ""
                print(f"      expansion tokens: {expansion_terms}")
            except Exception as exc:
                print(f"      FAILED: {exc!r}")
            print(f"      ({time.time() - t0:.2f}s)")
            print()

        # Stage 2a: BM25 WITHOUT expansion (vanilla baseline)
        print("[2a/3] BM25 search (no expansion) — vanilla baseline")
        t0 = time.time()
        vanilla = list(bm25.search_with_expansion(
            [query], [""], k=top_n, weight=0.0,
        )[0])
        print(f"      top-{min(10, len(vanilla))}:")
        for rank, (doc_idx, score) in enumerate(vanilla[:10]):
            rid = ids[doc_idx]
            mark = " ←watch" if watch_rid == rid else ""
            print(f"        #{rank+1:3} {rid}  bm25={score:.3f}{mark}")
        if watch_rid:
            for rank, (doc_idx, score) in enumerate(vanilla):
                if ids[doc_idx] == watch_rid:
                    if rank >= 10:
                        print(f"        #{rank+1:3} {watch_rid}  bm25={score:.3f}  ←watch")
                    break
            else:
                print(f"        watch={watch_rid}: not in top-{top_n}")
        print(f"      ({time.time() - t0:.2f}s)")
        print()

        # Stage 2b: BM25 WITH expansion
        print(f"[2b/3] BM25 search WITH expansion (weight={_EXPANSION_WEIGHT})")
        t0 = time.time()
        expanded = list(bm25.search_with_expansion(
            [query], [expansion_terms], k=top_n, weight=_EXPANSION_WEIGHT,
        )[0])
        # Show top-10 and any rank shifts vs vanilla
        vanilla_ranks = {ids[di]: r for r, (di, _) in enumerate(vanilla)}
        print(f"      top-{min(10, len(expanded))}:")
        for rank, (doc_idx, score) in enumerate(expanded[:10]):
            rid = ids[doc_idx]
            vr = vanilla_ranks.get(rid)
            shift = f"  (was #{vr+1})" if vr is not None and vr != rank else (
                "  (new)" if vr is None else ""
            )
            mark = " ←watch" if watch_rid == rid else ""
            print(f"        #{rank+1:3} {rid}  bm25={score:.3f}{shift}{mark}")
        if watch_rid:
            for rank, (doc_idx, score) in enumerate(expanded):
                if ids[doc_idx] == watch_rid:
                    if rank >= 10:
                        print(f"        #{rank+1:3} {watch_rid}  bm25={score:.3f}  ←watch")
                    break
            else:
                print(f"        watch={watch_rid}: not in top-{top_n}")
        print(f"      ({time.time() - t0:.2f}s)")
        print()

        # Stage 3: optional LLM rerank
        if do_rerank:
            if not rp_template:
                print("[3/3] Rerank prompt missing — skipping rerank.")
                return 0
            print(f"[3/3] LLM rerank of top-{len(expanded)} ...")
            t0 = time.time()
            scores: list[tuple[int, float, int]] = []  # (doc_idx, bm25, rerank)
            for i, (doc_idx, bm25_score) in enumerate(expanded):
                row = by_id[ids[doc_idx]]
                doc_text = f"{row['title']}\n{row['text']}"
                prompt = rp_template.format(query=query, doc=doc_text[:6000])
                try:
                    raw = await _llm_call(client, prompt, max_tokens=128, temperature=0.0)
                    rscore = _parse_score(raw)
                except Exception as exc:
                    print(f"      rerank failed for {ids[doc_idx]}: {exc!r}")
                    rscore = 0
                scores.append((doc_idx, bm25_score, rscore))
                if (i + 1) % 5 == 0:
                    print(f"      ({i+1}/{len(expanded)} reranked, {time.time() - t0:.1f}s)")
            scores.sort(key=lambda x: x[2], reverse=True)
            max_rerank = scores[0][2] if scores else 0
            print(f"      rerank done in {time.time() - t0:.1f}s; max rerank score = {max_rerank}")
            print(f"      top-{min(10, len(scores))} by rerank:")
            for rank, (doc_idx, bm, rs) in enumerate(scores[:10]):
                rid = ids[doc_idx]
                mark = " ←watch" if watch_rid == rid else ""
                print(f"        #{rank+1:3} {rid}  rerank={rs:>3}  bm25={bm:.3f}{mark}")
            if watch_rid:
                for rank, (doc_idx, bm, rs) in enumerate(scores):
                    if ids[doc_idx] == watch_rid:
                        if rank >= 10:
                            print(f"        #{rank+1:3} {watch_rid}  rerank={rs}  bm25={bm:.3f}  ←watch")
                        break
                else:
                    print(f"        watch={watch_rid}: not in reranked candidates")
    return 0


def cmd_query(args) -> int:
    return asyncio.run(_run_query_trace(
        args.query,
        top_n=args.top_n,
        do_rerank=args.rerank,
        watch_rid=args.watch,
    ))


def cmd_service(args) -> int:
    import httpx
    try:
        with httpx.Client(timeout=5.0) as client:
            for ep in ("/healthz", "/v1/models"):
                r = client.get(f"{_SERVICE_URL}{ep}")
                print(f"GET {_SERVICE_URL}{ep} -> {r.status_code}")
                try:
                    print(json.dumps(r.json(), indent=2))
                except Exception:
                    print(r.text[:500])
                print()
    except Exception as exc:
        print(f"Service unreachable at {_SERVICE_URL}: {exc!r}")
        return 1
    return 0


# ── argparse ────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(prog="sira_debug", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("env", help="Print resolved config + path existence")

    pc = sub.add_parser("corpus", help="Corpus stats (or filter by req_id substring)")
    pc.add_argument("--filter", help="Substring match against req_ids (case-insensitive)")
    pc.add_argument("--limit", type=int, default=20)

    pp = sub.add_parser("phrases", help="Doc-enrichment phrase stats + sample")
    pp.add_argument("--filter", help="Substring match against req_ids")
    pp.add_argument("--limit", type=int, default=20)

    pr = sub.add_parser("req", help="Show one req's text + phrases + BM25 score for a query")
    pr.add_argument("req_id")
    pr.add_argument("--query", default="")

    pq = sub.add_parser("query", help="Full per-query trace")
    pq.add_argument("query")
    pq.add_argument("--top-n", type=int, default=_RERANK_TOP_N_DEFAULT)
    pq.add_argument("--rerank", action="store_true", help="Run LLM rerank too (SLOW)")
    pq.add_argument("--watch", help="Track a specific req_id even outside top-N")

    sub.add_parser("service", help="Ping the running FastAPI service")

    args = p.parse_args(argv)
    fn = {
        "env": cmd_env,
        "corpus": cmd_corpus,
        "phrases": cmd_phrases,
        "req": cmd_req,
        "query": cmd_query,
        "service": cmd_service,
    }[args.cmd]
    try:
        return fn(args)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
