"""Profile debug — inspect / bootstrap / validate document profiles.

Four modes:

  python -m core.src.profiler.profile_debug --env-dir <ENV_DIR>
      Analyzes <ENV_DIR>/out/extract/*_ir.json and out/profile/profile.json,
      prints a compact, no-proprietary-content summary (NFR-8 / D-012) safe
      to paste in chat for remote diagnosis.

  python -m core.src.profiler.profile_debug --emit-prompt
      Prints a prompt template the user can paste into their proprietary LLM
      chat interface, along with 1-2 representative documents, to bootstrap a
      DocumentProfile JSON. The LLM-emitted JSON goes to
      <ENV_DIR>/corrections/profile.json, where the pipeline picks it up via
      the corrections-override workflow (D-011 / FR-15) on the next run.

  python -m core.src.profiler.profile_debug --create --model <ollama-model> \
      --files <doc1> [<doc2> ...] --out <profile.json>
      Bootstraps a DocumentProfile by extracting the supplied representative
      documents and asking a local Ollama model to emit profile JSON. The
      response is auto-recovered (unterminated string at EOF) and sanitized
      (oversized / runaway / uncompilable regexes blanked or dropped) before
      being written. Routes through `core.src.llm.ollama_provider.OllamaProvider`
      to honor the LLMProvider Protocol seam — no direct ollama imports here.

  python -m core.src.profiler.profile_debug --validate <profile.json> [--fix] [--out <path>]
      Validates an LLM-emitted profile.json before the pipeline loads it:
      - JSON parses cleanly
      - Every regex string compiles (re.compile)
      - No regex exceeds a sane length bound (LLM repetition failure mode)
      - No runaway repetition pattern (e.g. "\\*\\*\\*..." 13kb of escapes)
      - DocumentProfile.load_json accepts the result
      With --fix, sanitizes problems in place (oversized / runaway /
      uncompilable regexes are blanked or dropped from list-valued fields)
      and writes back. With --out, writes the sanitized JSON elsewhere.

Use case: LLM-bootstrap workflow. The LLM occasionally emits oversized
or runaway-repetition regex strings (a known repetition failure mode);
validating before the pipeline loads it surfaces these cleanly without
exploding at runtime.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from core.src.models.document import DocumentIR


# Validation thresholds — tuned for typical LLM-emitted profiles.
_MAX_REGEX_LEN = 500           # any regex > this is suspect (LLM repetition)
_REPETITION_THRESHOLD = 12     # 2-4 char chunk repeated >=N times = runaway
_RUNAWAY_CHUNK_SIZES = (2, 3, 4)


def _is_runaway(s: str, threshold: int = _REPETITION_THRESHOLD) -> bool:
    """Detect runaway repetition (a small chunk repeating >= threshold times).

    Catches LLM repetition failures like '\\*\\*\\*\\*\\*...' — the model gets
    stuck in a token loop and emits the same 2-4 character pattern thousands
    of times consecutively.
    """
    if len(s) < _RUNAWAY_CHUNK_SIZES[0] * threshold:
        return False
    for chunk_size in _RUNAWAY_CHUNK_SIZES:
        chunk_total = chunk_size * threshold
        for i in range(len(s) - chunk_total + 1):
            chunk = s[i:i + chunk_size]
            if not chunk:
                continue
            if s[i:i + chunk_total] == chunk * threshold:
                return True
    return False


def _check_regex(pattern: str) -> tuple[str, str]:
    """Return (status, note) for a regex string.

    status in {'OK', 'BAD'}; 'OK' for empty strings (treated as 'no pattern').
    """
    if not pattern:
        return "OK", "empty"
    if len(pattern) > _MAX_REGEX_LEN:
        return "BAD", f"oversized {len(pattern)} chars (max {_MAX_REGEX_LEN})"
    if _is_runaway(pattern):
        return "BAD", "runaway repetition"
    try:
        re.compile(pattern)
    except re.error as e:
        return "BAD", f"compile error: {e}"
    return "OK", f"{len(pattern)} chars"


def _recover_unterminated(text: str, e: json.JSONDecodeError) -> tuple[str, int] | None:
    """Best-effort recovery of JSON with an unterminated string at EOF.

    LLM repetition failure mode: file ends mid-string after thousands of
    repeated escape sequences (e.g., `"...*\\*\\*\\*...` with no closing quote
    or following `]` / `}`). Recovery: truncate at the offending opening
    quote, replace the unterminated string with `""`, close any unclosed
    `{` / `[` structures with a stack walk that skips over balanced strings.

    Returns (recovered_text, closers_added) on success, None if the recovered
    text still doesn't parse.
    """
    if "Unterminated string" not in e.msg:
        return None
    if e.pos < 0 or e.pos >= len(text):
        return None

    head = text[:e.pos]

    # Walk head, tracking unclosed { and [ in a stack. Skip over balanced
    # strings (with backslash-escape handling) so brackets inside string
    # values don't contribute to the open-stack.
    stack: list[str] = []
    in_string = False
    escape = False
    for c in head:
        if escape:
            escape = False
            continue
        if in_string:
            if c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c in "{[":
            stack.append(c)
        elif c == "}":
            if stack and stack[-1] == "{":
                stack.pop()
        elif c == "]":
            if stack and stack[-1] == "[":
                stack.pop()

    closing_map = {"{": "}", "[": "]"}
    closers = "".join(closing_map[c] for c in reversed(stack))
    recovered = head + '""' + closers

    try:
        json.loads(recovered)
    except json.JSONDecodeError:
        return None
    return recovered, len(closers)


def _walk_regex_fields(data: dict, fix: bool) -> list[tuple[str, str, str, str]]:
    """Walk every regex-valued field in the profile. Return issue list.

    Each issue: (label, status, note, action).
    If fix=True, mutate `data` in place: blank bad scalar fields, drop bad
    list entries.
    """
    issues: list[tuple[str, str, str, str]] = []

    def check_str(parent: dict, key: str, label: str) -> None:
        val = parent.get(key, "")
        if not isinstance(val, str):
            issues.append((label, "BAD", f"not a string ({type(val).__name__})",
                           "blanked" if fix else "would blank"))
            if fix:
                parent[key] = ""
            return
        status, note = _check_regex(val)
        action = ""
        if status == "BAD":
            if fix:
                parent[key] = ""
                action = "blanked"
            else:
                action = "would blank"
        issues.append((label, status, note, action))

    def check_list(parent: dict, key: str, label: str) -> None:
        items = parent.get(key, [])
        if not isinstance(items, list):
            issues.append((label, "BAD", f"not a list ({type(items).__name__})",
                           "replaced with []" if fix else "would replace with []"))
            if fix:
                parent[key] = []
            return
        kept: list[str] = []
        for i, val in enumerate(items):
            entry_label = f"{label}[{i}]"
            if not isinstance(val, str):
                issues.append((entry_label, "BAD", f"not a string ({type(val).__name__})",
                               "dropped" if fix else "would drop"))
                continue
            status, note = _check_regex(val)
            action = ""
            if status == "BAD":
                action = "dropped" if fix else "would drop"
            else:
                kept.append(val)
            issues.append((entry_label, status, note, action))
        if fix:
            parent[key] = kept

    hd = data.get("heading_detection") if isinstance(data.get("heading_detection"), dict) else {}
    check_str(hd, "numbering_pattern", "heading_detection.numbering_pattern")

    rid = data.get("requirement_id") if isinstance(data.get("requirement_id"), dict) else {}
    check_str(rid, "pattern", "requirement_id.pattern")

    pm = data.get("plan_metadata") if isinstance(data.get("plan_metadata"), dict) else {}
    for fname in ("plan_name", "plan_id", "version", "release_date"):
        sub = pm.get(fname)
        if isinstance(sub, dict):
            check_str(sub, "pattern", f"plan_metadata.{fname}.pattern")

    zones = data.get("document_zones")
    if isinstance(zones, list):
        for i, z in enumerate(zones):
            if isinstance(z, dict):
                check_str(z, "section_pattern", f"document_zones[{i}].section_pattern")

    hf = data.get("header_footer") if isinstance(data.get("header_footer"), dict) else {}
    check_list(hf, "header_patterns", "header_footer.header_patterns")
    check_list(hf, "footer_patterns", "header_footer.footer_patterns")
    check_str(hf, "page_number_pattern", "header_footer.page_number_pattern")

    crp = data.get("cross_reference_patterns") if isinstance(data.get("cross_reference_patterns"), dict) else {}
    check_list(crp, "standards_citations", "cross_reference_patterns.standards_citations")
    check_str(crp, "internal_section_refs", "cross_reference_patterns.internal_section_refs")
    check_str(crp, "requirement_id_refs", "cross_reference_patterns.requirement_id_refs")

    return issues


def _validate_profile(
    path: Path,
    fix: bool,
    out_path: Path | None,
    recover: bool = False,
) -> int:
    """Validate (and optionally sanitize) an LLM-emitted profile.json.

    Returns the process exit code: 0 if all OK after any applied fixes, 1 otherwise.
    """
    if not path.exists():
        print(f"VLD ERR file not found: {path}")
        return 1
    try:
        text = path.read_text()
    except Exception as e:
        print(f"VLD ERR read failed: {type(e).__name__}: {e}")
        return 1
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        # Try best-effort recovery for the LLM unterminated-string-at-EOF case.
        if recover and "Unterminated string" in e.msg:
            result = _recover_unterminated(text, e)
            if result is not None:
                recovered_text, closers = result
                # Diff in size shows how much was discarded.
                discarded = len(text) - len(recovered_text)
                print(
                    f"VLD recovered: truncated unterminated string at line "
                    f"{e.lineno} col {e.colno}; closed {closers} JSON structure(s); "
                    f"discarded {discarded} bytes"
                )
                text = recovered_text
                data = json.loads(text)
                # Continue with regex-level validation on the recovered data.
            else:
                print(f"VLD ERR recovery failed for parse error at line {e.lineno} col {e.colno}: {e.msg}")
                return 1
        else:
            print(f"VLD ERR parse error at line {e.lineno} col {e.colno}: {e.msg}")
            # Show context around the error so the user can spot the cause
            # (most often: an unescaped \ inside a regex string — JSON needs \\)
            lines = text.split("\n")
            start = max(1, e.lineno - 2)
            end = min(len(lines), e.lineno + 2)
            print()
            print(f"Context (lines {start}-{end}):")
            for i in range(start - 1, end):
                marker = ">>>" if (i + 1) == e.lineno else "   "
                display = lines[i] if len(lines[i]) <= 160 else lines[i][:157] + "..."
                print(f"  {marker} {i + 1:4d}: {display}")
                if (i + 1) == e.lineno:
                    # Caret at the offending column (account for "  >>> NNNN: ")
                    pad = 8 + len(f"{i + 1:4d}: ") + e.colno - 1
                    print(f"  {' ' * pad}^")
            print()
            if "Unterminated string" in e.msg:
                print(
                    "Hint: file ends mid-string (LLM runaway-repetition failure). "
                    "Re-run with --recover to truncate the offending field and "
                    "close open JSON structures automatically."
                )
            else:
                print(
                    "Hint: most common cause is an unescaped backslash inside a "
                    "regex pattern string. JSON requires `\\\\` to represent a "
                    "single literal backslash. Edit the file manually to fix, "
                    "then re-run --validate."
                )
            return 1
    if not isinstance(data, dict):
        print(f"VLD ERR top-level JSON is not an object (got {type(data).__name__})")
        return 1

    print(f"VLD {path.name} ({path})")
    print(f"VLD bytes={len(text)} top_level_keys={len(data)}")
    print()

    issues = _walk_regex_fields(data, fix)
    bad_count = sum(1 for _, s, _, _ in issues if s == "BAD")
    ok_count = sum(1 for _, s, _, _ in issues if s == "OK")

    for label, status, note, action in issues:
        line = f"{status} {label}: {note}"
        if action:
            line += f" -> {action}"
        print(line)

    print()
    print(f"summary: {ok_count} OK, {bad_count} BAD")

    if fix:
        # --fix always writes (otherwise an --out target stays at its prior
        # state — possibly empty — when there's nothing to sanitize, which
        # surprises callers who expected --fix to emit a guaranteed file).
        target = out_path or path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        target.write_text(payload)
        if bad_count > 0:
            print(f"fix: sanitized; wrote {len(payload)} bytes to {target}")
        else:
            print(f"fix: no issues; wrote {len(payload)} bytes to {target}")
    elif bad_count > 0:
        print("fix: rerun with --fix to apply (blank bad regex fields, drop bad list entries)")

    # Schema-level load check on whichever file is final.
    final = (out_path if (fix and out_path) else path)
    try:
        from core.src.profiler.profile_schema import DocumentProfile
        DocumentProfile.load_json(final)
        print("schema: DocumentProfile.load_json OK")
    except Exception as e:
        print(f"schema: load FAIL — {type(e).__name__}: {e}")
        return 1

    return 0 if bad_count == 0 else 1


def _format_ir_lines(label: str, ir: DocumentIR) -> list[str]:
    """One or two lines describing an IR — no proprietary content."""
    type_counts = Counter(b.type.value for b in ir.content_blocks)
    # h:N,p:N,t:N,i:N,e:N (heading / paragraph / table / image / embedded)
    type_str = ",".join(f"{t[0]}:{c}" for t, c in sorted(type_counts.items()))

    style_counts = Counter(b.style for b in ir.content_blocks if b.style)

    sizes = sorted({round(b.font_info.size, 1) for b in ir.content_blocks if b.font_info})
    bold_count = sum(1 for b in ir.content_blocks if b.font_info and b.font_info.bold)

    lines = [f"{label} fmt={ir.source_format} blk={ir.block_count} type={type_str}"]

    if style_counts:
        # Top 6 styles by count, truncated
        top = style_counts.most_common(6)
        style_summary = ",".join(f"{_safe_style(s)}:{c}" for s, c in top)
        lines.append(f"{label} styles={len(style_counts)} ({style_summary})")
    else:
        lines.append(f"{label} styles=0")

    if sizes:
        sizes_str = ",".join(str(s) for s in sizes[:8])
        lines.append(f"{label} fonts={len(sizes)} sizes={sizes_str} bold={bold_count}")
    else:
        lines.append(f"{label} fonts=0")

    return lines


def _safe_style(style: str) -> str:
    """Strip whitespace and truncate long style names for compact rendering."""
    s = style.replace(" ", "").replace(",", "_")
    return s[:24]


def _format_profile_lines(profile_path: Path) -> list[str]:
    """Compact summary of the emitted profile (no proprietary content)."""
    if not profile_path.exists():
        return [f"PROFILE absent ({profile_path.name} not at out/profile/)"]

    try:
        with open(profile_path) as f:
            p = json.load(f)
    except Exception as e:
        return [f"PROFILE load_error={type(e).__name__}"]

    hd = p.get("heading_detection") or {}
    method = hd.get("method", "?")
    levels = len(hd.get("heading_levels", []) or [])

    req = p.get("requirement_id") or {}
    req_pattern_count = len(req.get("patterns", []) or [])

    zones_block = p.get("document_zones") or {}
    if isinstance(zones_block, dict):
        zones = len(zones_block.get("zones", []) or [])
    elif isinstance(zones_block, list):
        zones = len(zones_block)
    else:
        zones = 0

    hf_block = p.get("header_footer") or {}
    hf_pat = (
        len(hf_block.get("patterns", []) or [])
        if isinstance(hf_block, dict) else 0
    )

    xref = p.get("cross_references") or {}
    xref_count = sum(
        len(xref.get(k, []) or [])
        for k in (
            "internal_patterns",
            "standards_patterns",
            "cross_plan_patterns",
            "patterns",
        )
        if isinstance(xref.get(k, []), list)
    )

    body = p.get("body_text") or {}
    body_size = body.get("dominant_size", body.get("size", "?"))

    return [
        f"PROFILE method={method} levels={levels} rpat={req_pattern_count} zones={zones}",
        f"PROFILE hf_patterns={hf_pat} xref_patterns={xref_count} body_size={body_size}",
    ]


_LLM_PROMPT_TEMPLATE = """\
You are producing a single JSON "DocumentProfile" that drives a profile-driven, LLM-free structural parser. The parser uses your output to identify headings, requirement IDs, document zones, and cross-references in the documents below.

