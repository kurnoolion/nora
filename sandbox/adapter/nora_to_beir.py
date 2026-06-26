"""NORA → BEIR adapter for the SIRA standalone-sandbox experiment.

Reads parsed `_tree.json` artifacts from `<env_dir>/out/parse/` and emits
the four files in the SIRA-internal `raw/` layout:

  <out>/raw/corpus.jsonl         — one row per Requirement {_id, title, text}
  <out>/raw/queries-test.jsonl   — one row per eval question {_id, text}
                                    (from `core.src.eval.questions`)
  <out>/raw/qrels-test.jsonl     — {query-id, corpus-id, score} per qrel
                                    (score=1 for every expected req_id;
                                    relevance is binary)
  <out>/raw/metadata.json        — {name, source, num_corpus, splits}

When `<out>/raw/metadata.json` exists, SIRA's `scripts/prepare_mteb_data.py`
early-returns ("Already prepared at … — skipping download.") so the full
hydra pipeline (`scripts/run_pipeline.py data=nora ...`) runs unchanged
against our local data. No HuggingFace download happens.

Corpus row shape — `{"_id": req_id, "title": "<section_num section_title>",
"text": "<markdown body>"}`. The body is constructed as:

    # {section_number} {section_title}
    **req_id**: {req_id}
    **plan**: {plan_name or plan_id}
    **hierarchy**: {parent_section path}

    {body text with inline acronym expansion per chunk_builder D-032 / D-043}

    **Cross-refs**: {comma-joined internal req_ids + standards specs}

Acronym expansion uses `core.src.vectorstore.chunk_builder._expand_definitions`
directly — guarantees parity with NORA's BM25 lane (apples-to-apples test
per strand decision).

Locked strand decisions reflected here:
  * acronym pre-expansion ON (D-032/D-043 inline format)
  * heading-only reqs INCLUDED (title becomes the text)
  * struck reqs naturally absent (parser drops per D-031/D-037)
  * combined corpus across all parsed plans (18-Q eval spans plans)
  * `_id` = req_id so the qrels join works cleanly

Usage:
    python -m sandbox.adapter.nora_to_beir \\
        --env-dir <env_dir> \\
        --output sandbox/adapter/out/nora_beir
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from core.src.parser.structural_parser import build_context_string
from core.src.vectorstore.chunk_builder import ChunkBuilder


# We need ChunkBuilder's two static helpers; importing them by reference
# avoids re-implementing the regex compile + expansion logic.
_compile_defs = ChunkBuilder._compile_definitions_regex
_expand_defs = ChunkBuilder._expand_definitions


def _load_trees(env_dir: Path) -> list[dict[str, Any]]:
    parse_dir = env_dir / "out" / "parse"
    if not parse_dir.is_dir():
        raise FileNotFoundError(
            f"No parse output at {parse_dir} — run the parse stage first."
        )
    trees: list[dict[str, Any]] = []
    # rglob: NORA's per-cell layout nests parse output at
    # out/parse/<mno>/<rel>/*_tree.json (D-DRAFT-12); recursive find also
    # matches the legacy flat layout. Trees still carry mno/release, so the
    # adapter's (mno, release) partitioning + --multi-cell emission are
    # unchanged.
    for p in sorted(parse_dir.rglob("*_tree.json")):
        with open(p, "r", encoding="utf-8") as f:
            trees.append(json.load(f))
    return trees


# ── Multi-cell partitioning (multi-mno-sira) ───────────────────────
#
# Cell identity / naming / ordering live in `sandbox.sira_cells` — the
# single home shared by this adapter, the batch orchestrator, and the
# runtime query service. See multi-mno-sira D-DRAFT-3/5/6. Aliases kept
# for back-compat of this module's callers/tests.

from sandbox.sira_cells import (  # noqa: E402
    RELEASE_RE as _RELEASE_RE,
    cell_dirname as _cell_dirname,
    is_valid_release as _is_valid_release,
)


def _first_corpus_id(trees: list[dict[str, Any]]) -> str | None:
    """First non-empty req_id across a cell's trees — used as the target
    of the bm25-stage's dummy index-build qrel. None if no requirement
    rows exist."""
    for tree in trees:
        for req in tree.get("requirements") or []:
            rid = (req.get("req_id") or "").strip()
            if rid:
                return rid
    return None


def _partition_trees_by_cell(
    trees: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group trees by their (mno, release) cell key.

    Fail-loud (D-DRAFT-5): every tree's `release` must match the
    MMMYYYY convention. All violations are collected and reported
    together so the operator can fix every offending input dir in one
    pass, rather than discovering them one re-run at a time.

    Raises ValueError if any tree has a missing `mno` or a
    non-conforming `release`.
    """
    cells: dict[tuple[str, str], list[dict[str, Any]]] = {}
    violations: list[tuple[str, str, str]] = []
    for tree in trees:
        mno = (tree.get("mno") or "").strip()
        release = (tree.get("release") or "").strip()
        if not mno or not _is_valid_release(release):
            violations.append((mno, release, str(tree.get("plan_id") or "?")))
            continue
        cells.setdefault((mno, release), []).append(tree)
    if violations:
        lines = "\n".join(
            f"  - mno={m!r} release={r!r} plan_id={p!r}"
            for m, r, p in violations
        )
        raise ValueError(
            f"{len(violations)} tree(s) have a missing MNO or non-MMMYYYY "
            f"release (expected e.g. 'Feb2026'):\n{lines}\n"
            f"Release identity comes from the input directory name "
            f"<env_dir>/input/<MNO>/<MMMYYYY>/ (D-DRAFT-5) — rename the "
            f"offending input dir(s) and re-run extract -> parse."
        )
    return cells


