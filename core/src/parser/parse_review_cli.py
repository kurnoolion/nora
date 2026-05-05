"""CLI for parse-log review: generate templates and compact chat reports.

Usage:
  # Step 1 — generate review template (pre-populated with parser data):
  python -m core.src.parser.parse_review_cli --create <env_dir>/reports/parse_log/LTEOTADM_parse_log.json

  # Step 2 — edit the template (fill in corrections only):
  #   vi <env_dir>/reports/parse_log/LTEOTADM_parse_review.json

  # Step 3 — generate compact report for pasting into chat:
  python -m core.src.parser.parse_review_cli --report <env_dir>/reports/parse_log/LTEOTADM_parse_review.json

  # Step 3 alternative — explicit log path if review and log are in different dirs:
  python -m core.src.parser.parse_review_cli \\
      --report LTEOTADM_parse_review.json \\
      --log <env_dir>/reports/parse_log/LTEOTADM_parse_log.json

  # Batch: generate templates for all logs in a directory
  python -m core.src.parser.parse_review_cli --create-all <env_dir>/reports/parse_log/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.src.parser.parse_review import generate_compact_report, generate_template


def cmd_create(args: argparse.Namespace) -> int:
    log_path = Path(args.log)
    if not log_path.exists():
        print(f"PRV-E001: parse log not found: {log_path}", file=sys.stderr)
        return 1

    out_path = args.output
    if out_path is None:
        stem = log_path.stem.replace("_parse_log", "")
        out_path = str(log_path.parent / f"{stem}_parse_review.json")

    out_file = Path(out_path)
    if out_file.exists() and not args.force:
        print(
            f"PRV-W001: review file already exists: {out_file}\n"
            f"Use --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    template = generate_template(log_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2, ensure_ascii=False)

    print(f"Created: {out_file}")
    print("Fill in reviewer/review_date/overall_verdict and add corrections.")
    print(f"Then run: python -m core.src.parser.parse_review_cli --report {out_file}")
    return 0


def cmd_create_all(args: argparse.Namespace) -> int:
    log_dir = Path(args.log_dir)
    if not log_dir.is_dir():
        print(f"PRV-E001: directory not found: {log_dir}", file=sys.stderr)
        return 1

    log_files = sorted(log_dir.glob("*_parse_log.json"))
    if not log_files:
        print(f"PRV-W001: no *_parse_log.json files in {log_dir}", file=sys.stderr)
        return 0

    created = 0
    skipped = 0
    for log_path in log_files:
        stem = log_path.stem.replace("_parse_log", "")
        out_file = log_dir / f"{stem}_parse_review.json"
        if out_file.exists() and not args.force:
            print(f"  skip  {out_file.name}  (already exists; use --force to overwrite)")
            skipped += 1
            continue
        template = generate_template(log_path)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(template, f, indent=2, ensure_ascii=False)
        print(f"  created  {out_file.name}")
        created += 1

    print(f"\n{created} created, {skipped} skipped.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    review_path = Path(args.review)
    if not review_path.exists():
        print(f"PRV-E001: review file not found: {review_path}", file=sys.stderr)
        return 1

    log_path = Path(args.log) if args.log else None

    report = generate_compact_report(review_path, log_path=log_path)
    print(report)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="parse_review_cli",
        description="Parse-log review: generate templates and compact chat reports.",
    )
    sub = parser.add_subparsers(dest="command")

    # --create
    p_create = sub.add_parser("create", help="Generate review template from a parse log.")
    p_create.add_argument("log", metavar="PARSE_LOG_JSON")
    p_create.add_argument("--output", "-o", default=None,
                          help="Output path (default: <log_dir>/<doc_id>_parse_review.json)")
    p_create.add_argument("--force", action="store_true", help="Overwrite existing review file.")

    # --create-all
    p_create_all = sub.add_parser("create-all",
                                   help="Generate review templates for all logs in a directory.")
    p_create_all.add_argument("log_dir", metavar="LOG_DIR")
    p_create_all.add_argument("--force", action="store_true", help="Overwrite existing files.")

    # --report
    p_report = sub.add_parser("report", help="Generate compact chat report from a review file.")
    p_report.add_argument("review", metavar="REVIEW_JSON")
    p_report.add_argument("--log", default=None,
                           help="Parse log JSON (auto-detected if review and log share a directory).")

    # Legacy single-flag forms for convenience
    parser.add_argument("--create", dest="_create_log", default=None,
                        metavar="PARSE_LOG_JSON",
                        help="Shortcut for: create <PARSE_LOG_JSON>")
    parser.add_argument("--create-all", dest="_create_all_dir", default=None,
                        metavar="LOG_DIR",
                        help="Shortcut for: create-all <LOG_DIR>")
    parser.add_argument("--report", dest="_report_review", default=None,
                        metavar="REVIEW_JSON",
                        help="Shortcut for: report <REVIEW_JSON>")
    parser.add_argument("--log", dest="_report_log", default=None,
                        metavar="PARSE_LOG_JSON",
                        help="Explicit log path for --report shortcut.")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing files (for --create / --create-all shortcuts).")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path (for --create shortcut).")

    args = parser.parse_args()

    # Dispatch subcommand
    if args.command == "create":
        sys.exit(cmd_create(args))
    elif args.command == "create-all":
        sys.exit(cmd_create_all(args))
    elif args.command == "report":
        sys.exit(cmd_report(args))

    # Legacy single-flag shortcuts
    if args._create_log:
        ns = argparse.Namespace(
            log=args._create_log, output=args.output, force=args.force
        )
        sys.exit(cmd_create(ns))
    if args._create_all_dir:
        ns = argparse.Namespace(log_dir=args._create_all_dir, force=args.force)
        sys.exit(cmd_create_all(ns))
    if args._report_review:
        ns = argparse.Namespace(review=args._report_review, log=args._report_log)
        sys.exit(cmd_report(ns))

    parser.print_help()
    sys.exit(0)


if __name__ == "__main__":
    main()
