"""verify_req_recall — diff req-ids visible at extract time against the ids
that made it into parse trees as requirements.

Users reported requirements clearly present in source documents that never
surface downstream. This checker mechanizes the first diagnostic step:

  * scan each cell's extract IRs (``out/extract/<mno>/<rel>/*_ir.json``) for
    every match of the cell profile's ``requirement_id.pattern``,
  * compare against the ids present in the matching parse trees
    (``out/parse/<mno>/<rel>/*_tree.json``),
  * bucket the differences so document inspection starts from a short list.

Buckets (per doc and per cell):

  * ``MISSING``    — id seen in a non-struck heading/paragraph block but absent
                     from the tree entirely. The prime inspection list: each is
                     either a recognition gap or a bare cross-reference in body
                     prose (the checker cannot tell — a human or the profile's
                     cross-reference patterns must classify).
  * ``demoted``    — id present in the tree but on a node with
                     ``is_requirement: false`` (recognized, not a requirement).
  * ``table-only`` — id seen only inside TABLE blocks and absent from the tree.
                     Reported separately: table-cell ids with no paragraph
                     anchor are treated as cross-references by design (D-027),
                     so these are informational, not failures.
  * ``struck-only``— id seen only in struck blocks and absent from the tree.
                     Expected when the profile drops struck content. A block
                     is struck when EITHER the block-level flag OR
                     ``font_info.strikethrough`` is set (msg 0012: DOCX
                     revision strikes surface only at font level).
  * ``toc-only``   — id whose only live occurrence is a TOC entry line
                     (matches the profile's ``toc_detection.entry_pattern``).
                     Usually the surviving echo of a struck requirement —
                     never a body occurrence.
  * ``section-id`` — id matched ``pattern`` but failed the narrower
                     ``requirement_type_pattern`` (structural section ids).
                     Counted, never listed as missing.

A tree file missing for an existing IR is itself reported — the document never
parsed at all.

Usage:
    python -m sandbox.verify_req_recall --extract <env_dir>/out/extract \
        --parse <env_dir>/out/parse [--profile-root <env_dir>/out/profile] \
        [--show 20] [--strict]

Exit codes: 0 ok/informational · 1 MISSING>0 or unparsed docs (only with
``--strict``) · 2 usage. No proprietary content is embedded here; all
patterns come from the materialized cell profiles at runtime.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path


def load_id_config(profile_path: Path) -> dict:
    """Pull the requirement_id block from a materialized cell profile,
    plus the TOC entry pattern (used to classify TOC echo lines as
    non-body). Returns {} when the file or the pattern is absent (cell
    not checkable)."""
    try:
        prof = json.load(open(profile_path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rid = dict(prof.get("requirement_id") or {})
    if not rid.get("pattern"):
        return {}
    toc = prof.get("toc_detection") or {}
    if isinstance(toc, dict) and toc.get("entry_pattern"):
        rid["_toc_entry_pattern"] = toc["entry_pattern"]
    return rid


def _normalize(token: str, mode: str) -> str:
    return token.upper() if mode == "upper" else token


def scan_ir(ir: dict, id_cfg: dict) -> dict:
    """Scan one IR's blocks for req-id candidates.

    Returns {"body": {id: (page, index, block_type)}, "table": set,
    "struck": set, "toc": set, "section_id": set} — "body" keeps the
    first-seen location of ids found in live heading/paragraph text (the
    ones parse is expected to pick up).

    Liveness (strand req-recall, msg 0012): a block is struck when EITHER
    the block-level ``struck`` flag OR ``font_info.strikethrough`` is set
    — DOCX revision strikes surface only at font level, and the parser's
    strike machinery honors both. TOC echo lines (blocks matching the
    profile's ``toc_detection.entry_pattern``) bucket as ``toc``, not
    body — a struck requirement's id survives in its live TOC entry, and
    counting that as a body occurrence inflated MISSING ~40x on the
    strike-heavy corpus."""
    pat = re.compile(id_cfg["pattern"])
    type_pat = (re.compile(id_cfg["requirement_type_pattern"])
                if id_cfg.get("requirement_type_pattern") else None)
    toc_pat = (re.compile(id_cfg["_toc_entry_pattern"])
               if id_cfg.get("_toc_entry_pattern") else None)
    norm = id_cfg.get("normalize", "none")

    body: dict[str, tuple] = {}
    table: set[str] = set()
    struck: set[str] = set()
    toc: set[str] = set()
    section_id: set[str] = set()

    def classify(text: str, bucket_add) -> None:
        for m in pat.finditer(text or ""):
            rid = _normalize(m.group(0), norm)
            if type_pat and not type_pat.fullmatch(rid):
                section_id.add(rid)
                continue
            bucket_add(rid)

    for b in ir.get("content_blocks", []):
        btype = b.get("type", "")
        pos = b.get("position") or {}
        loc = (pos.get("page"), pos.get("index"), btype)
        font = b.get("font_info") or {}
        if b.get("struck") or font.get("strikethrough"):
            classify(b.get("text", ""), struck.add)
            continue
        if toc_pat is not None and btype != "table" \
                and toc_pat.search(b.get("text") or ""):
            classify(b.get("text", ""), toc.add)
            continue
        if btype == "table":
            cells = list(b.get("headers") or [])
            for row in b.get("rows") or []:
                cells.extend(row)
            for cell in cells:
                classify(cell, table.add)
            # a table block can also carry flat text (layout providers)
            classify(b.get("text", ""), table.add)
            continue
        classify(b.get("text", ""),
                 lambda rid, loc=loc: body.setdefault(rid, loc))
    return {"body": body, "table": table, "struck": struck, "toc": toc,
            "section_id": section_id}


def scan_tree(tree: dict) -> tuple[set[str], set[str]]:
    """Return (all ids in the tree, ids on requirement nodes). A node without
    the ``is_requirement`` key counts as a requirement when it has a req_id
    (historical back-compat semantics)."""
    all_ids: set[str] = set()
    req_ids: set[str] = set()
    for r in tree.get("requirements", []):
        rid = (r.get("req_id") or "").strip()
        if not rid:
            continue
        all_ids.add(rid)
        if r.get("is_requirement", True):
            req_ids.add(rid)
    return all_ids, req_ids


def check_doc(ir_path: Path, tree_path: Path, id_cfg: dict) -> dict:
    """Diff one document. Returns per-doc buckets; ``unparsed`` True when the
    tree file is absent."""
    ir = json.load(open(ir_path, encoding="utf-8"))
    found = scan_ir(ir, id_cfg)
    if not tree_path.exists():
        return {"unparsed": True, "found": found, "missing": {},
                "demoted": set(), "table_only": set(), "struck_only": set(),
                "toc_only": set()}
    tree = json.load(open(tree_path, encoding="utf-8"))
    all_ids, req_ids = scan_tree(tree)

    body = found["body"]
    missing = {rid: loc for rid, loc in body.items() if rid not in all_ids}
    demoted = {rid for rid in body if rid in all_ids and rid not in req_ids}
    table_only = {rid for rid in found["table"]
                  if rid not in body and rid not in all_ids}
    struck_only = {rid for rid in found["struck"]
                   if rid not in body and rid not in found["table"]
                   and rid not in all_ids}
    # TOC echo without any other live occurrence: usually the surviving
    # TOC entry of a struck (correctly dropped) requirement.
    toc_only = {rid for rid in found["toc"]
                if rid not in body and rid not in found["table"]
                and rid not in found["struck"] and rid not in all_ids}
    return {"unparsed": False, "found": found, "missing": missing,
            "demoted": demoted, "table_only": table_only,
            "struck_only": struck_only, "toc_only": toc_only,
            "tree_reqs": len(req_ids)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="verify_req_recall",
        description="Diff extract-visible req-ids against parse-tree requirements.",
    )
    ap.add_argument("--extract", required=True,
                    help="NORA extract dir (<env_dir>/out/extract)")
    ap.add_argument("--parse", required=True,
                    help="NORA parse dir (<env_dir>/out/parse)")
    ap.add_argument("--profile-root", default=None,
                    help="Materialized profiles root (default: <extract>/../profile)")
    ap.add_argument("--show", type=int, default=20,
                    help="Max MISSING ids listed per doc (default 20)")
    ap.add_argument("--strict", action="store_true",
                    help="Exit 1 when any doc has MISSING ids or is unparsed")
    args = ap.parse_args(argv)

    extract_dir = Path(args.extract)
    parse_dir = Path(args.parse)
    if not extract_dir.is_dir() or not parse_dir.is_dir():
        print("error: --extract and --parse must be existing directories",
              file=sys.stderr)
        return 2
    profile_root = (Path(args.profile_root) if args.profile_root
                    else extract_dir.parent / "profile")

    grand = {"docs": 0, "unparsed": 0, "missing": 0, "demoted": 0,
             "table_only": 0, "struck_only": 0, "toc_only": 0,
             "section_id": 0}
    failing = False

    for cell_dir in sorted(p for p in glob.glob(str(extract_dir / "*" / "*"))
                           if Path(p).is_dir()):
        cell = Path(cell_dir)
        key = "/".join(cell.parts[-2:])   # mno/rel
        id_cfg = load_id_config(profile_root / cell.parts[-2] / cell.parts[-1]
                                / "profile.json")
        if not id_cfg:
            print(f"== {key} == [skip] no materialized profile / empty "
                  f"requirement_id.pattern")
            continue
        print(f"== {key} ==")
        for ir_path in sorted(cell.glob("*_ir.json")):
            stem = ir_path.stem.replace("_ir", "")
            tree_path = (parse_dir / cell.parts[-2] / cell.parts[-1]
                         / f"{stem}_tree.json")
            res = check_doc(ir_path, tree_path, id_cfg)
            grand["docs"] += 1
            f = res["found"]
            grand["section_id"] += len(f["section_id"])
            if res["unparsed"]:
                grand["unparsed"] += 1
                failing = True
                print(f"  {stem}: [UNPARSED] no tree file "
                      f"({len(f['body'])} body-candidate id(s) in extract)")
                continue
            n_miss = len(res["missing"])
            grand["missing"] += n_miss
            grand["demoted"] += len(res["demoted"])
            grand["table_only"] += len(res["table_only"])
            grand["struck_only"] += len(res["struck_only"])
            grand["toc_only"] += len(res["toc_only"])
            line = (f"  {stem}: body_ids={len(f['body'])} "
                    f"tree_reqs={res['tree_reqs']} MISSING={n_miss} "
                    f"demoted={len(res['demoted'])} "
                    f"table_only={len(res['table_only'])} "
                    f"struck_only={len(res['struck_only'])} "
                    f"toc_only={len(res['toc_only'])}")
            print(line)
            if n_miss:
                failing = True
                for rid, (page, idx, btype) in sorted(
                        res["missing"].items())[: args.show]:
                    print(f"      MISSING {rid}  (page={page} block={idx} "
                          f"type={btype})")
                if n_miss > args.show:
                    print(f"      ... {n_miss - args.show} more "
                          f"(raise --show)")

    print(f"\nTOTAL: docs={grand['docs']} unparsed={grand['unparsed']} "
          f"MISSING={grand['missing']} demoted={grand['demoted']} "
          f"table_only={grand['table_only']} "
          f"struck_only={grand['struck_only']} "
          f"toc_only={grand['toc_only']} "
          f"section_ids={grand['section_id']}")
    if grand["missing"] or grand["unparsed"]:
        print("[note] MISSING ids need document inspection: recognition gap "
              "vs bare body cross-reference. table_only/struck_only are "
              "informational by design.")
    return 1 if (args.strict and failing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
