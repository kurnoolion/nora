"""Pipeline report generation.

Produces two report formats:
  1. Verbose terminal report — for the user's own analysis
  2. Compact report — short enough to paste in chat for collaborative debugging

Usage:
    from src.pipeline.report import format_compact_report, format_verbose_report

    text = format_compact_report(results, hw_info, model_choice, env_name)
    print(text)  # ~10 lines, paste-friendly
"""

from __future__ import annotations

from datetime import datetime
from textwrap import dedent

from src.pipeline.stages import StageResult

# Short stage name for compact report (max 3 chars)
_SHORT = {
    "extract": "EXT", "profile": "PRF", "parse": "PRS",
    "resolve": "RES", "taxonomy": "TAX", "standards": "STD",
    "graph": "GRF", "vectorstore": "VEC", "eval": "EVL",
}


def format_compact_report(
    results: list[StageResult],
    hw_summary: str = "",
    model_name: str = "",
    env_name: str = "",
) -> str:
    """Generate a compact report suitable for pasting in chat.

    Designed to be short enough to type/paste — typically 8-15 lines.
    Contains no document content, only structural metrics.
    """
    lines: list[str] = []
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M")
    lines.append(f"RPT {env_name or 'standalone'} {ts}")
    if hw_summary:
        lines.append(f"HW {hw_summary}")
    if model_name:
        lines.append(f"MDL {model_name}")

    errors: list[str] = []
    for r in results:
        short = _SHORT.get(r.stage, r.stage[:3].upper())
        elapsed = f"{r.elapsed_seconds:.0f}s"
        stat_parts = _format_stats_compact(r.stage, r.stats)
        lines.append(f"{short} {r.status} {elapsed} {stat_parts}")
        if r.error_code:
            errors.append(f"{r.error_code}: {r.error_message}")

    if errors:
        lines.append("ERR " + "; ".join(errors))
    else:
        lines.append("ERR none")

    return "\n".join(lines)


def _format_stats_compact(stage: str, stats: dict) -> str:
    """Format stage stats into a compact key=value string."""
    if not stats:
        return ""

    # Per-stage compact format
    formatters = {
        "extract": lambda s: f"docs={s.get('docs',0)} blk={s.get('blocks',0)} tbl={s.get('tables',0)}",
        "profile": lambda s: (
            f"src={s['source']}" if "source" in s
            else f"lvl={s.get('heading_levels',0)} rpat={s.get('req_patterns',0)} zone={s.get('zones',0)}"
        ),
        "parse": lambda s: f"req={s.get('reqs',0)} dep={s.get('max_depth',0)} docs={s.get('docs',0)}",
        "resolve": lambda s: f"int={s.get('internal',0)} xp={s.get('cross_plan',0)} std={s.get('standards',0)}",
        "taxonomy": lambda s: (
            f"src={s['source']}" if "source" in s
            else f"feat={s.get('features',0)} docs={s.get('docs',0)}"
        ),
        "standards": lambda s: f"dl={s.get('downloaded',0)} prs={s.get('parsed',0)} ext={s.get('extracted',0)}",
        "graph": lambda s: f"n={s.get('nodes',0)} e={s.get('edges',0)} cc={s.get('components',0)}",
        "vectorstore": lambda s: f"chk={s.get('chunks',0)} dup={s.get('dedup',0)}",
        "eval": lambda s: f"q={s.get('questions',0)} overall={s.get('overall','?')} acc={s.get('accuracy','?')}",
    }
    formatter = formatters.get(stage)
    if formatter:
        return formatter(stats)
    return " ".join(f"{k}={v}" for k, v in stats.items())


