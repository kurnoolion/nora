"""Probe a TEI deployment's embedding + reranking wire shapes.

Stdlib-only diagnostic — POSTs a tiny payload to each candidate endpoint and
reports HTTP status, response shape, dimension/score, elapsed ms in a compact
``SPK`` one-line format safe to paste back in chat.

Usage:
    python -m core.src.vectorstore.probe_tei \\
        --embed-base http://your-gateway/embed \\
        --rerank-base http://your-gateway/rerank

The bases passed here are the URL prefixes whose `/embed` or `/rerank` route
forwards to a TEI server. They match the values you would set in
``NORA_RERANKER_BASE_URL`` (for the rerank base) and (eventually)
``NORA_EMBEDDING_BASE_URL``. The script appends the TEI-side route to each
base, so:

    --embed-base http://host/embed   →  POST http://host/embed/v1/embeddings
                                     →  POST http://host/embed/embed
    --rerank-base http://host/rerank →  POST http://host/rerank/rerank

Three probes:
  1. POST {embed-base}/v1/embeddings — OpenAI-compatible embedding (TEI ≥1.5)
  2. POST {embed-base}/embed         — TEI native embedding
  3. POST {rerank-base}/rerank       — TEI Cohere-style rerank

Each probe is independent — failures in one don't block the others. Compact
output is intended to be pasted back so NORA can pick the right embedding
wire shape to wire in (``openai-embedding`` vs ``tei-embedding``) without
guessing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _post_json(url: str, payload: dict, timeout: float = 30.0) -> tuple[int, str, float]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return resp.status, text, time.perf_counter() - t0
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            text = "<unreadable>"
        return exc.code, text, time.perf_counter() - t0
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, repr(exc), time.perf_counter() - t0


def _trim(s: str, n: int = 200) -> str:
    s = s.replace("\n", " ").replace("\r", " ")
    return s if len(s) <= n else s[:n] + "..."


def probe_openai_embedding(embed_base: str, model: str) -> str:
    url = f"{embed_base.rstrip('/')}/v1/embeddings"
    status, text, dt = _post_json(url, {"input": "hello", "model": model})
    if status == 200:
        try:
            data = json.loads(text)
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                first = data["data"][0]
                emb = first.get("embedding") if isinstance(first, dict) else None
                if isinstance(emb, list):
                    return (f"SPK openai-embedding: OK status=200 dim={len(emb)} "
                            f"elapsed={dt*1000:.0f}ms shape=openai-compat")
            return (f"SPK openai-embedding: 200_BUT_UNEXPECTED_SHAPE elapsed={dt*1000:.0f}ms "
                    f"body={_trim(text)}")
        except json.JSONDecodeError:
            return (f"SPK openai-embedding: 200_BUT_NON_JSON elapsed={dt*1000:.0f}ms "
                    f"body={_trim(text)}")
    return f"SPK openai-embedding: FAIL status={status} elapsed={dt*1000:.0f}ms body={_trim(text)}"


def probe_tei_embedding(embed_base: str) -> str:
    url = f"{embed_base.rstrip('/')}/embed"
    status, text, dt = _post_json(url, {"inputs": "hello"})
    if status == 200:
        try:
            data = json.loads(text)
            # TEI native: [[0.1, 0.2, ...]] for batched inputs
            if isinstance(data, list) and data and isinstance(data[0], list):
                return (f"SPK tei-embedding: OK status=200 dim={len(data[0])} "
                        f"elapsed={dt*1000:.0f}ms shape=tei-native(array-of-arrays)")
            if isinstance(data, list) and all(isinstance(x, (int, float)) for x in data):
                return (f"SPK tei-embedding: OK status=200 dim={len(data)} "
                        f"elapsed={dt*1000:.0f}ms shape=tei-native(flat-array)")
            return (f"SPK tei-embedding: 200_BUT_UNEXPECTED_SHAPE elapsed={dt*1000:.0f}ms "
                    f"body={_trim(text)}")
        except json.JSONDecodeError:
            return (f"SPK tei-embedding: 200_BUT_NON_JSON elapsed={dt*1000:.0f}ms "
                    f"body={_trim(text)}")
    return f"SPK tei-embedding: FAIL status={status} elapsed={dt*1000:.0f}ms body={_trim(text)}"


def probe_tei_rerank(rerank_base: str) -> str:
    url = f"{rerank_base.rstrip('/')}/rerank"
    payload = {"query": "what is a network operator", "texts": ["mobile carrier", "banana"]}
    status, text, dt = _post_json(url, payload)
    if status == 200:
        try:
            data = json.loads(text)
            # TEI Cohere: [{"index": 0, "score": 0.9}, ...]  OR  {"results": [...]}
            arr = data if isinstance(data, list) else (
                data.get("results") if isinstance(data, dict) else None
            )
            if isinstance(arr, list) and arr and isinstance(arr[0], dict) \
                    and "index" in arr[0] and "score" in arr[0]:
                top = arr[0]
                shape = "flat-array" if isinstance(data, list) else "results-wrapper"
                return (f"SPK tei-rerank: OK status=200 top_index={top['index']} "
                        f"top_score={top['score']:.4f} count={len(arr)} "
                        f"elapsed={dt*1000:.0f}ms shape={shape}")
            return (f"SPK tei-rerank: 200_BUT_UNEXPECTED_SHAPE elapsed={dt*1000:.0f}ms "
                    f"body={_trim(text)}")
        except json.JSONDecodeError:
            return (f"SPK tei-rerank: 200_BUT_NON_JSON elapsed={dt*1000:.0f}ms "
                    f"body={_trim(text)}")
    return f"SPK tei-rerank: FAIL status={status} elapsed={dt*1000:.0f}ms body={_trim(text)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe a TEI deployment's embedding + reranking endpoints.",
    )
    parser.add_argument("--embed-base",
                        help="Gateway base whose /embed route forwards to a TEI "
                             "embedder. Probes will POST to {embed-base}/v1/embeddings "
                             "and {embed-base}/embed.")
    parser.add_argument("--rerank-base",
                        help="Gateway base whose /rerank route forwards to a TEI "
                             "reranker. Probe will POST to {rerank-base}/rerank.")
    parser.add_argument("--embed-model", default="bge-large-en-v1.5",
                        help="Model name for the OpenAI-compatible embedding probe "
                             "(informational for TEI; some OpenAI-shape servers require it)")
    args = parser.parse_args(argv)

    if not args.embed_base and not args.rerank_base:
        parser.error("pass at least one of --embed-base / --rerank-base")

    if args.embed_base:
        print(f"# Embedding probes against {args.embed_base}")
        print(probe_openai_embedding(args.embed_base, args.embed_model))
        print(probe_tei_embedding(args.embed_base))
    if args.rerank_base:
        print(f"# Rerank probe against {args.rerank_base}")
        print(probe_tei_rerank(args.rerank_base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
