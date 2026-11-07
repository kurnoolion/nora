"""The stages of one self-serve ingest, and the flip, rollback and
abandon jobs (strand design §3).

Every stage runs the command the runbook shows, through `Exec`, and
streams its output into the job log. Detached cycle verbs (parse,
taxonomy, enrich) are waited on through the build's `CYCLE.json` and
the phase log, exactly as `cycle.sh status` reads them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from core.src.ingest_runner import verdict as V
from core.src.ingest_runner.config import (
    CTR_CORRECTIONS, CTR_ENV, CTR_LABELS, CTR_SERVE, CTR_WORK, ConfigError, RunnerConfig,
    read_env_value,
)
from core.src.ingest_runner import webhook
from core.src.ingest_runner.config import webhook_host_allowed
from core.src.ingest_runner.db import RunnerDB, now_iso
from core.src.ingest_runner.execute import CommandError, Exec, Result
from core.src.web.jobs_schema import INGEST_STAGES as STAGES, NOTIFY_STATES, RETRY_FROM  # noqa: F401

_CELL_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,30}$")
_REL_RE = re.compile(r"^[A-Z][a-z]{2}\d{4}$")


class StageError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


@dataclass
class Ctx:
    cfg: RunnerConfig
    db: RunnerDB
    ex: Exec
    job: dict
    ingest: dict
    sleep: Callable[[float], None] = time.sleep
    healthz: Callable[[str], bool] | None = None   # None: HTTP GET, 200 = healthy
    post: webhook.Poster = webhook.http_post       # webhook delivery (tests replace it)
    build: str = ""
    label: str = ""
    run_ref: str = ""
    notes: dict = field(default_factory=dict)

    # -- helpers ----------------------------------------------------------------

    @property
    def job_id(self) -> str:
        return self.job["id"]

    @property
    def cell(self) -> str:
        """The cell's name, `<mno>/<release>` (one spelling everywhere)."""
        return f"{self.ingest['mno']}/{self.ingest['release']}"

    def log(self, line: str) -> None:
        self.db.append_log(self.job_id, line)

    def run(self, argv: list[str], *, env: dict[str, str] | None = None,
            cwd: str | None = None, what: str = "", quiet: bool = False) -> Result:
        self.log(f"$ {' '.join(argv)}")
        res = self.ex.run(argv, env=env, cwd=cwd, on_line=None if quiet else self.log)
        if res.rc != 0:
            raise CommandError(argv, res, what)
        return res

    def cycle(self, *verb_args: str) -> Result:
        env = {**os.environ, **self.cfg.cycle_env}
        return self.run([str(Path(self.cfg.docker_dir) / "cycle.sh"), *verb_args],
                        env=env, cwd=self.cfg.docker_dir, what=f"cycle.sh {verb_args[0]}")

    def compose(self, project: str, env_file: str, *args: str,
                extra_env: dict[str, str] | None = None, what: str = "",
                quiet: bool = False) -> Result:
        env = {**os.environ, **_job_user(env_file), **(extra_env or {})}
        proj = ["-p", project] if project else []
        return self.run(["docker", "compose", *proj, "--env-file", env_file, *args],
                        env=env, cwd=self.cfg.docker_dir, what=what, quiet=quiet)

    def batch_oneshot(self, project: str, env_file: str, service: str, cmd: list[str], *,
                      mounts: list[tuple[str, str]], extra_env: dict[str, str] | None = None,
                      what: str = "") -> Result:
        vols: list[str] = []
        for host, ctr in mounts:
            vols += ["-v", f"{host}:{ctr}"]
        return self.compose(project, env_file, "--profile", "ingest", "run", "--rm", "-T",
                            *vols, service, *cmd, extra_env=extra_env, what=what)

    def set_state(self, state: str, **cols: object) -> None:
        """Change the ingest's state, then tell the callback URL, if any.
        Delivery is logged and never fails the job."""
        self.db.update_ingest(self.ingest["id"], state=state, **cols)
        self.ingest["state"] = state
        for k, v in cols.items():
            self.ingest[k] = v
        if state not in NOTIFY_STATES:
            return
        url = (self.ingest.get("callback_url") or "").strip()
        if not url:
            return
        if not webhook_host_allowed(url, self.cfg.webhook_hosts):
            self.log(f"webhook: {url} is not an allowed host (webhook_hosts in the runner config) — not posted")
            return
        row = self.db.get_ingest(self.ingest["id"]) or self.ingest
        payload = {"event": f"ingest.{state}", "ingest_id": row.get("id"), "mno": row.get("mno"),
                   "release": row.get("release"), "state": state, "stage": row.get("stage"),
                   "verdict": row.get("verdict"), "label": row.get("label"),
                   "previous_label": row.get("previous_label"), "error": row.get("error"),
                   "at": now_iso()}
        ok, note = webhook.deliver(url, payload, secret=self.cfg.webhook_secret,
                                   timeout=self.cfg.webhook_timeout_seconds, post=self.post, sleep=self.sleep)
        self.log(f"webhook: ingest.{state} → {url}: {note}")

    def check_cancelled(self) -> None:
        if self.db.job_status(self.job_id) == "cancelled":
            raise Cancelled("cancelled by the user")

    # -- detached verbs -----------------------------------------------------------

    def wait_detached(self, phase: str) -> None:
        """Follow a detached phase to its `CYC-EXIT` line, streaming the log."""
        cj = self.cfg.build_dir(self.build) / "CYCLE.json"
        entries = [p for p in json.loads(cj.read_text(encoding="utf-8"))["phases"] if p["phase"] == phase]
        if not entries or not entries[-1].get("log"):
            raise StageError(f"{phase}: no detached log recorded in {cj}")
        log = Path(entries[-1]["log"])
        self.log(f"{phase}: following {log.name}")
        deadline = time.monotonic() + self.cfg.detached_timeout_seconds
        pos = 0
        buf = ""
        while True:
            if log.is_file():
                with log.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos = fh.tell()
                if chunk:
                    buf += chunk
                    *done, buf = buf.split("\n")
                    for line in done:
                        self.log(line)
                        m = re.search(r"CYC-EXIT rc=(\d+)", line)
                        if m:
                            rc = int(m.group(1))
                            if rc != 0:
                                raise StageError(f"{phase} failed rc={rc} (see the log above)")
                            return
            if time.monotonic() > deadline:
                raise StageError(f"{phase}: no CYC-EXIT after {self.cfg.detached_timeout_seconds:.0f}s")
            self.check_cancelled()
            self.sleep(self.cfg.detached_poll_seconds)


