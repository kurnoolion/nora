"""CLI for environment management.

Usage:
    # List available stages
    python -m src.env.env_cli stages

    # Create an environment
    python -m src.env.env_cli create \
        --name profiler-review \
        --member alice \
        --doc-root /data/vzw-new-batch \
        --stages extract:parse \
        --scope VZW/Feb2026 \
        --objectives "Verify heading detection" "Check table extraction"

    # List all environments
    python -m src.env.env_cli list

    # Show environment details
    python -m src.env.env_cli show profiler-review

    # Initialize directory structure at document_root
    python -m src.env.env_cli init profiler-review

    # Delete an environment config
    python -m src.env.env_cli delete profiler-review
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.env.config import (
    DOC_ROOT_DIRS,
    EnvironmentConfig,
    PIPELINE_STAGES,
    STAGE_DESC,
    resolve_stage,
)

ENVS_DIR = Path("environments")


def _env_path(name: str) -> Path:
    return ENVS_DIR / f"{name}.json"


def _load_env(name: str) -> EnvironmentConfig:
    path = _env_path(name)
    if not path.exists():
        print(f"Error: Environment '{name}' not found at {path}")
        sys.exit(1)
    return EnvironmentConfig.load_json(path)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_stages(_args: argparse.Namespace) -> None:
    """List all pipeline stages."""
    print("Pipeline stages:")
    print(f"  {'#':<4} {'Name':<14} Description")
    print(f"  {'─'*4} {'─'*14} {'─'*40}")
    for i, (name, desc) in enumerate(PIPELINE_STAGES, 1):
        print(f"  {i:<4} {name:<14} {desc}")
    print(f"\nUse stage names or numbers with --stages (e.g., --stages extract:parse or --stages 1:3)")


def cmd_create(args: argparse.Namespace) -> None:
    """Create a new environment."""
    # Parse stages range
    stage_start, stage_end = "extract", "eval"
    if args.stages:
        parts = args.stages.split(":")
        stage_start = resolve_stage(parts[0])
        stage_end = resolve_stage(parts[1]) if len(parts) > 1 else stage_start

    # Parse scope
    mnos, releases = ["VZW"], ["Feb2026"]
    if args.scope:
        for scope_item in args.scope:
            parts = scope_item.split("/")
            if parts[0] not in mnos:
                mnos.append(parts[0]) if parts[0] != mnos[0] else None
            if len(parts) > 1 and parts[1] not in releases:
                releases.append(parts[1]) if parts[1] != releases[0] else None
        # Re-derive clean lists from all scope items
        mnos_set: list[str] = []
        releases_set: list[str] = []
        for scope_item in args.scope:
            parts = scope_item.split("/")
            if parts[0] not in mnos_set:
                mnos_set.append(parts[0])
            if len(parts) > 1 and parts[1] not in releases_set:
                releases_set.append(parts[1])
        mnos = mnos_set
        releases = releases_set if releases_set else ["Feb2026"]

    env = EnvironmentConfig(
        name=args.name,
        description=args.description or f"Environment for {args.member}",
        created_by=args.created_by or "admin",
        member=args.member,
        document_root=str(Path(args.doc_root).resolve()),
        stage_start=stage_start,
        stage_end=stage_end,
        mnos=mnos,
        releases=releases,
        objectives=args.objectives or [],
        model_name=args.model or "auto",
    )

    errors = env.validate()
    if errors:
        print("Validation errors:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    path = _env_path(args.name)
    if path.exists() and not args.force:
        print(f"Error: Environment '{args.name}' already exists. Use --force to overwrite.")
        sys.exit(1)

    ENVS_DIR.mkdir(parents=True, exist_ok=True)
    env.save_json(path)
    print(f"Environment '{args.name}' created: {path}")
    print(f"  Member: {env.member}")
    print(f"  Stages: {env.stage_start} -> {env.stage_end} ({', '.join(env.active_stages)})")
    print(f"  Scope:  {', '.join(env.mnos)} / {', '.join(env.releases)}")
    print(f"  Doc root: {env.document_root}")
    if env.objectives:
        print(f"  Objectives:")
        for obj in env.objectives:
            print(f"    - {obj}")
    print(f"\nNext: python -m src.env.env_cli init {args.name}")


def cmd_list(_args: argparse.Namespace) -> None:
    """List all environments."""
    if not ENVS_DIR.exists():
        print("No environments directory. Create one with: python -m src.env.env_cli create ...")
        return

    files = sorted(ENVS_DIR.glob("*.json"))
    if not files:
        print("No environments found.")
        return

    print(f"{'Name':<24} {'Member':<12} {'Stages':<20} {'Scope':<20}")
    print(f"{'─'*24} {'─'*12} {'─'*20} {'─'*20}")
    for f in files:
        try:
            env = EnvironmentConfig.load_json(f)
            stages = f"{env.stage_start}->{env.stage_end}"
            scope = f"{','.join(env.mnos)}/{','.join(env.releases)}"
            print(f"{env.name:<24} {env.member:<12} {stages:<20} {scope:<20}")
        except Exception as e:
            print(f"{f.stem:<24} (error: {e})")


def cmd_show(args: argparse.Namespace) -> None:
    """Show environment details."""
    env = _load_env(args.name)

    print(f"Environment: {env.name}")
    print(f"  Description:  {env.description}")
    print(f"  Created by:   {env.created_by} on {env.created_at}")
    print(f"  Member:       {env.member}")
    print(f"  Doc root:     {env.document_root}")
    print(f"  Stages:       {env.stage_start} -> {env.stage_end}")
    print(f"  Active:       {', '.join(env.active_stages)}")
    print(f"  MNOs:         {', '.join(env.mnos)}")
    print(f"  Releases:     {', '.join(env.releases)}")
    print(f"  Doc types:    {', '.join(env.doc_types)}")
    print(f"  Model:        {env.model_provider}/{env.model_name} (timeout={env.model_timeout}s)")
    if env.objectives:
        print(f"  Objectives:")
        for obj in env.objectives:
            print(f"    - {obj}")

    # Check directory structure
    root = env.doc_root
    print(f"\n  Directory status:")
    for dirname, desc in DOC_ROOT_DIRS.items():
        p = root / dirname
        status = "EXISTS" if p.exists() else "MISSING"
        count = ""
        if p.exists() and p.is_dir():
            files = list(p.iterdir())
            count = f" ({len(files)} items)" if files else " (empty)"
        print(f"    {dirname + '/':<16} {status}{count}")

    # Check corrections
    corrections_dir = root / "corrections"
    if corrections_dir.exists():
        corr_files = list(corrections_dir.iterdir())
        if corr_files:
            print(f"\n  Corrections available:")
            for cf in corr_files:
                print(f"    {cf.name}")


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize the directory structure for an environment."""
    env = _load_env(args.name)
    created = env.init_directories()
    if created:
        print(f"Created directories for '{args.name}':")
        for d in created:
            print(f"  {d}")
    else:
        print(f"All directories already exist for '{args.name}'.")

    print(f"\nExpected document root layout:")
    for dirname, desc in DOC_ROOT_DIRS.items():
        print(f"  {dirname + '/':<16} {desc}")
    print(f"\nPlace source documents in: {env.doc_root}/documents/")