def _hierarchy_path(req: dict[str, Any]) -> str:
    # Tree stores `hierarchy_path` as a list of strings (e.g. ["LTE OTA",
    # "Idle Mode", "T3402 timer"]). Joined with " > " for prose
    # readability; empty when the req sits at the doc root.
    h = req.get("hierarchy_path") or []
    return " > ".join(str(x) for x in h if x)


def _cross_refs_line(req: dict[str, Any]) -> str:
    xr = req.get("cross_references") or {}
    parts: list[str] = []
    for rid in (xr.get("internal") or [])[:8]:
        parts.append(str(rid))
    for plan in (xr.get("external_plans") or [])[:8]:
        parts.append(str(plan))
    for s in (xr.get("standards") or [])[:8]:
        spec = s.get("spec", "")
        sect = s.get("section", "")
        if spec and sect:
            parts.append(f"{spec} §{sect}")
        elif spec:
            parts.append(spec)
    return ", ".join(parts)


def _build_text(req: dict[str, Any], tree: dict[str, Any],
                defs_re: "re.Pattern | None",
                definitions_map: dict[str, str],
                section_index: dict[str, tuple[str, str]] | None = None) -> str:
    """Markdown body for one corpus row.

    `section_index` (`{section_number: (title, body)}`) lets the row carry its
    enclosing-section **context** (D-DRAFT-5), assembled here from the tree's
    section nodes per `tree.build_context` — so SIRA's corpus is self-contained
    while the parsed tree stays compact (context not duplicated there).
    """
    section_num = req.get("section_number") or ""
    title = req.get("title") or ""
    req_id = req.get("req_id") or ""
    # In leading-id mode prefer the requirement's own plan (D-DRAFT-1) — a
    # multi-plan document's reqs each carry their plan. In heading mode
    # (MNO-A) keep the original tree-level plan display, unchanged.
    if (tree.get("detection_mode") or "heading") == "leading_id_body":
        plan = (
            (req.get("plan_id") or "").strip()
            or tree.get("plan_name") or tree.get("plan_id") or ""
        )
    else:
        plan = tree.get("plan_name") or tree.get("plan_id") or ""
    hierarchy = _hierarchy_path(req)
    body = req.get("text") or ""
    if defs_re is not None and body:
        body = _expand_defs(body, defs_re, definitions_map)

    lines = [f"# {section_num} {title}".strip()]
    if req_id:
        lines.append(f"**req_id**: {req_id}")
    if plan:
        lines.append(f"**plan**: {plan}")
    if hierarchy:
        lines.append(f"**hierarchy**: {hierarchy}")
    ctx = build_context_string(
        req.get("parent_section") or "",
        section_index or {},
        tree.get("build_context") or "none",
        max_chars=int(tree.get("build_context_max_chars", 0) or 0),
    )
    if ctx:
        lines.append("")
        lines.append(ctx)
    if body:
        lines.append("")
        lines.append(body)
    # Tables are inlined into `req.text` at their document position by the parser
    # (faithful order), so `body` above already contains them — no separate
    # append here. (The parser's `Requirement.tables` field is kept as structured
    # metadata but is intentionally NOT re-serialized, to preserve ordering.)
    xref = _cross_refs_line(req)
    if xref:
        lines.append("")
        lines.append(f"**Cross-refs**: {xref}")
    return "\n".join(lines)