def format_verbose_report(
    results: list[StageResult],
    hw_summary: str = "",
    model_name: str = "",
    env_name: str = "",
) -> str:
    """Generate a verbose terminal report."""
    lines: list[str] = []
    sep = "=" * 60
    lines.append(sep)
    lines.append("PIPELINE REPORT")
    lines.append(sep)
    lines.append(f"  Environment: {env_name or 'standalone'}")
    lines.append(f"  Time:        {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if hw_summary:
        lines.append(f"  Hardware:    {hw_summary}")
    if model_name:
        lines.append(f"  Model:       {model_name}")

    total_time = sum(r.elapsed_seconds for r in results)
    passed = sum(1 for r in results if r.ok)
    lines.append(f"  Stages:      {passed}/{len(results)} OK ({total_time:.1f}s total)")

    lines.append("")
    lines.append("STAGES")
    for i, r in enumerate(results, 1):
        icon = {"OK": "[+]", "WARN": "[!]", "FAIL": "[X]", "SKIP": "[-]"}.get(r.status, "[?]")
        lines.append(f"  {icon} {i}. {r.stage:<14} {r.status:4s} {r.elapsed_seconds:6.1f}s")
        for k, v in r.stats.items():
            lines.append(f"       {k}: {v}")
        for w in r.warnings:
            lines.append(f"       WARN: {w}")
        if r.error_code:
            lines.append(f"       ERROR: {r.error_code} — {r.error_message}")

    lines.append("")
    lines.append("COMPACT (paste in chat):")
    lines.append("─" * 40)
    compact = format_compact_report(results, hw_summary, model_name, env_name)
    for line in compact.splitlines():
        lines.append(f"  {line}")
    lines.append("─" * 40)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Quality check templates — users type these back in chat
# ---------------------------------------------------------------------------

QC_TEMPLATES = {
    "profile": dedent("""\
        QC {env} profile
        lvl={Y/N} rpat={Y/N} zone={Y/N} body={Y/N} hf={Y/N}
        miss: (comma-sep patterns missed, or "none")
        notes: (free text)"""),

    "extract": dedent("""\
        QC {env} extract
        docs={N} ok={N} issues={N}
        issues: DOC_NAME(description), ... or "none"
        notes: (free text)"""),

    "parse": dedent("""\
        QC {env} parse
        docs={N} ok={N} issues={N}
        issues: DOC_NAME(description), ... or "none"
        notes: (free text)"""),

    "taxonomy": dedent("""\
        QC {env} taxonomy
        feat={N} correct={N} wrong={N} miss={N}
        wrong: FEAT_NAME(reason), ... or "none"
        miss: FEAT_NAME, FEAT_NAME, ... or "none"
        notes: (free text)"""),

    "eval": dedent("""\
        QC {env} eval
        q={N} pass={N} fail={N}
        fail: Q_ID(reason), ... or "none"
        notes: (free text)"""),
}


# Correction feedback templates — users type these to tell us what they changed
FIX_TEMPLATES = {
    "profile": dedent("""\
        FIX {env} profile
        (one change per line)
        heading_threshold: OLD -> NEW
        req_pattern: added "PATTERN" / removed "PATTERN"
        zone: added/removed ZONE_NAME
        notes: (free text)"""),

    "taxonomy": dedent("""\
        FIX {env} taxonomy
        added={N} removed={N} renamed={N}
        add: FEAT(keywords: kw1,kw2), ...
        remove: FEAT, ...
        rename: OLD->NEW, ...
        notes: (free text)"""),

    "eval": dedent("""\
        FIX {env} eval Q_ID
        expected_plans: +PLAN / -PLAN
        expected_req_ids: +REQ_ID / -REQ_ID
        notes: (free text)"""),
}


def print_qc_template(stage: str, env_name: str = "ENV") -> str:
    """Get the quality check template for a stage."""
    tmpl = QC_TEMPLATES.get(stage, f"QC {env_name} {stage}\nnotes: (free text)")
    return tmpl.replace("{env}", env_name)


def print_fix_template(artifact: str, env_name: str = "ENV") -> str:
    """Get the correction feedback template for an artifact."""
    tmpl = FIX_TEMPLATES.get(artifact, f"FIX {env_name} {artifact}\nnotes: (free text)")
    return tmpl.replace("{env}", env_name)