def cmd_delete(args: argparse.Namespace) -> None:
    """Delete an environment config."""
    path = _env_path(args.name)
    if not path.exists():
        print(f"Environment '{args.name}' not found.")
        sys.exit(1)
    path.unlink()
    print(f"Environment '{args.name}' deleted.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage pipeline environments for team collaboration."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # stages
    sub.add_parser("stages", help="List all pipeline stages")

    # create
    p_create = sub.add_parser("create", help="Create a new environment")
    p_create.add_argument("--name", required=True, help="Environment name (used as filename)")
    p_create.add_argument("--member", required=True, help="Team member name")
    p_create.add_argument("--doc-root", required=True, help="Path to document root directory")
    p_create.add_argument(
        "--stages", default=None,
        help="Stage range as start:end (names or numbers, e.g., extract:parse or 1:3)",
    )
    p_create.add_argument(
        "--scope", nargs="+", default=None,
        help="MNO/Release pairs (e.g., VZW/Feb2026 ATT/Oct2025)",
    )
    p_create.add_argument("--objectives", nargs="+", default=None, help="Objective descriptions")
    p_create.add_argument("--description", default=None, help="Environment description")
    p_create.add_argument("--created-by", default=None, help="Admin name (default: admin)")
    p_create.add_argument("--model", default=None, help="Model name (default: auto)")
    p_create.add_argument("--force", action="store_true", help="Overwrite existing environment")

    # list
    sub.add_parser("list", help="List all environments")

    # show
    p_show = sub.add_parser("show", help="Show environment details")
    p_show.add_argument("name", help="Environment name")

    # init
    p_init = sub.add_parser("init", help="Initialize directory structure for an environment")
    p_init.add_argument("name", help="Environment name")

    # delete
    p_del = sub.add_parser("delete", help="Delete an environment config")
    p_del.add_argument("name", help="Environment name")

    args = parser.parse_args()

    commands = {
        "stages": cmd_stages,
        "create": cmd_create,
        "list": cmd_list,
        "show": cmd_show,
        "init": cmd_init,
        "delete": cmd_delete,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