# -- the ingest stages -----------------------------------------------------------

def st_documents(c: Ctx) -> None:
    ing = c.ingest
    if not _CELL_RE.match(ing["mno"]) or not _REL_RE.match(ing["release"]):
        raise StageError(f"bad cell coordinates {ing['mno']}/{ing['release']}")
    try:
        src = c.cfg.resolve_source(ing["source"])
    except ConfigError as exc:
        raise StageError(str(exc))
    if not src.is_dir():
        raise StageError(f"source folder not found on the host: {src}")
    dst = Path(c.cfg.requirements_dir) / ing["mno"] / ing["release"]
    if dst.exists() and any(dst.iterdir()):
        raise StageError(f"cell {c.cell} already exists under the requirements dir — abandon or pick another release")
    exts = {e.lower() for e in c.cfg.document_extensions}
    files = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in exts)
    if not files:
        raise StageError(f"no documents ({', '.join(sorted(exts))}) in {src}")
    dst.mkdir(parents=True, exist_ok=True)
    for p in files:
        shutil.copy2(p, dst / p.name)
    c.log(f"documents: {len(files)} file(s) staged into {dst}")


def _job_user(env_file: str) -> dict[str, str]:
    """JOB_UID/JOB_GID for a one-shot: the runner's own identity unless the
    shell or the env file sets them. compose's `user:` defaults to 0:0, and a
    serving or staging env has no reason to carry the job user — without
    this, eval runs and registry writes land root-owned on the host."""
    out: dict[str, str] = {}
    for var, val in (("JOB_UID", os.getuid()), ("JOB_GID", os.getgid())):
        if os.environ.get(var) or read_env_value(env_file, var):
            continue
        out[var] = str(val)
    return out