Do NOT assume conventions from any document family you may know — derive every regex and structural rule strictly from what you observe in the actual documents pasted at the bottom.

Emit ONE JSON object — no prose, no markdown fences, no commentary. The JSON must match the schema exactly.

SCHEMA (placeholder values; replace based on the actual documents):

{
  "profile_name": "<short-identifier>",
  "profile_version": 1,
  "created_from": ["<filename1>.docx", "<filename2>.docx"],
  "last_updated": "YYYY-MM-DD",
  "heading_detection": {
    "method": "docx_styles" | "font_size_clustering",
    "levels": [
      {
        "level": 1,
        "font_size_min": <float, points>,
        "font_size_max": <float, points>,
        "bold": true | false | null,
        "all_caps": true | false | null,
        "sample_texts": [],
        "count": 0
      }
    ],
    "numbering_pattern": "<regex matching the section-number prefix on heading lines, e.g. ^(\\\\d+\\\\.)+\\\\d*\\\\s>",
    "max_observed_depth": <int>
  },
  "requirement_id": {
    "pattern": "<regex matching individual requirement labels, or empty string if the docs don't use per-requirement IDs>",
    "components": {"prefix": "<observed>", "separator": "<observed>", "plan_id_position": <int>, "number_position": <int>},
    "sample_ids": [],
    "total_found": 0
  },
  "plan_metadata": {
    "plan_name":    {"location": "first_page", "pattern": "<regex with one capture group, or empty>", "sample_value": ""},
    "plan_id":      {"location": "first_page", "pattern": "<regex>", "sample_value": ""},
    "version":      {"location": "first_page", "pattern": "<regex>", "sample_value": ""},
    "release_date": {"location": "first_page", "pattern": "<regex>", "sample_value": ""}
  },
  "document_zones": [
    {"section_pattern": "<regex>", "zone_type": "<one of: introduction | hardware_specs | software_specs | scenarios | provisioning | performance | test_coverage | references | content>", "description": "<heading text>", "heading_text": "<heading text>"}
  ],
  "header_footer": {
    "header_patterns": [],
    "footer_patterns": [],
    "page_number_pattern": "<regex matching repeating page-number lines, or empty>"
  },
  "cross_reference_patterns": {
    "standards_citations": [
      "<one regex per citation flavor observed: 3GPP TS, IETF RFC, GSMA, IEEE, Wi-Fi Alliance, etc.>"
    ],
    "internal_section_refs": "<regex for 'see section X.Y' style intra-document refs, or empty>",
    "requirement_id_refs": "<same regex as requirement_id.pattern, or empty if no requirement IDs>"
  },
  "body_text": {
    "font_size_min": <float>,
    "font_size_max": <float>,
    "font_families": []
  }
}

