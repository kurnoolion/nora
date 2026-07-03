#!/usr/bin/env python3
"""PoC: convert requirement-document figures to retrievable text via a
vision-capable, OpenAI-compatible LLM endpoint (strand: image-ingestion).

Single classify+convert call per image. The model classifies the figure and
converts it to the format that best preserves its content for retrieval:

  flow_diagram -> Mermaid (flowchart, or sequenceDiagram for message-sequence
                  charts / signaling flows)
  table        -> GitHub-flavored markdown table
  ux_flow      -> numbered screen-by-screen prose walkthrough (format TBD)
  other        -> concise caption

Usage:
    export VISION_BASE_URL=http://127.0.0.1:8000/v1   # up to /v1 (no trailing path)
    export VISION_MODEL=<model-name>
    export VISION_API_KEY=<key>                       # optional; default "none"

    python analyze_image.py fig1.png fig2.jpg --out ./out
    python analyze_image.py ./crops_dir --out ./out   # every image in a dir

Outputs per image (under --out):
    <stem>.analysis.json   # full result: kind, title, caption, content, raw
    <stem>.md              # eyeball file: title + caption + fenced content

No NORA imports — standalone by design; the productized version lands behind a
VisionProvider protocol in core/src/extraction later. Do not put proprietary
endpoint URLs/model names in this file — env vars only.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import time
from pathlib import Path

import requests

BASE_URL = os.getenv("VISION_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")
API_KEY = os.getenv("VISION_API_KEY", "none")
MODEL = os.getenv("VISION_MODEL", "")
TIMEOUT_S = int(os.getenv("VISION_TIMEOUT_S", "300"))
MAX_TOKENS = int(os.getenv("VISION_MAX_TOKENS", "4096"))
DEBUG = os.getenv("VISION_DEBUG", "").lower() in ("1", "true", "yes")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

KINDS = ("flow_diagram", "table", "ux_flow", "other")

# Mermaid diagram types we accept as "looks like mermaid" (light validation —
# a full parse needs mermaid-cli, overkill for the PoC).
_MERMAID_HEADS = (
    "flowchart", "graph", "sequencediagram", "statediagram",
    "classdiagram", "erdiagram", "journey",
)

SYSTEM_PROMPT = """\
You analyze figures extracted from telecom device-requirement documents and
convert them to text that fully preserves their content for search and
question answering.

Classify the figure as exactly one of:
- "flow_diagram": a flowchart, state machine, call flow, message-sequence
  chart, or signaling diagram (boxes/lifelines with arrows).
- "table": a data table rendered as an image.
- "ux_flow": a sequence of device screens / UI mockups showing a user flow.
- "other": anything else (logo, photo, icon, chart without flow semantics).

Then convert:
- flow_diagram -> Mermaid. Use `sequenceDiagram` for message-sequence /
  signaling charts (lifelines exchanging messages); use `flowchart TD` for
  box-and-arrow flows and state machines. Preserve EVERY node label, message
  name, condition, and annotation verbatim; do not summarize or invent.
- table -> a GitHub-flavored markdown table reproducing every cell verbatim.
- ux_flow -> a numbered walkthrough: one item per screen, describing the
  screen's title, key elements, and the action that leads to the next screen.
- other -> one factual sentence describing the image.