def _label_taken(c: Ctx, name: str) -> bool:
    """A label is taken when a promoted snapshot or a registry release set
    carries it (labels are immutable). The registry is read if present."""
    if (Path(c.cfg.labels_root) / name).exists():
        return True
    reg = Path(c.cfg.registry_root) / "registry.db"
    if reg.is_file():
        try:
            con = sqlite3.connect(f"file:{reg}?mode=ro", uri=True)
            try:
                return con.execute("SELECT 1 FROM release_set WHERE label=?", (name,)).fetchone() is not None
            finally:
                con.close()
        except sqlite3.Error:
            return False
    return False


def _next_free(c: Ctx, prefix: str, *, builds: bool) -> str:
    """The smallest <prefix><n> that is neither a build (when asked) nor a label."""
    n = 1
    while (builds and c.cfg.build_dir(f"{prefix}{n}").exists()) or _label_taken(c, f"{prefix}{n}"):
        n += 1
    return f"{prefix}{n}"


def _label_prefix(c: Ctx) -> str:
    ing = c.ingest
    day = datetime.now(UTC).strftime("%Y%m%d")
    return f"{ing['release']}-{ing['mno']}-{day}-"


def _next_build_id(c: Ctx) -> str:
    """Build id = label: the first same-day sequence number no build, promoted
    snapshot or release set carries (a removed build does not free its label)."""
    return _next_free(c, _label_prefix(c), builds=True)


def st_start(c: Ctx) -> None:
    seed = c.db.last_served_build() or ""
    if seed and not c.cfg.build_dir(seed).is_dir():
        c.log(f"start: last served build {seed} is gone from the builds root; seeding from the configured {c.cfg.seed_build or '-'}")
        seed = ""
    seed = seed or c.cfg.seed_build
    if not seed:
        raise StageError("no seed build: set seed_build in the runner config for the first ingest")
    c.build = _next_build_id(c)
    c.label = c.build
    c.db.update_ingest(c.ingest["id"], build=c.build, label=c.label)
    c.cycle("start", c.build, "--seed", seed)
    c.log(f"start: build {c.build} seeded from {seed}")


def st_parse(c: Ctx) -> None:
    c.cycle("parse", "--cell", c.cell, "--skip-standards")
    c.wait_detached("parse")


def st_prompts(c: Ctx) -> None:
    c.cycle("prompts", "--yes")


def st_taxonomy(c: Ctx) -> None:
    c.cycle("taxonomy", "--cell", c.cell)
    c.wait_detached("taxonomy")


def st_enrich(c: Ctx) -> None:
    args = ["enrich", "--cell", c.cell]
    if c.notes.get("retry"):
        args.append("--retry-failed")
    c.cycle(*args)
    c.wait_detached("enrich")


def st_verify(c: Ctx) -> None:
    c.cycle("verify-enrich", "--yes")


def st_promote(c: Ctx) -> None:
    if _label_taken(c, c.label):
        # same-day re-ingest after a cleanup: the build is ours, the label is
        # not (an older snapshot or release set still carries it)
        new = _next_free(c, _label_prefix(c), builds=False)
        c.log(f"promote: label {c.label} is taken by an earlier snapshot or release set; using {new}")
        c.label = new
        c.db.update_ingest(c.ingest["id"], label=new)
    args = ["promote", "--label", c.label]
    if c.cfg.prompt_scheme:
        args += ["--scheme", c.cfg.prompt_scheme]
    c.cycle(*args)


def _work_cells(c: Ctx) -> str:
    return f"{CTR_WORK}/{c.label}/cells"


def _run_name(c: Ctx) -> str:
    cj = c.cfg.build_dir(c.build) / "CYCLE.json"
    try:
        return str(json.loads(cj.read_text(encoding="utf-8")).get("run_name") or c.build)
    except (OSError, json.JSONDecodeError):
        return c.build


