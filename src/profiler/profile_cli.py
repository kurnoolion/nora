"""CLI entry point for the DocumentProfiler.

Usage:
    # Create a new profile from representative docs
    python -m src.profiler.profile_cli create \
        --name VZW_OA \
        --docs data/extracted/LTEDATARETRY_ir.json data/extracted/LTEB13NAC_ir.json \
        --output profiles/vzw_oa_profile.json

    # Update profile with additional docs
    python -m src.profiler.profile_cli update \
        --profile profiles/vzw_oa_profile.json \
        --docs data/extracted/LTESMS_ir.json

    # Validate profile against a document
    python -m src.profiler.profile_cli validate \
        --profile profiles/vzw_oa_profile.json \
        --doc data/extracted/LTEOTADM_ir.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.models.document import DocumentIR
from src.profiler.profile_schema import DocumentProfile
from src.profiler.profiler import DocumentProfiler


def cmd_create(args: argparse.Namespace) -> None:
    docs = [DocumentIR.load_json(Path(p)) for p in args.docs]
    profiler = DocumentProfiler()
    profile = profiler.create_profile(docs, profile_name=args.name)

    output = Path(args.output)
    profile.save_json(output)
    print(f"\nProfile saved to: {output}")


def cmd_update(args: argparse.Namespace) -> None:
    profile = DocumentProfile.load_json(Path(args.profile))
    docs = [DocumentIR.load_json(Path(p)) for p in args.docs]

    profiler = DocumentProfiler()
    profile = profiler.update_profile(profile, docs)

    profile.save_json(Path(args.profile))
    print(f"\nProfile updated: {args.profile} (v{profile.profile_version})")


def cmd_validate(args: argparse.Namespace) -> None:
    profile = DocumentProfile.load_json(Path(args.profile))
    doc = DocumentIR.load_json(Path(args.doc))

    profiler = DocumentProfiler()
    report = profiler.validate_profile(profile, doc)

    print(f"\n{'='*50}")
    print(f"Validation Report: {report['document']}")
    print(f"Profile: {report['profile']}")
    print(f"{'='*50}")
    print(f"Total blocks:         {report['total_blocks']}")
    print(f"Text blocks:          {report['text_blocks']}")
    print(f"Headings detected:    {report['headings_detected']}")
    print(f"  By level:           {report['headings_by_level']}")
    print(f"Sections w/ numbers:  {report['sections_with_numbers']}")
    print(f"Max section depth:    {report['max_section_depth']}")
    print(f"Requirement IDs:      {report['requirement_ids_found']}")
    print(f"H/F pattern matches:  {report['header_footer_matches']}")
    print(f"Plan metadata:")
    for k, v in report.get("plan_metadata", {}).items():
        print(f"  {k}: {v}")

    # Warnings
    warnings = []
    if report["headings_detected"] == 0:
        warnings.append("No headings detected — heading rules may be wrong")
    if report["requirement_ids_found"] == 0:
        warnings.append("No requirement IDs found — pattern may not match this doc")
    if not any(report.get("plan_metadata", {}).values()):
        warnings.append("No plan metadata extracted — patterns may need tuning")

    if warnings:
        print(f"\nWarnings:")
        for w in warnings:
            print(f"  ! {w}")
    else:
        print(f"\nAll checks passed.")


def main():
    parser = argparse.ArgumentParser(
        description="DocumentProfiler — derive document structure profiles from representative docs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = subparsers.add_parser("create", help="Create a new profile")
    p_create.add_argument("--name", required=True, help="Profile name")
    p_create.add_argument(
        "--docs", nargs="+", required=True,
        help="Paths to extracted IR JSON files (representative docs)",
    )
    p_create.add_argument(
        "--output", required=True, help="Output path for profile JSON",
    )
    p_create.set_defaults(func=cmd_create)

    # update
    p_update = subparsers.add_parser("update", help="Update an existing profile with more docs")
    p_update.add_argument("--profile", required=True, help="Path to existing profile JSON")
    p_update.add_argument(
        "--docs", nargs="+", required=True,
        help="Paths to additional extracted IR JSON files",
    )
    p_update.set_defaults(func=cmd_update)

    # validate
    p_validate = subparsers.add_parser("validate", help="Validate a profile against a document")
    p_validate.add_argument("--profile", required=True, help="Path to profile JSON")
    p_validate.add_argument("--doc", required=True, help="Path to extracted IR JSON to validate against")
    p_validate.set_defaults(func=cmd_validate)

    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    args.func(args)


if __name__ == "__main__":
    main()
