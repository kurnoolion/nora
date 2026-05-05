"""CLI for resolve-log review: generate templates and compact chat reports.

Usage:
  python -m core.src.resolver.resolve_review_cli create <doc_id>_xrefs.json
  python -m core.src.resolver.resolve_review_cli create-all <resolve_dir>
  python -m core.src.resolver.resolve_review_cli report <doc_id>_resolve_review.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.src.resolver.resolve_review import generate_compact_report, generate_template


def cmd_create(args: argparse.Namespace) -> int:
    xrefs_path = Path(args.xrefs)
    if not xrefs_path.exists():
        print(f"RSV-E001: xrefs file not found: {xrefs_path}", file=sys.stderr)
        return 1
    out_path = Path(args.output) if args.output else (
        xrefs_path.parent / (xrefs_path.stem.replace("_xrefs", "") + "_resolve_review.json")
    )
    if out_path.exists() and not args.force:
        print(f"RSV-W001: review file already exists: {out_path}\nUse --force to overwrite.",
              file=sys.stderr)
        return 1
    template = generate_template(xrefs_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Created: {out_path}")
    print(f"Then run: python -m core.src.resolver.resolve_review_cli report {out_path}")
    return 0


def cmd_create_all(args: argparse.Namespace) -> int:
    d = Path(args.resolve_dir)
    if not d.is_dir():
        print(f"RSV-E001: directory not found: {d}", file=sys.stderr)
        return 1
    files = sorted(d.glob("*_xrefs.json"))
    if not files:
        print(f"RSV-W001: no *_xrefs.json files in {d}", file=sys.stderr)
        return 0
    created = skipped = 0
    for p in files:
        out = d / (p.stem.replace("_xrefs", "") + "_resolve_review.json")
        if out.exists() and not args.force:
            print(f"  skip  {out.name}")
            skipped += 1
            continue
        out.write_text(json.dumps(generate_template(p), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  created  {out.name}")
        created += 1
    print(f"\n{created} created, {skipped} skipped.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    review_path = Path(args.review)
    if not review_path.exists():
        print(f"RSV-E001: review file not found: {review_path}", file=sys.stderr)
        return 1
    xrefs_path = Path(args.xrefs) if getattr(args, "xrefs", None) else None
    print(generate_compact_report(review_path, xrefs_path=xrefs_path))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="resolve_review_cli",
        description="Resolve-log review: generate templates and compact chat reports.",
    )
    sub = parser.add_subparsers(dest="command")

    p_create = sub.add_parser("create", help="Generate review template from an xrefs file.")
    p_create.add_argument("xrefs", metavar="XREFS_JSON")
    p_create.add_argument("--output", "-o", default=None)
    p_create.add_argument("--force", action="store_true")

    p_create_all = sub.add_parser("create-all", help="Batch generate templates.")
    p_create_all.add_argument("resolve_dir", metavar="RESOLVE_DIR")
    p_create_all.add_argument("--force", action="store_true")

    p_report = sub.add_parser("report", help="Generate compact RES-CHK report.")
    p_report.add_argument("review", metavar="REVIEW_JSON")
    p_report.add_argument("--xrefs", default=None)

    args = parser.parse_args()
    if args.command == "create":
        sys.exit(cmd_create(args))
    elif args.command == "create-all":
        sys.exit(cmd_create_all(args))
    elif args.command == "report":
        sys.exit(cmd_report(args))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