def heal_best_link(c: Ctx) -> None:
    """A resumed enrichment that found every document already enriched
    leaves `enrichments/doc/best.jsonl` as a dangling link (SIRA skips
    the apply block but still re-points the link; field-found on the
    first retry). `sira_incremental promote` writes the missing file;
    run it when the link dangles, before the converter trips CNV-011."""
    best = c.cfg.sira_build_dir(c.build) / c.ingest["mno"] / c.ingest["release"] / "enrichments" / "doc" / "best.jsonl"
    if not os.path.islink(best) or os.path.exists(best):
        return
    run = _run_name(c)
    c.log(f"enrich: best.jsonl dangles in {c.cell} (resumed run) — promoting run {run}")
    c.batch_oneshot("", c.cfg.builds_wiring, "sira-batch",
                    ["python", "-m", "sandbox.sira_incremental", "promote",
                     "--dataset", f"/data/db/{c.cell}", "--run-name", run],
                    mounts=[], what="sira_incremental promote")
    if os.path.islink(best) and not os.path.exists(best):
        raise StageError(f"best.jsonl still dangles in {c.cell} after promote — pin the run by hand (CNV-011)")


def st_convert(c: Ctx) -> None:
    heal_best_link(c)
    Path(c.cfg.work_dir, c.label).mkdir(parents=True, exist_ok=True)
    cmd = ["python", "-m", "sandbox.cell_db_convert", "--label", f"{CTR_LABELS}/{c.label}",
           "--cells", c.cell, "--out", _work_cells(c), "--verify", str(c.cfg.verify_n), "--force"]
    mounts = [(c.cfg.labels_root, CTR_LABELS), (c.cfg.work_dir, CTR_WORK)]
    if c.cfg.overlay_db:
        mounts.append((str(Path(c.cfg.overlay_db).parent), CTR_CORRECTIONS))
        cmd += ["--overlay-db", f"{CTR_CORRECTIONS}/{Path(c.cfg.overlay_db).name}"]
    elif c.cfg.overlay_root:
        mounts.append((c.cfg.overlay_root, CTR_CORRECTIONS))
        cmd += ["--overlay-root", CTR_CORRECTIONS]
    # the converter runs off the builds wiring (no stack project), as cycle.sh's verbs do
    c.batch_oneshot("", c.cfg.builds_wiring, "sira-batch", cmd, mounts=mounts, what="cell_db_convert")


def st_publish(c: Ctx) -> None:
    cmd = ["python", "-m", "core.src.stores.publish", "publish", "--serve-root", CTR_SERVE,
           "--label", c.label, "--cells-src", _work_cells(c),
           "--env-dir", f"{CTR_LABELS}/{c.label}/nora"]
    if c.cfg.prompt_scheme:
        cmd += ["--prompt-scheme", c.cfg.prompt_scheme]
    c.batch_oneshot(c.cfg.stack_project, c.cfg.stack_env, "nora-batch", cmd,
                    mounts=_publish_mounts(c), what="publish publish")


def _publish_mounts(c: Ctx) -> list[tuple[str, str]]:
    return [(c.cfg.registry_root, CTR_SERVE), (c.cfg.labels_root, CTR_LABELS), (c.cfg.work_dir, CTR_WORK)]


def _staging_healthz(c: Ctx) -> str:
    if c.cfg.staging_healthz_url:
        return c.cfg.staging_healthz_url
    port = "8040"
    try:
        for line in Path(c.cfg.staging_env).read_text(encoding="utf-8").splitlines():
            if line.startswith("STORE_PORT="):
                port = line.split("=", 1)[1].strip()
    except OSError:
        pass
    return f"http://127.0.0.1:{port}/healthz"


def _http_ok(url: str) -> bool:
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (local URL)
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _wait_healthz(c: Ctx, url: str, timeout: float = 300.0) -> None:
    probe = c.healthz or _http_ok
    deadline = time.monotonic() + timeout
    while True:
        if probe(url):
            return
        if time.monotonic() > deadline:
            raise StageError(f"staging store not healthy after {timeout:.0f}s ({url})")
        c.sleep(3)


