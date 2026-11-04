"""Host-side runner for self-serve ingestion.

The web enqueues `ingest*` jobs in its jobs database; this package runs
them on the build host as the build user: the cycle driver's verbs, the
publish steps and the golden eval, as the runbook does by hand, and
writes progress back into the same database. Standard library only, so
the host needs no project virtualenv: everything heavy runs in the
batch and store containers. See `docs/architecture.md` §28 and the
strand design `docs/compact/strands/self-serve-ingestion/design.md`.
"""

from core.src.ingest_runner.config import RunnerConfig, ConfigError
from core.src.ingest_runner.runner import Runner

__all__ = ["RunnerConfig", "ConfigError", "Runner"]