def _section_prefixes(section_number: str, max_depth: int) -> list[str]:
    """All ancestor section prefixes of `section_number` up to max_depth.

    >>> _section_prefixes("5.1.2.3", 2)
    ['5', '5.1']
    >>> _section_prefixes("5.1", 2)
    ['5', '5.1']
    >>> _section_prefixes("5", 2)
    ['5']
    """
    if not section_number:
        return []
    parts = [p for p in section_number.split(".") if p]
    out: list[str] = []
    for i in range(1, min(max_depth, len(parts)) + 1):
        out.append(".".join(parts[:i]))
    return out


def _section_sort_key(section_num: str) -> tuple[int, ...]:
    """Natural-sort key for section numbers. "5.10" → (5, 10),
    "5.2" → (5, 2); tuples sort so (5, 2) < (5, 10)."""
    out: list[int] = []
    for part in section_num.split("."):
        try:
            out.append(int(part))
        except ValueError:
            # Non-numeric segment (e.g. "5.A") — sort lexically as a
            # large tuple of char codes so it stays stable.
            out.extend(ord(c) for c in part)
    return tuple(out)


def _build_doc_row_text(
    plan_id: str, plan_name: str, req_ids: list[str],
    depth1_sections: list[tuple[str, str]],
) -> str:
    """Doc-level row body — short header + section-title list +
    req_id pointer list. The section titles widen the matching
    surface so concept-shaped queries (e.g. "WiFi calling") can
    bridge to the plan even when SIRA's query-side enrichment LLM
    misses the plan-name expansion (plan-aware-sira / D-DRAFT-10).

    BM25 length-normalization stays manageable because section titles
    are typically a few hundred bytes total (5-20 depth-1 sections);
    the req_id list remains the bulk of the body.

    `depth1_sections`: list of (section_number, section_title) for
    all sections at depth 1 within this plan.
    """
    display = plan_name or plan_id
    lines = [
        f"# {display} plan",
        f"**plan**: {plan_id}" + (f" / {plan_name}" if plan_name else ""),
    ]
    if depth1_sections:
        lines.append("")
        lines.append("**sections**:")
        for num, title in depth1_sections:
            lines.append(f"  - {num} {title}".rstrip())
    lines.extend([
        "",
        f"Contains {len(req_ids)} requirements:",
        " ".join(req_ids),
    ])
    return "\n".join(lines)


def _build_section_row_text(
    plan_id: str, plan_name: str, section_num: str,
    section_title: str, req_ids: list[str],
    subsection_titles: list[tuple[str, str]],
) -> str:
    """Section-level row body — header + subsection-title list +
    req_id pointer list. Subsection titles serve the same vocabulary-
    bridging role as section titles in the doc-level row.

    `subsection_titles`: list of (section_number, section_title) for
    immediate child sections (one level deeper). May be empty for
    leaf-most section rows.
    """
    lines = [
        f"# {section_num} {section_title}".strip(),
        f"**plan**: {plan_id}" + (f" / {plan_name}" if plan_name else ""),
        f"**section**: {section_num}",
    ]
    if subsection_titles:
        lines.append("")
        lines.append("**subsections**:")
        for num, title in subsection_titles:
            lines.append(f"  - {num} {title}".rstrip())
    lines.extend([
        "",
        f"Contains {len(req_ids)} requirements:",
        " ".join(req_ids),
    ])
    return "\n".join(lines)