def st_eval(c: Ctx) -> None:
    stage_env = {"SERVE_RELEASE_SET": c.label}
    c.compose(c.cfg.staging_project, c.cfg.staging_env, "--profile", "serve", "up", "-d", "nora-store",
              extra_env=stage_env, what="staging store up")
    try:
        _wait_healthz(c, _staging_healthz(c))
        cmd = ["python", "-m", "core.src.eval.golden_cli", "--env-dir", CTR_ENV,
               "--stack-url", c.cfg.staging_store_url, "--stack-label", c.label,
               "--stage", "1", "--allow-map-mismatch"]
        if c.cfg.prompt_scheme:
            cmd += ["--sira-prompt-scheme", c.cfg.prompt_scheme]
        res = c.ex.run(["docker", "compose", "-p", c.cfg.staging_project, "--env-file", c.cfg.staging_env,
                        "--profile", "ingest", "run", "--rm", "-T", "nora-batch", *cmd],
                       env={**os.environ, **_job_user(c.cfg.staging_env), **stage_env},
                       cwd=c.cfg.docker_dir, on_line=c.log)
        run_ref = ""
        for line in res.lines:
            m = re.search(r"Run artifacts:\s*(\S+)", line)
            if m:
                run_ref = Path(m.group(1)).name
        if res.rc == 2 or not run_ref:
            raise StageError(f"golden eval did not run (rc={res.rc}); see the log above")
        c.run_ref = run_ref
        c.db.update_ingest(c.ingest["id"], run_ref=run_ref)
        c.log(f"eval: run {run_ref} (rc={res.rc})")
    finally:
        try:
            c.compose(c.cfg.staging_project, c.cfg.staging_env, "--profile", "serve", "down",
                      "--remove-orphans", extra_env=stage_env, what="staging store down", quiet=True)
        except CommandError as exc:
            c.log(f"WARN: staging store down failed: {exc}")


def st_gate(c: Ctx) -> None:
    report = V.read_report(c.cfg.golden_dir, c.run_ref)
    recall = V.recall_avg(report)
    errors = V.gev_errors(report)
    baseline = c.db.baseline_recall()
    source = "last passed ingest"
    if baseline is None and c.cfg.baseline_recall is not None:
        baseline, source = float(c.cfg.baseline_recall), "runner config"
    if baseline is None:
        rec = V.latest_recorded_recall(c.cfg.golden_dir, exclude=c.run_ref)
        if rec:
            baseline, source = rec[0], f"recorded run {rec[1]}"
    verdict, detail = V.decide(recall, baseline, c.cfg.eval_tolerance, errors)
    c.log(f"gate: {verdict} — {detail} (baseline from {source if baseline is not None else 'nothing'})")
    cmd = ["python", "-m", "core.src.stores.publish", "gate", "--serve-root", CTR_SERVE,
           "--label", c.label, "--kind", "golden-stage1", "--verdict", verdict, "--run-ref", c.run_ref]
    c.batch_oneshot(c.cfg.stack_project, c.cfg.stack_env, "nora-batch", cmd,
                    mounts=[(c.cfg.registry_root, CTR_SERVE)], what="publish gate")
    c.db.update_ingest(c.ingest["id"], recall_avg=recall, baseline=baseline, verdict=verdict,
                       verdict_detail=detail)


STAGE_FNS = {
    "documents": st_documents, "start": st_start, "parse": st_parse, "prompts": st_prompts,
    "taxonomy": st_taxonomy, "enrich": st_enrich, "verify": st_verify, "promote": st_promote,
    "convert": st_convert, "publish": st_publish, "eval": st_eval, "gate": st_gate,
}


def run_ingest(c: Ctx) -> None:
    """Run the stages from the start, or from `params.retry_from`."""
    retry_from = (c.job.get("params") or {}).get("retry_from") or ""
    if retry_from:
        if retry_from not in STAGES:
            raise StageError(f"unknown retry stage {retry_from!r}")
        c.build = c.ingest.get("build") or ""
        c.label = c.ingest.get("label") or c.build
        c.run_ref = c.ingest.get("run_ref") or ""
        if STAGES.index(retry_from) > STAGES.index("start") and not c.build:
            raise StageError(f"retry from {retry_from} needs a build on the ingest")
        c.notes["retry"] = True
        c.log(f"retry from stage {retry_from} (build {c.build or '-'})")
    todo = STAGES[STAGES.index(retry_from):] if retry_from else STAGES
    total = len(STAGES)
    c.set_state("running", job_id=c.job_id, error=None)
    for name in todo:
        c.check_cancelled()
        idx = STAGES.index(name)
        c.db.set_job(c.job_id, current_stage=name, progress=int(idx * 100 / total))
        c.db.update_ingest(c.ingest["id"], stage=name)
        c.log(f"[{idx + 1}/{total}] {name}")
        STAGE_FNS[name](c)
    c.set_state("staged", stage=None)


