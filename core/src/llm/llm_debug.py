"""LLM debug — verify the active LLM provider and probe arbitrary endpoints.

Modes (mutually exclusive):

  python -m core.src.llm.llm_debug --probe <url> [--api-key KEY]
      Probe an HTTP endpoint to discover which API kind(s) it supports.
      Tests the four interesting routes:
        GET  <url>/api/tags             — native Ollama (model list)
        GET  <url>/v1/models            — OpenAI-compatible (model list)
        POST <url>/api/chat             — native Ollama chat (no real call,
                                          just shape of error / 405 / 200)
        POST <url>/v1/chat/completions  — OpenAI-compatible chat (same)
      For each route, prints HTTP status, response shape, and (when
      available) the model list. Use this on a new endpoint before
      configuring NORA_LLM_PROVIDER to know whether to pick `ollama`
      or `openai-compatible`.

  python -m core.src.llm.llm_debug --check
      Resolve the active LLM provider via the same chain the web UI
      and pipeline use (CLI > NORA_LLM_* env > config/llm.json >
      env-config > default), construct it, and send a one-line
      completion ("ping"). Reports which knobs were honored and
      where the model name / base URL came from. Useful when web
      UI answers don't match what you think you configured.

  python -m core.src.llm.llm_debug --complete --text "..." [--system "..."]
      Resolve the active LLM provider and run a single completion on
      ad-hoc text. Prints the response, latency, and (if the provider
      tracks it) per-call stats.

When pasting output back into chat, --probe omits `--api-key` from the
echoed config. --check and --complete print the model name only,
not request bodies.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Probe helpers ────────────────────────────────────────────────


def _http_get(url: str, timeout: int = 15) -> tuple[int, str, dict | None]:
    """Return (status_code, raw_body_excerpt, parsed_json_or_None)."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode(errors="replace")
            try:
                return resp.status, body[:300], json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body[:300], None
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace") if e.fp else ""
        return e.code, body[:300], None
    except urllib.error.URLError as e:
        return 0, f"URLError: {e.reason}", None
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", None


def _http_post(url: str, body: dict, timeout: int = 15,
               headers: dict | None = None) -> tuple[int, str, dict | None]:
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode(errors="replace")
            try:
                return resp.status, text[:300], json.loads(text)
            except json.JSONDecodeError:
                return resp.status, text[:300], None
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace") if e.fp else ""
        return e.code, text[:300], None
    except urllib.error.URLError as e:
        return 0, f"URLError: {e.reason}", None
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}", None


def _summarize_models(parsed: dict | None) -> str:
    """Best-effort extraction of a model list from a /models or /tags response."""
    if not parsed:
        return ""
    # Ollama /api/tags shape: {"models": [{"name": "..."}, ...]}
    # OpenAI /v1/models shape: {"data": [{"id": "..."}, ...]}
    items = parsed.get("models") or parsed.get("data") or []
    names = []
    for it in items:
        if isinstance(it, dict):
            n = it.get("name") or it.get("id") or it.get("model")
            if n:
                names.append(n)
    if not names:
        return ""
    if len(names) > 8:
        return ", ".join(names[:8]) + f", … (+{len(names) - 8} more)"
    return ", ".join(names)