def _emit_multigranularity_rows(
    trees: list[dict[str, Any]],
    f_out,
    section_max_depth: int = 2,
) -> tuple[int, int]:
    """Emit doc-level (one per plan) + section-level (one per
    (plan, section-prefix) up to max_depth) rows alongside the
    per-requirement rows.

    Returns (n_doc_rows, n_section_rows). Per the plan-aware-sira
    strand (D-DRAFT-10): rows carry req_id pointer lists, not full
    content. Fan-out at retrieval time expands them into req-level
    chunks for the synthesizer.

    Row _id conventions:
      - `doc:<plan_id>`               — one per plan
      - `section:<plan_id>:<prefix>`  — one per (plan, section_prefix)

    Section title for `section:<plan_id>:<num>` comes from the section
    catalog — any node carrying that `section_number` (a heading-anchored
    requirement in the MNO-A model, or a structural heading in the
    leading-id model — D-DRAFT-2); falls back to "Section <num>" when no
    catalog entry exists.

    Plans are grouped **per requirement** (D-DRAFT-1): a single source
    document may carry multiple plans (one PDF, sections-as-plans), so each
    distinct `req.plan_id` yields its own `doc:`/`section:` rows. For
    single-plan documents every req shares the tree's plan, so this collapses
    to one `doc:` row per document as before. A requirement's "home" section
    is its own `section_number` when it has one (heading-anchored), else its
    `parent_section` (table-anchored or leading-id reqs — which have none).
    """
    n_doc = 0
    n_section = 0
    for tree in trees:
        reqs = tree.get("requirements") or []
        tree_plan_id = (tree.get("plan_id") or "").strip()
        tree_plan_name = (tree.get("plan_name") or "").strip()
        # Per-req plan grouping + parent_section-based section derivation apply
        # ONLY to leading-id corpora (D-DRAFT-1/2). "heading" corpora (MNO-A)
        # keep their original behavior: one plan per document, sections keyed
        # off each req's own section_number.
        leading = (tree.get("detection_mode") or "heading") == "leading_id_body"

        # Section-title catalog: every node carrying a section_number, across
        # both detection models (MNO-A heading-requirements AND leading-id
        # structural headings). Independent of req_id so id-less headings
        # still supply titles.
        section_title_by_num: dict[str, str] = {}
        for req in reqs:
            sec_num = (req.get("section_number") or "").strip()
            if sec_num:
                section_title_by_num[sec_num] = (req.get("title") or "").strip()

        # Partition retrievable requirements (those with a req_id) by plan —
        # per-req plan in leading-id mode, else the single document plan.
        plan_members: dict[str, list[dict[str, Any]]] = {}
        for req in reqs:
            rid = (req.get("req_id") or "").strip()
            if not rid:
                continue
            plan = (
                ((req.get("plan_id") or "").strip() or tree_plan_id)
                if leading else tree_plan_id
            )
            plan_members.setdefault(plan, []).append(req)

        for plan_id, members in plan_members.items():
            if not plan_id:
                continue  # no plan → can't construct stable row IDs
            # Only the primary plan inherits the tree's plan_name; other
            # plans in a multi-plan document show just their id.
            plan_name = tree_plan_name if plan_id == tree_plan_id else ""

            all_req_ids: list[str] = []
            section_to_descendants: dict[str, list[str]] = {}
            for req in members:
                rid = (req.get("req_id") or "").strip()
                all_req_ids.append(rid)
                # A requirement's "home" section: its own section_number
                # (heading-anchored), or — only in leading-id mode — its
                # parent_section. In heading mode a req without its own
                # section_number (e.g. MNO-A table-anchored) contributes to
                # no section row, exactly as before.
                home = (req.get("section_number") or "").strip()
                if not home and leading:
                    home = (req.get("parent_section") or "").strip()
                for prefix in _section_prefixes(home, section_max_depth):
                    section_to_descendants.setdefault(prefix, []).append(rid)

            if not all_req_ids:
                continue

            # Section anchors restricted to the sections this plan touches.
            section_anchors: dict[str, str] = {
                num: section_title_by_num.get(num, "")
                for num in section_to_descendants
            }
            # Collect immediate-child sections per parent for the title-
            # augmentation pass (depth-1 sections feed the doc-level row;
            # "5.1.*" feed the "5.1" section row).
            depth1_sections: list[tuple[str, str]] = []
            children_of_section: dict[str, list[tuple[str, str]]] = {}
            for sec_num, title in section_anchors.items():
                if "." not in sec_num:
                    depth1_sections.append((sec_num, title))
                else:
                    parent = sec_num[: sec_num.rfind(".")]
                    children_of_section.setdefault(parent, []).append((sec_num, title))
            depth1_sections.sort(key=lambda p: _section_sort_key(p[0]))
            for k in children_of_section:
                children_of_section[k].sort(key=lambda p: _section_sort_key(p[0]))

            # Doc-level row
            f_out.write(json.dumps({
                "_id": f"doc:{plan_id}",
                "title": f"{plan_name or plan_id} plan",
                "text": _build_doc_row_text(
                    plan_id, plan_name, all_req_ids, depth1_sections,
                ),
            }, ensure_ascii=False) + "\n")
            n_doc += 1

            # Section-level rows
            for prefix in sorted(
                section_to_descendants.keys(), key=_section_sort_key,
            ):
                descendants = section_to_descendants[prefix]
                section_title = section_anchors.get(prefix) or f"Section {prefix}"
                subsection_titles = children_of_section.get(prefix, [])
                f_out.write(json.dumps({
                    "_id": f"section:{plan_id}:{prefix}",
                    "title": f"{prefix} {section_title}".strip(),
                    "text": _build_section_row_text(
                        plan_id, plan_name, prefix, section_title, descendants,
                        subsection_titles,
                    ),
                }, ensure_ascii=False) + "\n")
                n_section += 1

    return n_doc, n_section