# -- flip / rollback / abandon ------------------------------------------------------

def _publish_json(res: Result) -> dict:
    text = "\n".join(res.lines)
    start = text.find("{")
    if start < 0:
        return {}
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return {}


def run_flip(c: Ctx) -> None:
    ing = c.ingest
    if ing.get("state") != "staged" or ing.get("verdict") != "pass":
        raise StageError(f"ingest is {ing.get('state')} with verdict {ing.get('verdict') or '-'}: not flippable")
    label = ing["label"]
    cmd = ["python", "-m", "core.src.stores.publish", "flip", "--serve-root", CTR_SERVE,
           "--label", label, "--store-url", c.cfg.store_url]
    res = c.batch_oneshot(c.cfg.stack_project, c.cfg.stack_env, "nora-batch", cmd,
                          mounts=[(c.cfg.registry_root, CTR_SERVE)], what="publish flip")
    out = _publish_json(res)
    previous = out.get("from_label") or None
    c.set_state("served", previous_label=previous, flipped_at=now_iso(), job_id=c.job_id)
    c.log(f"flip: {label} is active (previous: {previous or '-'})")
    # the release map follows the flip; the new build holds the parse trees advance checks
    adv = ["python", "-m", "core.src.eval.golden_release_cli", "advance", "--env-dir", CTR_ENV,
           "--mno", ing["mno"], "--release", ing["release"], "--by", c.cfg.runner_name, "--yes"]
    try:
        res = c.batch_oneshot(c.cfg.stack_project, c.cfg.stack_env, "nora-batch", adv, mounts=[],
                              extra_env={"NORA_ENV_DIR": str(c.cfg.build_dir(ing["build"]))},
                              what="golden_release_cli advance")
        flagged = sum(1 for line in res.lines if line.rstrip().endswith("-> REVIEW"))
        c.log(f"release map advanced for {ing['mno']} to {ing['release']}; {flagged} sample(s) flagged for review")
    except CommandError as exc:
        c.log(f"WARN: release map not advanced: {exc} — run `golden_release_cli advance` by hand")


def run_rollback(c: Ctx) -> None:
    ing = c.ingest
    if ing.get("state") != "served" or not ing.get("previous_label"):
        raise StageError(f"ingest is {ing.get('state')} (previous label {ing.get('previous_label') or '-'}): nothing to roll back to")
    cmd = ["python", "-m", "core.src.stores.publish", "rollback", "--serve-root", CTR_SERVE,
           "--to", ing["previous_label"], "--store-url", c.cfg.store_url]
    c.batch_oneshot(c.cfg.stack_project, c.cfg.stack_env, "nora-batch", cmd,
                    mounts=[(c.cfg.registry_root, CTR_SERVE)], what="publish rollback")
    c.set_state("rolled_back", rolled_back_at=now_iso(), job_id=c.job_id)
    c.log(f"rollback: {ing['previous_label']} is active again; the release map still names "
          f"{ing['release']} for {ing['mno']} — golden runs pass --allow-map-mismatch until the next flip")


def run_abandon(c: Ctx) -> None:
    ing = c.ingest
    if ing.get("state") not in ("failed", "cancelled", "staged"):
        raise StageError(f"ingest is {ing.get('state')}: only failed, cancelled or staged ingests can be abandoned")
    build = ing.get("build") or ""
    active = c.cfg.active_file
    if build and active.is_file() and active.read_text(encoding="utf-8").strip() == build:
        c.cycle("abandon", "--note", f"self-serve ingest {ing['id']} abandoned")
    else:
        c.log("abandon: no cycle in flight for this ingest")
    if c.cfg.abandon_removes_documents:
        cell = Path(c.cfg.requirements_dir) / ing["mno"] / ing["release"]
        if cell.is_dir():
            shutil.rmtree(cell)
            c.log(f"abandon: removed the staged documents under {cell}")
    c.set_state("abandoned", job_id=c.job_id)