GUIDANCE:
- For DOCX with explicit "Heading 1" / "Heading 2" paragraph styles → method = "docx_styles". For visual-only headings (bold + larger font without semantic styles) → method = "font_size_clustering".
- Requirement IDs may follow any scheme ("REQ-NNN", "VOWIFI_R042", "[V-001]", or none at all). If the documents enumerate requirements without a stable label format, leave requirement_id.pattern as empty string and the parser will fall back to section-number anchoring.
- document_zones: only top-level structural sections. Skip nested subsections. zone_type must be one of the listed values; pick "content" if none of the others fits.
- plan_metadata fields that don't appear in the documents → empty pattern string, empty sample_value.
- standards_citations: list one regex per citation flavor (3GPP TS XXX.YYY, RFC NNNN, etc.). Don't conflate flavors into one regex.
- All sample_texts arrays can be empty; they're informational.

Now, here are the representative documents:

[PASTE 1-2 .docx FILES BELOW — text content of the documents]
"""


# System prompt and trailing reinforcement for --create. Small instruct-tuned
# models (Gemma 4B, etc.) tend to summarize the document instead of emitting
# JSON when the schema instruction sits ~30k chars before generation begins;
# anchoring the role at the system level and repeating "JSON only, start with
# {" right before the model speaks fixes this in practice.
_LLM_CREATE_SYSTEM = (
    "You are a JSON emitter. You output ONLY one JSON object — no prose, no "
    "markdown, no commentary, no explanation, no greeting. Your entire response "
    "must begin with `{` as the very first character and end with `}` as the "
    "very last character. Anything else breaks the consumer."
)

_LLM_CREATE_TRAILER = (
    "\n\nNow emit the DocumentProfile JSON object derived from the documents above. "
    "Begin with `{` as the very first character of your response. Do not summarize "
    "the documents. Do not explain what you are about to do. Do not wrap in "
    "markdown fences. JSON only."
)


# LLM-create mode: cap rendered text per doc so two big telecom requirements
# files don't blow past Gemma's context window. 30k chars × 2 docs ≈ 8k tokens
# which leaves plenty of room for the schema template and the model's response.
_MAX_DOC_CHARS = 30_000
_LLM_MAX_TOKENS = 8192


def _render_block_for_prompt(block) -> str:
    """One-line, structurally-annotated rendering of a ContentBlock for the LLM."""
    bt = block.type.value if hasattr(block.type, "value") else str(block.type)
    fi = block.font_info
    hint_parts: list[str] = []
    if fi is not None:
        hint_parts.append(f"size={round(fi.size, 1)}")
        if fi.bold:
            hint_parts.append("bold")
        if getattr(fi, "all_caps", False):
            hint_parts.append("caps")
    if block.style:
        hint_parts.append(f"style={block.style!r}")
    hint = (" {" + " ".join(hint_parts) + "}") if hint_parts else ""

    if bt == "heading":
        lvl = block.level if block.level is not None else "?"
        return f"[H{lvl}{hint}] {block.text}"
    if bt == "paragraph":
        return f"[P{hint}] {block.text}"
    if bt == "table":
        first_row = " | ".join(block.headers) if block.headers else ""
        # Cap first-row preview so wide telecom tables don't dominate the prompt.
        if len(first_row) > 200:
            first_row = first_row[:197] + "..."
        return f"[TABLE rows={len(block.rows)} cols={len(block.headers)}] {first_row}"
    if bt == "image":
        return "[IMAGE]"
    if bt == "embedded_object":
        return f"[EMBEDDED type={block.object_type}]"
    return f"[{bt.upper()}{hint}]"


def _render_ir_for_prompt(label: str, ir: DocumentIR, max_chars: int) -> str:
    """Render a DocumentIR as text-with-structural-hints for the LLM, truncated to max_chars."""
    header = f"=== {label} (file={Path(ir.source_file).name}, format={ir.source_format}, blocks={ir.block_count}) ==="
    out = [header]
    total = len(header) + 1
    truncated_at = -1
    for i, block in enumerate(ir.content_blocks):
        line = _render_block_for_prompt(block)
        if total + len(line) + 1 > max_chars:
            truncated_at = i
            break
        out.append(line)
        total += len(line) + 1
    if truncated_at >= 0:
        out.append(
            f"[... truncated: rendered {truncated_at} of {ir.block_count} blocks "
            f"to stay under {max_chars} chars]"
        )
    return "\n".join(out)


def _strip_markdown_fences(text: str) -> str:
    """Strip ```json ... ``` or ``` ... ``` fences if the LLM wrapped its response."""
    s = text.strip()
    if not s.startswith("```"):
        return text
    # Drop the opening fence (with optional language tag) and the closing fence.
    first_newline = s.find("\n")
    if first_newline == -1:
        return text
    body = s[first_newline + 1:]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3].rstrip()
    return body


def _extract_json_object(text: str) -> str | None:
    """Find the first balanced top-level JSON object in `text`.

    Walks the string looking for the first `{`, then advances with brace-depth
    tracking — string-aware (skips `{`/`}` inside `"..."`) and escape-aware.
    Returns the substring `{...}` (inclusive) or None if no balanced object
    exists.

    Use case: small instruct-tuned models often emit a prose preamble ("Here
    is the profile:" or a document summary) before the JSON despite being
    told not to. Stripping fences alone doesn't catch that; this does.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if in_string:
            if c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _create_profile_via_llm(
    model: str,
    files: list[Path],
    out_path: Path,
) -> int:
    """Call a local Ollama model to bootstrap a DocumentProfile from `files`.

    Returns 0 on success (JSON written, schema-loadable), non-zero otherwise.
    Output is line-prefixed with `CRT` so it's distinguishable in compact reports.
    """
    if not files:
        print("CRT ERR no files supplied")
        return 1

    # Resolve and validate inputs up front so we fail fast before extraction.
    resolved: list[Path] = []
    for f in files:
        p = Path(f).expanduser().resolve()
        if not p.exists():
            print(f"CRT ERR file not found: {p}")
            return 1
        resolved.append(p)

    print(f"CRT model={model} files={len(resolved)} out={out_path}")

    # Lazy imports — keep top-of-module clean and avoid forcing extraction
    # libraries (fitz, python-docx, openpyxl) on callers that only use --validate.
    from core.src.extraction.registry import extract_document, get_extractor

    rendered_docs: list[str] = []
    for i, path in enumerate(resolved, start=1):
        try:
            get_extractor(path)  # surfaces "no extractor for .ext" cleanly
        except ValueError as e:
            print(f"CRT ERR DOC{i} unsupported format: {e}")
            return 1
        try:
            ir = extract_document(path)
        except Exception as e:
            print(f"CRT ERR DOC{i} extraction failed: {type(e).__name__}: {e}")
            return 1
        rendered = _render_ir_for_prompt(f"DOC{i}", ir, _MAX_DOC_CHARS)
        rendered_docs.append(rendered)
        print(f"CRT DOC{i} fmt={ir.source_format} blocks={ir.block_count} rendered={len(rendered)} chars")

    content_blob = "\n\n".join(rendered_docs)
    prompt = _LLM_PROMPT_TEMPLATE.replace(
        "[PASTE 1-2 .docx FILES BELOW — text content of the documents]",
        content_blob,
    ) + _LLM_CREATE_TRAILER

    from core.src.llm.ollama_provider import OllamaProvider
    try:
        provider = OllamaProvider(model=model)
    except ConnectionError as e:
        print(f"CRT ERR ollama unreachable: {e}")
        return 1

    print(f"CRT calling ollama prompt={len(prompt)} chars max_tokens={_LLM_MAX_TOKENS}")
    try:
        raw = provider.complete(
            prompt,
            system=_LLM_CREATE_SYSTEM,
            temperature=0.0,
            max_tokens=_LLM_MAX_TOKENS,
        )
    except Exception as e:
        print(f"CRT ERR ollama call failed: {type(e).__name__}: {e}")
        return 1

    stats = provider.last_call_stats
    if stats:
        print(
            f"CRT ollama tokens={stats.get('eval_count', 0)} "
            f"tps={stats.get('tokens_per_second', 0)} "
            f"duration_s={stats.get('total_duration_s', 0):.1f}"
        )

    body = _strip_markdown_fences(raw)
    print(f"CRT response={len(raw)} chars body={len(body)} chars")

    # Try parse → tolerant-extract → recover-on-unterminated → sanitize.
    # Mirrors --validate but applied automatically; the create path tolerates
    # prose preambles (small models occasionally summarize before the JSON
    # despite the system prompt) by extracting the first balanced {...}.
    data: dict | None = None
    parse_target = body
    try:
        data = json.loads(parse_target)
    except json.JSONDecodeError as e:
        extracted = _extract_json_object(body)
        if extracted is not None and extracted != body.strip():
            preamble_chars = body.find("{")
            print(
                f"CRT extracted: skipped {preamble_chars} chars of preamble; "
                f"object body={len(extracted)} chars"
            )
            parse_target = extracted
            try:
                data = json.loads(parse_target)
            except json.JSONDecodeError as e2:
                # Fall through to unterminated-string recovery using the
                # extracted text — the most likely remaining failure mode.
                e = e2

        if data is None:
            if "Unterminated string" in e.msg:
                recovered = _recover_unterminated(parse_target, e)
                if recovered is None:
                    print(f"CRT ERR LLM emitted unterminated JSON; recovery failed at line {e.lineno} col {e.colno}")
                    return 1
                recovered_text, closers = recovered
                discarded = len(parse_target) - len(recovered_text)
                print(
                    f"CRT recovered: truncated unterminated string at line {e.lineno} "
                    f"col {e.colno}; closed {closers} structure(s); discarded {discarded} bytes"
                )
                try:
                    data = json.loads(recovered_text)
                except json.JSONDecodeError as e3:
                    print(f"CRT ERR recovered text still failed parse: {e3.msg}")
                    return 1
            else:
                print(f"CRT ERR LLM response failed JSON parse at line {e.lineno} col {e.colno}: {e.msg}")
                preview = body[:200].replace("\n", "\\n")
                print(f"CRT preview: {preview}")
                return 1

    if not isinstance(data, dict):
        print(f"CRT ERR top-level JSON is not an object (got {type(data).__name__})")
        return 1

    issues = _walk_regex_fields(data, fix=True)
    bad_count = sum(1 for _, s, _, _ in issues if s == "BAD")
    ok_count = sum(1 for _, s, _, _ in issues if s == "OK")
    print(f"CRT sanitize ok={ok_count} bad={bad_count}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    out_path.write_text(payload)
    print(f"CRT wrote {len(payload)} bytes to {out_path}")

    try:
        from core.src.profiler.profile_schema import DocumentProfile
        DocumentProfile.load_json(out_path)
        print("CRT schema: DocumentProfile.load_json OK")
    except Exception as e:
        print(f"CRT ERR schema load failed — {type(e).__name__}: {e}")
        return 1

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect extracted IRs and the emitted profile in compact format, "
            "emit an LLM prompt template for bootstrapping a DocumentProfile, "
            "or validate (and optionally sanitize) an LLM-emitted profile.json."
        ),
    )
    parser.add_argument(
        "--env-dir",
        type=Path,
        help="Path to env_dir (analysis mode; not used with --emit-prompt or --validate).",
    )
    parser.add_argument(
        "--max-docs",
        type=int,
        default=10,
        help="Maximum number of IR files to summarize (default: 10).",
    )
    parser.add_argument(
        "--emit-prompt",
        action="store_true",
        help=(
            "Print the LLM prompt template (no env_dir needed) and exit. "
            "Paste into your proprietary LLM chat with 1-2 representative documents; "
            "save the LLM's JSON to <env_dir>/corrections/profile.json."
        ),
    )
    parser.add_argument(
        "--validate",
        type=Path,
        metavar="PROFILE_JSON",
        help=(
            "Path to an LLM-emitted profile.json to validate. Checks JSON parse, "
            "regex compile, length bounds, runaway repetition, and schema load."
        ),
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help=(
            "With --validate: sanitize problems in place — blank bad regex "
            "fields, drop bad list entries — and write back."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        help=(
            "With --validate --fix: write sanitized JSON to this path instead "
            "of overwriting the input."
        ),
    )
    parser.add_argument(
        "--recover",
        action="store_true",
        help=(
            "With --validate: when the file ends mid-string (LLM runaway-"
            "repetition failure where the model never closed a regex value), "
            "truncate the offending field to empty, close any unclosed JSON "
            "structures, and continue validation on the recovered data. "
            "Pair with --fix --out to write the recovered JSON to disk."
        ),
    )
    parser.add_argument(
        "--create",
        action="store_true",
        help=(
            "Bootstrap a DocumentProfile by extracting --files and asking the "
            "Ollama --model to emit profile JSON. Requires --model, --files, --out."
        ),
    )
    parser.add_argument(
        "--model",
        type=str,
        help="With --create: Ollama model name (e.g. gemma4:e4b, gemma3:4b).",
    )
    parser.add_argument(
        "--files",
        type=Path,
        nargs="+",
        metavar="DOC",
        help="With --create: 1+ representative documents (PDF/DOCX/XLSX) to extract and feed to the LLM.",
    )
    args = parser.parse_args()

    if args.emit_prompt:
        sys.stdout.write(_LLM_PROMPT_TEMPLATE)
        return

    if args.create:
        if not args.model:
            parser.error("--create requires --model")
        if not args.files:
            parser.error("--create requires --files")
        if not args.out:
            parser.error("--create requires --out")
        rc = _create_profile_via_llm(
            model=args.model,
            files=list(args.files),
            out_path=Path(args.out).expanduser().resolve(),
        )
        sys.exit(rc)

    if args.validate:
        rc = _validate_profile(
            Path(args.validate).expanduser().resolve(),
            fix=args.fix,
            out_path=Path(args.out).expanduser().resolve() if args.out else None,
            recover=args.recover,
        )
        sys.exit(rc)

    if args.env_dir is None:
        parser.error(
            "specify one of --env-dir, --emit-prompt, or --validate <profile.json>"
        )

    env_dir = Path(args.env_dir).expanduser().resolve()
    extract_dir = env_dir / "out" / "extract"
    profile_dir = env_dir / "out" / "profile"
    corrections_dir = env_dir / "corrections"

    print(f"PRD {env_dir.name} {datetime.now().strftime('%Y-%m-%dT%H:%M')}")
    print(f"ENV {env_dir}")

    if not extract_dir.exists():
        print(f"ERR no extract dir at out/extract/")
        sys.exit(1)

    ir_files = sorted(extract_dir.glob("*_ir.json"))
    if not ir_files:
        print("ERR no *_ir.json files in out/extract/")
        sys.exit(1)

    print()
    for i, f in enumerate(ir_files[: args.max_docs], start=1):
        try:
            ir = DocumentIR.load_json(f)
        except Exception as e:
            print(f"DOC{i} load_error={type(e).__name__}")
            continue
        for line in _format_ir_lines(f"DOC{i}", ir):
            print(line)

    if len(ir_files) > args.max_docs:
        print(f"... {len(ir_files) - args.max_docs} more IR files (use --max-docs)")

    print()
    for line in _format_profile_lines(profile_dir / "profile.json"):
        print(line)

    print()
    profile_corr = corrections_dir / "profile.json"
    taxonomy_corr = corrections_dir / "taxonomy.json"
    print(
        f"CORR profile={'present' if profile_corr.exists() else 'absent'} "
        f"taxonomy={'present' if taxonomy_corr.exists() else 'absent'}"
    )

    print()
    print("ERR none")


if __name__ == "__main__":
    main()