def _emit_corpus(
    trees: list[dict[str, Any]],
    out_path: Path,
    section_max_depth: int = 2,
) -> tuple[int, int, int]:
    """Emit corpus.jsonl with per-req + doc-level + section-level rows.

    Returns (n_req_rows, n_doc_rows, n_section_rows).
    """
    seen_ids: set[str] = set()
    written = 0
    skipped_no_id = 0
    skipped_dup = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        # Per-requirement rows (existing behavior, unchanged shape)
        for tree in trees:
            definitions_map = tree.get("definitions_map") or {}
            defs_re = _compile_defs(definitions_map) if definitions_map else None
            leading = (tree.get("detection_mode") or "heading") == "leading_id_body"
            # {section_number: (title, body)} from this tree's section nodes —
            # the source for per-req Context (D-DRAFT-5). Built once per tree.
            section_index = {
                s["section_number"]: (s.get("title") or "", s.get("text") or "")
                for s in (tree.get("requirements") or [])
                if (s.get("section_number") or "")
            }
            for req in tree.get("requirements") or []:
                req_id = (req.get("req_id") or "").strip()
                if not req_id:
                    skipped_no_id += 1
                    continue
                if req_id in seen_ids:
                    # Same req_id reported by two trees: keep first.
                    # Cross-doc dedup avoids the corpus polluting itself
                    # with parser-stage hierarchy duplicates.
                    skipped_dup += 1
                    continue
                seen_ids.add(req_id)
                title = (
                    f"{(req.get('section_number') or '').strip()} "
                    f"{(req.get('title') or '').strip()}"
                ).strip()
                if not title and leading:
                    # Leading-id reqs (D-DRAFT-2) carry no section_number/title
                    # of their own; surface their section context from
                    # hierarchy_path so the corpus row isn't title-less. Only in
                    # leading-id mode — heading-mode (MNO-A) rows keep their
                    # original (possibly empty) title.
                    hp = req.get("hierarchy_path") or []
                    title = " > ".join(str(x) for x in hp if x)
                row = {
                    "_id": req_id,
                    "title": title,
                    "text": _build_text(req, tree, defs_re, definitions_map, section_index),
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
        # Doc-level + section-level rows (plan-aware-sira / D-DRAFT-10)
        n_doc, n_section = _emit_multigranularity_rows(
            trees, f, section_max_depth=section_max_depth,
        )
    print(f"  corpus.jsonl: wrote {written} per-req rows "
          f"(skipped: {skipped_no_id} no-id, {skipped_dup} duplicate)")
    print(f"  corpus.jsonl: wrote {n_doc} doc-level rows "
          f"+ {n_section} section-level rows "
          f"(section_max_depth={section_max_depth})")
    return written, n_doc, n_section


def _check_stale_downstream(
    out_dir: Path, wipe_index: bool, wipe_all: bool,
) -> None:
    """Detect (and optionally wipe) SIRA-derived artifacts that are now
    stale because the corpus just changed.

    When the corpus row-count changes (e.g., this adapter adds doc/
    section rows, or a new release/MNO is ingested), the pre-existing
    BM25 `index/` no longer matches the corpus. SIRA does NOT detect
    this — it reuses the cached index and then panics in enrich_corpus
    with "doc_id N out of range (num_docs=N)".

    Two wipe scopes, because the right answer differs by scenario:

    - `index/` + `enrichments/` are corpus-SIZE-dependent and cheap to
      regenerate (eval_bm25 rebuilds the index in seconds-minutes).
      These MUST be wiped on any corpus-size change.
    - `runs/` holds the doc-enrichment CACHE — the trace files SIRA's
      resume reads to skip already-enriched docs, AND the basis for
      incremental (content-hash-aware) enrichment. KEEP it for
      incremental growth; only wipe it for a deliberate full rebuild
      (changed enrichment prompt, or major composition shift that
      warrants re-baselining the DF-filter).

    wipe_index → wipe index/ + enrichments/ + eval/ + retrieval/
                 (incremental-safe: keeps runs/ enrichment cache).
    wipe_all   → also wipe runs/ (full rebuild — re-enriches everything).
    Neither    → warn only.
    """
    import shutil

    # All of these are derived from the corpus and become stale on any
    # corpus-size change. eval/ and retrieval/ matter specifically because
    # the bm25 baseline stage short-circuits on a cached eval/baseline/*.json
    # (eval_bm25._build_and_evaluate_one) — leaving them behind makes the
    # index rebuild silently skip, then the index/best symlink fails with
    # FileNotFoundError. None of these hold LLM enrichment work (that's runs/).
    size_dependent = [d for d in ("index", "enrichments", "eval", "retrieval")
                      if (out_dir / d).is_dir()]
    cache_dir_present = (out_dir / "runs").is_dir()

    if not size_dependent and not cache_dir_present:
        return

    if wipe_all:
        for d in ("index", "enrichments", "eval", "retrieval", "runs"):
            if (out_dir / d).is_dir():
                shutil.rmtree(out_dir / d)
        print()
        print("  wiped ALL SIRA-derived dirs (full rebuild): "
              "index, enrichments, eval, retrieval, runs")
        print("  → next pipeline run re-enriches every doc from scratch.")
        return

    if wipe_index:
        for d in size_dependent:
            shutil.rmtree(out_dir / d)
        print()
        print(f"  wiped size-dependent dirs: {', '.join(size_dependent) or '(none)'}")
        if cache_dir_present:
            print("  KEPT runs/ (enrichment cache) — incremental enrich will "
                  "reuse it; only new/changed docs get LLM-enriched.")
        print("  → next pipeline run rebuilds the index; enrich resumes "
              "from the cache.")
        return

    # Warn-only
    print()
    print("  " + "!" * 68)
    print(f"  WARNING: stale SIRA-derived dirs present: "
          f"{', '.join(size_dependent + (['runs'] if cache_dir_present else []))}")
    print("  The corpus changed. SIRA will reuse the cached index/ and "
          "then PANIC")
    print("  in enrich_corpus with 'doc_id N out of range'. Before "
          "re-running:")
    print("    • incremental (added release/MNO, same prompts):")
    print("        re-run this adapter with --wipe-stale-index  "
          "(keeps runs/ cache)")
    print("    • full rebuild (changed enrich prompt / major composition shift):")
    print("        re-run this adapter with --wipe-all-derived")
    print("  " + "!" * 68)


def _emit_queries_and_qrels(
    raw_dir: Path, split: str = "test",
) -> tuple[int, int, int]:
    """Returns (n_queries, n_qrels, n_queries_with_no_qrels)."""
    # Lazy import — the eval module pulls in heavy deps at top.
    from core.src.eval import questions as eval_q

    all_questions = [
        v for k, v in vars(eval_q).items()
        if k.startswith("Q_") and isinstance(v, eval_q.EvalQuestion)
    ]
    all_questions.sort(key=lambda q: q.id)

    queries_path = raw_dir / f"queries-{split}.jsonl"
    qrels_path = raw_dir / f"qrels-{split}.jsonl"

    n_qrels = 0
    n_queries = 0
    n_no_qrels = 0
    with open(queries_path, "w", encoding="utf-8") as qf, \
         open(qrels_path, "w", encoding="utf-8") as rf:
        for q in all_questions:
            qf.write(json.dumps({"_id": q.id, "text": q.question},
                                ensure_ascii=False) + "\n")
            n_queries += 1
            req_ids = q.ground_truth.expected_req_ids or []
            if not req_ids:
                n_no_qrels += 1
                continue
            for rid in req_ids:
                rf.write(json.dumps(
                    {"query-id": q.id, "corpus-id": rid, "score": 1}
                ) + "\n")
                n_qrels += 1
    print(f"  queries-{split}.jsonl: wrote {n_queries} queries "
          f"({n_no_qrels} have no expected_req_ids → not in qrels)")
    print(f"  qrels-{split}.jsonl: wrote {n_qrels} qrel rows")
    return n_queries, n_qrels, n_no_qrels


def _emit_metadata(raw_dir: Path, name: str, num_corpus: int,
                   n_queries: int, n_qrels: int, split: str = "test") -> None:
    """Write metadata.json mirroring `prepare_mteb_data.py`'s shape.

    SIRA's `prepare_mteb_data.py` early-returns when this file exists,
    so the rest of the pipeline runs against our local data without
    triggering a HuggingFace download.
    """
    metadata = {
        "name": name,
        "source": "nora-local-adapter",
        "num_corpus": num_corpus,
        "splits": {
            split: {"num_queries": n_queries, "num_qrels": n_qrels},
        },
    }
    with open(raw_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)


def _emit_multi_cell(
    trees: list[dict[str, Any]],
    db_root: Path,
    section_max_depth: int,
    wipe_index: bool,
    wipe_all: bool,
) -> list[str]:
    """Multi-MNO mode: partition trees into (mno, release) cells and emit
    one corpus-only BEIR dataset per cell at `<db_root>/<mno>__<release>/raw/`.

    Returns the list of cell dir names (usable as SIRA `data.name`).

    Corpus-only (OQ-2): each cell gets corpus.jsonl + metadata.json, plus
    empty queries-test.jsonl / qrels-test.jsonl so SIRA's split-aware
    file-existence checks don't trip. Real eval data lands when on-prem
    qrels exist. The runtime service (the actual consumer) reads only
    corpus + index + enrichments. See multi-mno-sira D-DRAFT-6.
    """
    cells = _partition_trees_by_cell(trees)
    print(f"multi-cell mode: {len(cells)} cell(s) from {len(trees)} tree(s)")
    print()
    names: list[str] = []
    for cell_key in sorted(cells):
        dirname = _cell_dirname(cell_key)
        raw = db_root / dirname / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        print(f"cell {dirname}: emitting raw/corpus.jsonl ...")
        n_req, n_doc, n_section = _emit_corpus(
            cells[cell_key], raw / "corpus.jsonl",
            section_max_depth=section_max_depth,
        )
        num_corpus = n_req + n_doc + n_section
        # Eval scaffolding — a single dummy query + qrel, NOT the
        # single-MNO 18-Q set. SIRA's bm25 stage (eval_bm25) reads
        # queries/qrels, searches them, and picks the best index by
        # Recall@K — that's what creates the index/best symlink the
        # runtime service loads. With ZERO queries that pick-best step
        # has nothing to evaluate and the symlink isn't created. One
        # dummy query (pointed at the cell's first corpus row) keeps the
        # bm25 stage's eval+pick-best alive so index/best is produced.
        # The dummy metric is meaningless and unused — the runtime
        # service reads the index, never the eval numbers. Real eval
        # ground truth is deferred (OQ-2).
        first_id = _first_corpus_id(cells[cell_key])
        if first_id:
            (raw / "queries-test.jsonl").write_text(
                json.dumps({"_id": "_idxbuild_0",
                            "text": "index build placeholder"}) + "\n",
                encoding="utf-8",
            )
            (raw / "qrels-test.jsonl").write_text(
                json.dumps({"query-id": "_idxbuild_0",
                            "corpus-id": first_id, "score": 1}) + "\n",
                encoding="utf-8",
            )
        else:
            # Cell has no requirement rows at all — empty scaffolding.
            (raw / "queries-test.jsonl").write_text("", encoding="utf-8")
            (raw / "qrels-test.jsonl").write_text("", encoding="utf-8")
        _emit_metadata(raw, name=dirname, num_corpus=num_corpus,
                       n_queries=0, n_qrels=0)
        _check_stale_downstream(
            db_root / dirname, wipe_index=wipe_index, wipe_all=wipe_all,
        )
        names.append(dirname)
        print(f"  -> {num_corpus} corpus rows; data.name={dirname}")
        print()
    return names


def main() -> int:
    p = argparse.ArgumentParser(
        prog="nora_to_beir",
        description=(
            "Convert NORA parse output + 18-Q eval set into BEIR-format "
            "corpus.jsonl + queries.jsonl + qrels/test.tsv for the SIRA "
            "standalone sandbox."
        ),
    )
    p.add_argument("--env-dir", required=True, type=Path,
                   help="NORA env dir containing out/parse/*_tree.json")
    p.add_argument("--output", required=True, type=Path,
                   help=(
                       "Output dir for the SIRA-internal layout. The "
                       "dir's basename becomes the dataset 'name' SIRA "
                       "uses (override with --name). raw/ subdir holds "
                       "corpus.jsonl, queries-test.jsonl, "
                       "qrels-test.jsonl, metadata.json."
                   ))
    p.add_argument("--name", default=None,
                   help="Dataset name in metadata.json. Defaults to "
                        "the basename of --output. Ignored in "
                        "--multi-cell mode (each cell's name is its "
                        "<mno>__<release> dir).")
    p.add_argument("--multi-cell", action="store_true",
                   help=(
                       "Multi-MNO mode (multi-mno-sira). Partition trees "
                       "by (mno, release) and emit one corpus-only BEIR "
                       "dataset per cell at <output>/<mno>__<release>/raw/. "
                       "In this mode --output is the db_root (parent of "
                       "all cells), not a single dataset dir. Release must "
                       "match the MMMYYYY convention (e.g. Feb2026) or the "
                       "run fails loud. queries/qrels are NOT emitted "
                       "(eval ground truth deferred — OQ-2)."
                   ))
    p.add_argument("--section-max-depth", type=int, default=2,
                   help=(
                       "Max section-prefix depth for section-level "
                       "multi-granularity rows. 2 = emit `section:<plan>:5` "
                       "and `section:<plan>:5.1` but not `5.1.1`. Default "
                       "2 (matches plan-aware-sira D-DRAFT-10 default)."
                   ))
    p.add_argument("--wipe-stale-index", action="store_true",
                   help=(
                       "Incremental-safe wipe: remove size-dependent "
                       "index/ + enrichments/ under --output (cheap to "
                       "rebuild) but KEEP runs/ (the enrichment cache), so "
                       "the next SIRA run re-enriches only new/changed docs. "
                       "Use for steady-state growth (added release / MNO, "
                       "same enrichment prompts)."
                   ))
    p.add_argument("--wipe-all-derived", action="store_true",
                   help=(
                       "Full-rebuild wipe: remove index/ + enrichments/ + "
                       "runs/ — re-enriches every doc from scratch. Use "
                       "only when the enrichment prompt changed or the "
                       "corpus composition shifted enough to re-baseline "
                       "the DF-filter (e.g., 1 MNO -> 3 MNOs)."
                   ))
    args = p.parse_args()
    if args.name is None:
        args.name = args.output.name

    env_dir: Path = args.env_dir
    out_dir: Path = args.output
    raw_dir = out_dir / "raw"

    print(f"# nora_to_beir")
    print(f"env_dir: {env_dir}")
    print(f"output:  {out_dir}  (SIRA-internal layout under raw/)")
    print()

    trees = _load_trees(env_dir)
    print(f"loaded {len(trees)} _tree.json file(s) from out/parse/")
    print()

    if args.multi_cell:
        # --output is the db_root (parent of all cells), not a dataset dir.
        names = _emit_multi_cell(
            trees, out_dir,
            section_max_depth=args.section_max_depth,
            wipe_index=args.wipe_stale_index,
            wipe_all=args.wipe_all_derived,
        )
        print(f"done — point SIRA at db_root={out_dir}")
        print(f"       cells (use as data.name): {', '.join(names)}")
        return 0

    raw_dir.mkdir(parents=True, exist_ok=True)
    print("emitting raw/corpus.jsonl ...")
    n_req, n_doc, n_section = _emit_corpus(
        trees, raw_dir / "corpus.jsonl",
        section_max_depth=args.section_max_depth,
    )
    num_corpus = n_req + n_doc + n_section
    print()
    print("emitting raw/queries-test.jsonl + raw/qrels-test.jsonl ...")
    n_queries, n_qrels, _ = _emit_queries_and_qrels(raw_dir)
    print()
    print("emitting raw/metadata.json ...")
    _emit_metadata(raw_dir, name=args.name, num_corpus=num_corpus,
                   n_queries=n_queries, n_qrels=n_qrels)
    print()
    # Staleness guard — the corpus just changed; pre-existing
    # index/ + enrichments/ no longer match it. runs/ (enrichment
    # cache) is kept unless --wipe-all-derived.
    _check_stale_downstream(
        out_dir,
        wipe_index=args.wipe_stale_index,
        wipe_all=args.wipe_all_derived,
    )
    print()
    print(f"done — point SIRA at db_root={out_dir.parent}, data.name={args.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
