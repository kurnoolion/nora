"""The runner loop: claim one job at a time, heartbeat while it runs,
dispatch by job type, record the outcome."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Callable

from core.src.ingest_runner import plan
from core.src.ingest_runner.config import RunnerConfig
from core.src.ingest_runner.db import RunnerDB
from core.src.ingest_runner.execute import CommandError, Exec

logger = logging.getLogger(__name__)

DISPATCH = {
    "ingest": plan.run_ingest,
    "ingest-flip": plan.run_flip,
    "ingest-rollback": plan.run_rollback,
    "ingest-abandon": plan.run_abandon,
    "ingest-purge": plan.run_purge,
}


class Runner:
    def __init__(self, cfg: RunnerConfig, db: RunnerDB | None = None, ex: Exec | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 healthz: Callable[[str], bool] | None = None,
                 post: Callable | None = None) -> None:
        self.cfg = cfg
        self.db = db or RunnerDB(cfg.jobs_db)
        self.ex = ex or Exec()
        self.sleep = sleep
        self.healthz = healthz
        self.post = post
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    # -- one job ---------------------------------------------------------------------

    def run_job(self, job: dict) -> str:
        """Run one claimed job to its end; returns the final job status."""
        job_id = job["id"]
        fn = DISPATCH.get(job["job_type"])
        params = job.get("params") or {}
        ingest = self.db.get_ingest(params.get("ingest_id", "")) if params.get("ingest_id") else None
        if fn is None or ingest is None:
            why = f"unknown job type {job['job_type']}" if fn is None else "job names no ingest"
            self.db.append_log(job_id, f"FATAL: {why}")
            self.db.finish_job(job_id, "failed", error=why)
            return "failed"

        hb_stop = threading.Event()

        def _heartbeat() -> None:
            while not hb_stop.wait(self.cfg.heartbeat_seconds):
                try:
                    self.db.heartbeat(job_id)
                except Exception:  # noqa: BLE001 — never kill the job over a heartbeat
                    logger.exception("heartbeat failed for %s", job_id)

        hb = threading.Thread(target=_heartbeat, name=f"hb-{job_id[:8]}", daemon=True)
        hb.start()
        ctx = plan.Ctx(cfg=self.cfg, db=self.db, ex=self.ex, job=job, ingest=ingest,
                       sleep=self.sleep, healthz=self.healthz)
        if self.post is not None:
            ctx.post = self.post
        ctx.log(f"{job['job_type']} claimed by {self.cfg.runner_name}")
        status = "completed"
        try:
            fn(ctx)
            self.db.finish_job(job_id, "completed", summary=_summary(job["job_type"], self.db.get_ingest(ingest["id"]) or {}))
        except plan.Cancelled as exc:
            status = "cancelled"
            ctx.log(f"cancelled: {exc}")
            ctx.set_state("cancelled", error=str(exc))
            self.db.finish_job(job_id, "cancelled", error=str(exc))
        except (plan.StageError, CommandError) as exc:
            status = "failed"
            ctx.log(f"FAILED: {exc}")
            if job["job_type"] == "ingest":
                ctx.set_state("failed", error=str(exc))
            else:
                self.db.update_ingest(ingest["id"], error=str(exc))
            self.db.finish_job(job_id, "failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001 — record, never crash the loop
            status = "failed"
            logger.exception("job %s crashed", job_id)
            ctx.log("FATAL: " + traceback.format_exc().rstrip())
            if job["job_type"] == "ingest":
                ctx.set_state("failed", error=f"runner error: {exc}")
            self.db.finish_job(job_id, "failed", error=f"runner error: {exc}")
        finally:
            hb_stop.set()
            hb.join(timeout=5)
        return status

    # -- the loop ------------------------------------------------------------------------

    def run_once(self, worker: str = "") -> str | None:
        """Claim and run one job; None when the queue holds none. `worker`
        names this worker in the runners table when there are several."""
        name = worker or self.cfg.runner_name
        job = self.db.claim_next(name, one_per_mno=self.cfg.per_build)
        self.db.seen(name, job["id"] if job else None)
        if job is None:
            return None
        logger.info("%s claimed %s %s", name, job["job_type"], job["id"])
        try:
            return self.run_job(job)
        finally:
            self.db.seen(name, None)

    def _loop(self, worker: str = "") -> None:
        while not self._stop.is_set():
            try:
                if self.run_once(worker) is None:
                    self.sleep(self.cfg.poll_seconds)
            except Exception:  # noqa: BLE001
                logger.exception("runner loop error; continuing")
                self.sleep(self.cfg.poll_seconds)

    def run_forever(self) -> None:
        n = max(1, int(self.cfg.max_parallel_ingests or 1))
        logger.info("ingest runner %s polling %s every %.0fs with %d worker(s)", self.cfg.runner_name,
                    self.cfg.jobs_db, self.cfg.poll_seconds, n)
        if n == 1:
            self._loop()
            return
        # several ingests at once (stage graph step 6): one thread per worker,
        # each claiming its own jobs; the per-MNO rule lives in the claim
        workers = [threading.Thread(target=self._loop, args=(f"{self.cfg.runner_name}#{i + 1}",),
                                    name=f"worker-{i + 1}", daemon=True) for i in range(n)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()


def _summary(job_type: str, ing: dict) -> str:
    if job_type == "ingest":
        return (f"{ing.get('mno')}/{ing.get('release')} staged as {ing.get('label')}: "
                f"gate {ing.get('verdict') or '-'}")
    if job_type == "ingest-flip":
        return f"{ing.get('label')} served (previous {ing.get('previous_label') or '-'})"
    if job_type == "ingest-rollback":
        return f"rolled back to {ing.get('previous_label')}"
    if job_type == "ingest-purge":
        return f"{ing.get('mno')}/{ing.get('release')} purged (build {ing.get('build') or '-'}, label {ing.get('label') or '-'})"
    return f"{ing.get('mno')}/{ing.get('release')} abandoned"
