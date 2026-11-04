"""CLI: `python -m core.src.ingest_runner.ingest_runner_cli --config config/ingest_runner.json`

Runs as a long-lived service on the build host (a `systemd --user` unit of
the build user, lingering on). `--once` claims and runs one job, then
exits (for a field run or a check); `--check` validates the config and
the jobs database and exits.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from core.src.ingest_runner.config import ConfigError, RunnerConfig
from core.src.ingest_runner.db import RunnerDB
from core.src.ingest_runner.runner import Runner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.src.ingest_runner.ingest_runner_cli",
                                 description="Host-side runner for self-serve ingestion jobs.")
    ap.add_argument("--config", default="config/ingest_runner.json")
    ap.add_argument("--once", action="store_true", help="run one job (if any) and exit")
    ap.add_argument("--check", action="store_true", help="validate the config and the jobs DB, then exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = RunnerConfig.load(args.config)
    except ConfigError as exc:
        print(f"IRN-E001: {exc}", file=sys.stderr)
        return 2
    try:
        db = RunnerDB(cfg.jobs_db)
    except Exception as exc:  # noqa: BLE001
        print(f"IRN-E002: cannot open jobs DB {cfg.jobs_db}: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print(f"ok: runner {cfg.runner_name}; jobs DB {cfg.jobs_db}; builds root {cfg.nora_builds_root}")
        return 0
    runner = Runner(cfg, db=db)
    if args.once:
        status = runner.run_once()
        print(status or "no job")
        return 0 if status in (None, "completed") else 1
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: runner.stop())
    runner.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
