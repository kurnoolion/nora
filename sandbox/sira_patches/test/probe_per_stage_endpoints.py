"""Probe SIRA per-stage LLM endpoints to confirm wire shapes + reachability.

Stdlib-only diagnostic — POSTs a tiny chat-completion to each configured
endpoint and reports HTTP status, model echoed back, elapsed ms in a
compact ``SPK`` one-line format safe to paste in chat.

Usage::

    python -m sandbox.sira_patches.test.probe_per_stage_endpoints \\
        --enrich-url   "$NORA_SIRA_ENRICH_LLM_URL" \\
        --enrich-model "$NORA_SIRA_ENRICH_LLM_MODEL" \\
        --rerank-url   "$NORA_SIRA_RERANK_LLM_URL" \\
        --rerank-model "$NORA_SIRA_RERANK_LLM_MODEL"

Or rely on the env vars directly (no args needed)::

    python -m sandbox.sira_patches.test.probe_per_stage_endpoints

Each base URL is the ``/v1`` prefix; the script appends
``/chat/completions``. Matches the same path SIRA's per-stage-routing
patch builds — so a ``SPK ... OK`` here means SIRA will also succeed
at runtime with the same env vars.

Three sample backends to verify your routing setup:

  - Existing OpenAI-compatible LLM gateway — single endpoint, both
    stages point at it.
  - Ollama (default http://localhost:11434/v1) — set ``--rerank-url``
    here for the fast-rerank pattern.
  - vLLM (your vLLM port, OpenAI-compatible by default) — works as
    either stage's endpoint.

Independent probes — failures in one don't block the others.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _post_json(url: str, payload: dict, timeout: float = 60.0) -> tuple[int, str, float]:
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
    return s if len(s) <= n else s[: n - 3] + "..."


def probe_stage(stage_name: str, base_url: str, model: str) -> str:
    """One probe — POST a minimal chat-completion to {base_url}/chat/completions.

    Returns a single compact `SPK <stage>: ...` line summarizing the
    outcome. No URLs / model names are echoed in the output beyond what
    the caller passed in.
    """
    if not base_url:
        return f"SPK {stage_name}: SKIP url-unset"
    if not model:
        return f"SPK {stage_name}: SKIP model-unset"

    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: OK."}],
        "max_tokens": 16,
        "temperature": 0,
    }
    status, text, dt = _post_json(url, payload)

    if status == 200:
        try:
            data = json.loads(text)
            content = ""
            if isinstance(data, dict):
                choices = data.get("choices") or []
                if choices and isinstance(choices[0], dict):
                    msg = choices[0].get("message") or {}
                    content = (msg.get("content") or "").strip()
            echoed_model = ""
            if isinstance(data, dict):
                echoed_model = (data.get("model") or "").strip()
            return (
                f"SPK {stage_name}: OK status=200 "
                f"elapsed={dt * 1000:.0f}ms "
                f"model_echoed={'yes' if echoed_model else 'no'} "
                f"content_len={len(content)}"
            )
        except json.JSONDecodeError:
            return (
                f"SPK {stage_name}: 200_BUT_NON_JSON elapsed={dt * 1000:.0f}ms "
                f"body={_trim(text)}"
            )
    return (
        f"SPK {stage_name}: FAIL status={status} elapsed={dt * 1000:.0f}ms "
        f"body={_trim(text)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Probe SIRA per-stage LLM endpoints — sanity check before "
            "running the pipeline. Reports one SPK line per stage."
        ),
    )
    parser.add_argument(
        "--enrich-url",
        default=os.getenv("NORA_SIRA_ENRICH_LLM_URL", ""),
        help="OpenAI /v1 base for enrichment (defaults to NORA_SIRA_ENRICH_LLM_URL env var).",
    )
    parser.add_argument(
        "--enrich-model",
        default=os.getenv("NORA_SIRA_ENRICH_LLM_MODEL", ""),
        help="Model name for enrichment payload (defaults to NORA_SIRA_ENRICH_LLM_MODEL env var).",
    )
    parser.add_argument(
        "--rerank-url",
        default=os.getenv("NORA_SIRA_RERANK_LLM_URL", ""),
        help="OpenAI /v1 base for reranking (defaults to NORA_SIRA_RERANK_LLM_URL env var).",
    )
    parser.add_argument(
        "--rerank-model",
        default=os.getenv("NORA_SIRA_RERANK_LLM_MODEL", ""),
        help="Model name for rerank payload (defaults to NORA_SIRA_RERANK_LLM_MODEL env var).",
    )
    args = parser.parse_args(argv)

    if not (args.enrich_url or args.rerank_url):
        parser.error(
            "no endpoints to probe — set NORA_SIRA_ENRICH_LLM_URL "
            "and/or NORA_SIRA_RERANK_LLM_URL, or pass --enrich-url / "
            "--rerank-url."
        )

    print(probe_stage("enrich", args.enrich_url, args.enrich_model))
    print(probe_stage("rerank", args.rerank_url, args.rerank_model))
    return 0


if __name__ == "__main__":
    sys.exit(main())