def cmd_probe(args: argparse.Namespace) -> int:
    base = args.probe.rstrip("/")
    headers = {}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    print(f"Probing endpoint: {base}")
    if args.api_key:
        print("Authorization: Bearer <redacted>")
    print("=" * 70)

    findings = {"ollama_native": False, "openai_compat": False}

    # --- 1. Native Ollama: GET /api/tags ---
    print("\n[1] GET /api/tags  (native Ollama model list)")
    status, body, parsed = _http_get(f"{base}/api/tags")
    print(f"    status: {status}")
    if status == 200:
        models = _summarize_models(parsed)
        print(f"    OK — native Ollama API confirmed")
        if models:
            print(f"    models: {models}")
        findings["ollama_native"] = True
    elif status == 0:
        print(f"    NETWORK: {body}")
    elif status == 404:
        print("    404 — not a native-Ollama endpoint")
    else:
        print(f"    body: {body}")

    # --- 2. OpenAI-compat: GET /v1/models ---
    print("\n[2] GET /v1/models  (OpenAI-compatible model list)")
    status, body, parsed = _http_get(f"{base}/v1/models", timeout=15)
    if status == 401 and headers:
        # try with auth
        req = urllib.request.Request(f"{base}/v1/models", method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                t = resp.read().decode(errors="replace")
                status = resp.status
                try:
                    parsed = json.loads(t)
                except json.JSONDecodeError:
                    parsed = None
                body = t[:300]
        except Exception as e:
            status, body, parsed = 0, str(e), None
    elif headers and status in (0, 401, 403):
        # First try unauth failed; retry with auth header
        req = urllib.request.Request(f"{base}/v1/models", method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                t = resp.read().decode(errors="replace")
                status = resp.status
                try:
                    parsed = json.loads(t)
                except json.JSONDecodeError:
                    parsed = None
                body = t[:300]
        except urllib.error.HTTPError as e:
            t = e.read().decode(errors="replace") if e.fp else ""
            status, body, parsed = e.code, t[:300], None
        except Exception as e:
            status, body, parsed = 0, str(e), None

    print(f"    status: {status}")
    if status == 200:
        models = _summarize_models(parsed)
        print("    OK — OpenAI-compatible API confirmed")
        if models:
            print(f"    models: {models}")
        findings["openai_compat"] = True
    elif status == 0:
        print(f"    NETWORK: {body}")
    elif status == 401:
        print("    401 — endpoint exists but rejected (provide --api-key to retry)")
    elif status == 404:
        print("    404 — not OpenAI-compatible")
    else:
        print(f"    body: {body}")

    # --- 3. Native Ollama chat: POST /api/chat (no real call, look for shape) ---
    print("\n[3] POST /api/chat  (native Ollama chat shape)")
    status, body, parsed = _http_post(
        f"{base}/api/chat",
        {"model": "<probe>", "messages": [{"role": "user", "content": "ping"}],
         "stream": False},
        timeout=10,
    )
    print(f"    status: {status}")
    if status == 200:
        print("    200 — chat endpoint exists (model '<probe>' likely missing)")
    elif status == 404:
        print("    404 — chat endpoint missing")
    elif status == 400:
        # Ollama emits 400 for missing model — confirms shape understood
        print(f"    400 — endpoint understands the request shape")
        print(f"    body excerpt: {body[:120]}")
    elif status == 0:
        print(f"    NETWORK: {body}")
    else:
        print(f"    body excerpt: {body[:120]}")

    # --- 4. OpenAI-compat chat: POST /v1/chat/completions ---
    print("\n[4] POST /v1/chat/completions  (OpenAI-compat chat shape)")
    status, body, parsed = _http_post(
        f"{base}/v1/chat/completions",
        {"model": "<probe>", "messages": [{"role": "user", "content": "ping"}]},
        timeout=10,
        headers=headers if args.api_key else None,
    )
    print(f"    status: {status}")
    if status in (200, 400, 404):
        # 200 ridiculously unlikely for fake model; 400 confirms shape
        if status == 400:
            print("    400 — endpoint understands the OpenAI chat shape")
        elif status == 404:
            print("    404 — chat endpoint missing")
        else:
            print("    200 — OpenAI-compat chat works")
        if body:
            print(f"    body excerpt: {body[:120]}")
    elif status == 401:
        print("    401 — auth required (provide --api-key)")
    elif status == 0:
        print(f"    NETWORK: {body}")
    else:
        print(f"    body excerpt: {body[:120]}")

    # --- Summary + recommendation ---
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Native Ollama API:        {'YES' if findings['ollama_native'] else 'no'}")
    print(f"  OpenAI-compatible API:    {'YES' if findings['openai_compat'] else 'no'}")
    print()

    if findings["openai_compat"]:
        print("RECOMMENDED config (zero code change required):")
        print("  export NORA_LLM_PROVIDER=openai-compatible")
        print(f"  export NORA_LLM_BASE_URL={base}/v1")
        if args.api_key:
            print(f"  export NORA_LLM_API_KEY=<your-key>")
        else:
            print("  export NORA_LLM_API_KEY=ollama  # any non-empty string for native Ollama proxies")
        print("  export NORA_LLM_MODEL=<model-name-from-listing-above>")
    elif findings["ollama_native"]:
        print("ENDPOINT is native-Ollama-only (no /v1 OpenAI surface).")
        print("Use NORA_LLM_PROVIDER=ollama with this endpoint, but note the codebase")
        print("does not yet thread custom Ollama base_url through the pipeline runner.")
        print("Workarounds: (a) reverse-proxy a /v1 surface in front; (b) wire")
        print("ollama_url through PipelineContext (small code change — ask claude).")
    else:
        print("Neither API was confirmed reachable.")
        print("Check the URL, network, and any proxy / VPN; confirm the server is up.")

    return 0 if (findings["ollama_native"] or findings["openai_compat"]) else 1


# ── Resolved-provider helpers (--check, --complete) ──────────────


def _resolve_active_provider():
    """Construct the LLM provider via the unified D-044 chain — same
    path the web UI and pipeline runner use. Returns (provider, info).
    """
    from core.src.env.config import (
        resolve_llm_provider, resolve_llm_model, resolve_llm_timeout,
    )
    from core.src.pipeline.runner import PipelineContext

    provider_name = resolve_llm_provider()
    model = resolve_llm_model()
    timeout = resolve_llm_timeout()
    ctx = PipelineContext(
        documents_dir=Path("."),
        corrections_dir=None,
        eval_dir=None,
        verbose=False,
        model_provider=provider_name,
        model_name=model,
        model_timeout=timeout,
    )
    info = {
        "provider": provider_name,
        "model": model,
        "timeout": timeout,
    }
    provider = ctx.create_llm_provider(require_real=False)
    return provider, info


def cmd_check(args: argparse.Namespace) -> int:
    provider, info = _resolve_active_provider()
    print(f"LLM check — resolved provider={info['provider']!r} model={info['model']!r} "
          f"timeout={info['timeout']}s")
    print(f"Provider class: {type(provider).__name__}")
    is_mock = getattr(provider, "_is_mock", False)
    print(f"is_mock: {is_mock}")
    if is_mock:
        print("Resolved provider is a mock — real provider construction failed.")
        print("Check the warnings logged above; rerun with --verbose for detail.")
        return 1
    print("Sending one-line probe completion 'ping'…")
    t0 = time.time()
    try:
        out = provider.complete(
            prompt="Reply with exactly one word: pong",
            system="", temperature=0.0, max_tokens=8,
        )
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        return 2
    elapsed = time.time() - t0
    print(f"  completed in {elapsed:.2f}s")
    print(f"  response: {out!r}")
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    provider, info = _resolve_active_provider()
    print(f"Active provider={info['provider']} model={info['model']} timeout={info['timeout']}s")
    is_mock = getattr(provider, "_is_mock", False)
    if is_mock:
        print("Resolved to mock provider — refusing to run --complete (would be misleading)")
        return 1
    print(f"Sending prompt ({len(args.text)} chars)…")
    t0 = time.time()
    try:
        out = provider.complete(
            prompt=args.text,
            system=args.system or "",
            temperature=0.0,
            max_tokens=args.max_tokens,
        )
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        return 2
    elapsed = time.time() - t0
    print(f"completed in {elapsed:.2f}s")
    print("=" * 70)
    print(out)
    print("=" * 70)
    stats = getattr(provider, "last_call_stats", None)
    if stats is not None:
        print(f"last_call_stats: {stats}")
    return 0


# ── CLI ──────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LLM debug — probe endpoints and verify the active provider.",
    )
    parser.add_argument(
        "--probe", metavar="URL",
        help="Probe an HTTP endpoint to discover whether it speaks "
             "native Ollama (/api/tags), OpenAI-compatible (/v1/models), "
             "or both. Recommends config to use.",
    )
    parser.add_argument(
        "--api-key", default=None,
        help="Bearer token to send when probing /v1/models / /v1/chat/completions. "
             "Optional — only required if the endpoint enforces auth on probes.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Resolve the active LLM provider via the unified chain and "
             "send a one-line probe completion. Reports which knobs were "
             "honored.",
    )
    parser.add_argument(
        "--complete", action="store_true",
        help="Resolve the active LLM provider and run a single completion "
             "on --text. Requires --text.",
    )
    parser.add_argument(
        "--text", default=None,
        help="Prompt text for --complete.",
    )
    parser.add_argument(
        "--system", default=None,
        help="Optional system prompt for --complete.",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=512,
        help="Max tokens for --complete (default: 512).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose logging from underlying providers.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    modes = [args.probe, args.check, args.complete]
    if sum(1 for m in modes if m) != 1:
        parser.print_help()
        print("\nPick exactly one mode: --probe URL | --check | --complete --text \"...\"")
        sys.exit(1)

    if args.probe:
        sys.exit(cmd_probe(args))
    if args.check:
        sys.exit(cmd_check(args))
    if args.complete:
        if not args.text:
            parser.error("--complete requires --text")
        sys.exit(cmd_complete(args))


if __name__ == "__main__":
    main()