Respond with STRICT JSON, no markdown fences, exactly this shape:
{
  "kind": "<flow_diagram|table|ux_flow|other>",
  "title": "<short figure title, from the image if visible>",
  "caption": "<1-2 sentence summary of what the figure shows>",
  "content": "<the converted content per the rules above>"
}
If part of the image is unreadable, convert what is readable and note the gap
in "caption" — never fabricate labels.
"""


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _chat(image_path: Path) -> str:
    """One OpenAI-compatible chat call with the image attached. Returns the
    assistant message content (str). Raises on transport/HTTP errors."""
    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text",
                     "text": "Classify and convert this figure. Respond with the strict JSON only."},
                    {"type": "image_url",
                     "image_url": {"url": _data_url(image_path)}},
                ],
            },
        ],
    }
    resp = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _strip_fences(text: str) -> str:
    m = re.match(r"\s*```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    return m.group(1) if m else text


def _parse_result(raw: str) -> dict:
    """Parse the model's JSON; salvage the first balanced object if it wrapped
    the JSON in prose. Raises ValueError when nothing parseable is found."""
    text = _strip_fences(raw)
    try:
        # strict=False: tolerate raw newlines/tabs inside JSON strings — a
        # common LLM emission (multi-line mermaid inside "content").
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1], strict=False)
    raise ValueError("no JSON object in model response")


def _validate(result: dict) -> list[str]:
    """Light sanity checks. Returns a list of warnings (empty = clean)."""
    warns: list[str] = []
    kind = result.get("kind", "")
    content = (result.get("content") or "").strip()
    if kind not in KINDS:
        warns.append(f"unknown kind {kind!r}")
    if not content:
        warns.append("empty content")
    if kind == "flow_diagram" and content:
        head = content.lstrip().splitlines()[0].strip().lower()
        if not any(head.startswith(h) for h in _MERMAID_HEADS):
            warns.append(f"content does not start with a mermaid header ({head[:40]!r})")
    if kind == "table" and content and "|" not in content:
        warns.append("table content has no '|' — not a markdown table?")
    return warns


def _fence_lang(kind: str) -> str:
    return {"flow_diagram": "mermaid", "table": "", "ux_flow": "", "other": ""}.get(kind, "")


def analyze_one(image_path: Path, out_dir: Path) -> dict:
    t0 = time.time()
    raw = _chat(image_path)
    if DEBUG:
        print(f"--- raw response ({image_path.name}) ---\n{raw}\n---", file=sys.stderr)
    result = _parse_result(raw)
    warns = _validate(result)
    elapsed = round(time.time() - t0, 1)

    record = {
        "image": str(image_path),
        "kind": result.get("kind", ""),
        "title": result.get("title", ""),
        "caption": result.get("caption", ""),
        "content": result.get("content", ""),
        "warnings": warns,
        "elapsed_s": elapsed,
        "model": MODEL,
        "raw": raw,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = image_path.stem
    (out_dir / f"{stem}.analysis.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    lang = _fence_lang(record["kind"])
    md = (
        f"# {record['title'] or stem}\n\n"
        f"**Image:** `{image_path.name}`  \n"
        f"**Kind:** {record['kind']}  \n"
        f"**Caption:** {record['caption']}\n\n"
        f"```{lang}\n{record['content']}\n```\n"
    )
    if warns:
        md += "\n> ⚠ " + "; ".join(warns) + "\n"
    (out_dir / f"{stem}.md").write_text(md, encoding="utf-8")
    return record


def _collect_images(args_paths: list[str]) -> list[Path]:
    images: list[Path] = []
    for p in args_paths:
        path = Path(p)
        if path.is_dir():
            images.extend(sorted(
                f for f in path.iterdir() if f.suffix.lower() in IMAGE_EXTS))
        elif path.is_file():
            images.append(path)
        else:
            print(f"WARN: {p} not found, skipping", file=sys.stderr)
    return images


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("images", nargs="+",
                    help="image file(s) and/or director(ies) of images")
    ap.add_argument("--out", default="./out", type=Path,
                    help="output dir for .analysis.json + .md files (default ./out)")
    args = ap.parse_args()

    if not MODEL:
        print("ERROR: set VISION_MODEL (and VISION_BASE_URL / VISION_API_KEY).",
              file=sys.stderr)
        return 2

    images = _collect_images(args.images)
    if not images:
        print("ERROR: no images found.", file=sys.stderr)
        return 2

    print(f"endpoint: {BASE_URL}  model: {MODEL}  images: {len(images)}\n")
    counts: dict[str, int] = {}
    failures = 0
    for img in images:
        try:
            rec = analyze_one(img, args.out)
        except Exception as e:  # noqa: BLE001 — PoC: keep going, report at end
            failures += 1
            print(f"[FAIL] {img.name}: {e}")
            continue
        counts[rec["kind"]] = counts.get(rec["kind"], 0) + 1
        flag = f"  ⚠ {'; '.join(rec['warnings'])}" if rec["warnings"] else ""
        print(f"[{rec['kind']:>12}] {img.name}  ({rec['elapsed_s']}s){flag}")

    print(f"\ndone: {sum(counts.values())} analyzed, {failures} failed  "
          f"{dict(sorted(counts.items()))}")
    print(f"review the .md files under {args.out}/")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
