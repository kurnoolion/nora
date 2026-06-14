"""Input-layout pre-flight for multi-MNO SIRA (multi-mno-sira D-DRAFT-5).

Validates that every release directory under `<env_dir>/input/<MNO>/`
follows the MMMYYYY convention (e.g. `Feb2026`) BEFORE the pipeline runs
extract -> parse -> adapter. Catching a misnamed release dir here is the
earliest, clearest point — the operator fixes it before spending time on
extraction.

Why this lives in sandbox (not in core's `infer_metadata_from_path`):
the MMMYYYY convention is a multi-MNO-SIRA concern, and core serves the
legacy NORA pipeline where free-form releases (e.g. `OA-baseline`) are
valid. Baking MMMYYYY into core would break that path and invert the
module boundary (core must not depend on sandbox). The adapter's
`--multi-cell` partitioning is the backstop fail-loud; this is the
opt-in early check.

Usage::

    python -m sandbox.sira_preflight --env-dir /path/to/env_dir
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sandbox.sira_cells import is_valid_release


def find_input_release_dirs(env_dir: Path) -> list[tuple[Path, str, str]]:
    """Return (path, mno, release) for each
    `<env_dir>/input/<MNO>/<release>/` directory. Empty if `input/`
    is missing."""
    input_dir = env_dir / "input"
    if not input_dir.is_dir():
        return []
    out: list[tuple[Path, str, str]] = []
    for mno_dir in sorted(input_dir.iterdir()):
        if not mno_dir.is_dir():
            continue
        for rel_dir in sorted(mno_dir.iterdir()):
            if rel_dir.is_dir():
                out.append((rel_dir, mno_dir.name, rel_dir.name))
    return out


def validate_input_layout(env_dir: Path) -> list[tuple[str, str]]:
    """Return the list of (mno, release) whose release dir is NOT MMMYYYY.

    Empty list == all release dirs conform (or there are none to check).
    """
    return [
        (mno, release)
        for _path, mno, release in find_input_release_dirs(env_dir)
        if not is_valid_release(release)
    ]


def format_violations(violations: list[tuple[str, str]]) -> str:
    lines = "\n".join(f"  - input/{m}/{r}" for m, r in violations)
    return (
        f"{len(violations)} release dir(s) do not follow the MMMYYYY "
        f"convention (e.g. 'Feb2026'):\n{lines}\n"
        f"Multi-MNO SIRA derives release identity + ordering from the "
        f"input directory name <env_dir>/input/<MNO>/<MMMYYYY>/ "
        f"(D-DRAFT-5). Rename the offending dir(s) and re-run "
        f"extract -> parse."
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sira_preflight",
        description="Validate <env_dir>/input/<MNO>/<MMMYYYY>/ layout for "
                    "multi-MNO SIRA.",
    )
    p.add_argument("--env-dir", required=True, type=Path,
                   help="NORA env dir containing input/<MNO>/<release>/.")
    args = p.parse_args(argv)

    dirs = find_input_release_dirs(args.env_dir)
    if not dirs:
        print(f"no input/<MNO>/<release>/ dirs found under {args.env_dir}/input")
        return 0

    violations = validate_input_layout(args.env_dir)
    if violations:
        print(format_violations(violations), file=sys.stderr)
        return 1

    print(f"OK — {len(dirs)} release dir(s), all MMMYYYY-conforming:")
    for _path, mno, release in dirs:
        print(f"  input/{mno}/{release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
