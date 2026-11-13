"""The stages of one self-serve ingest, and the flip, rollback and
abandon jobs (strand design §3).

Every stage runs the command the runbook shows, through `Exec`, and
streams its output into the job log. Detached cycle verbs (parse,
taxonomy, enrich) are waited on through the build's `CYCLE.json` and
the phase log, exactly as `cycle.sh status` reads them.
"""

from __future__ import annotations

import contextlib
import fcntl
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
from core.src.web.jobs_schema import INGEST_STAGES as STAGES, NOTIFY_STATES, PURGEABLE_STATES, RETRY_FROM  # noqa: F401

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
    if ing.get("redo_of"):
        c.log(f"documents: a redo of {str(ing['redo_of'])[:8]} uses the served cell's documents")
        return
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
    c.db.update_ingest(ing["id"], documents_staged=1)
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


def _serves(registry_root: str, unit: str) -> bool | None:
    """Whether the registry under `registry_root` has `unit` in its active
    cells; None when it cannot be read (a missing registry serves nothing)."""
    reg = Path(registry_root) / "registry.db"
    if not reg.is_file():
        return False
    try:
        con = sqlite3.connect(f"file:{reg}?mode=ro", uri=True)
        try:
            return con.execute("SELECT 1 FROM active WHERE store='cells' AND unit=?",
                               (unit,)).fetchone() is not None
        finally:
            con.close()
    except sqlite3.Error:
        return None


def _documents_kept(c: Ctx, ing: dict) -> str:
    """Why the cell's documents must stay, or '' when this ingest may remove
    them: it staged them itself, and no stack on the host serves the cell
    (the requirements dir is shared by every stack's runner)."""
    if ing.get("redo_of"):
        return "a redo's documents belong to the ingest it repeats"
    if not ing.get("documents_staged"):
        return "this ingest did not stage them (they were there before it, or it predates the record)"
    unit = f"{ing['mno']}/{ing['release']}"
    for name, root in [("this stack", c.cfg.registry_root)] + [
            (f"the stack at {r}", r) for r in c.cfg.peer_registry_roots]:
        served = _serves(root, unit)
        if served is None:
            return f"the registry of {name} cannot be read"
        if served:
            return f"{name} serves this cell"
    return ""


def _registry_labels(c: Ctx, prefix: str) -> list[str]:
    """Release-set labels `<prefix><n>` in the registry (n digits only)."""
    reg = Path(c.cfg.registry_root) / "registry.db"
    if not reg.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{reg}?mode=ro", uri=True)
        try:
            rows = [r[0] for r in con.execute("SELECT label FROM release_set")]
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return sorted(r for r in rows if r.startswith(prefix) and r[len(prefix):].isdigit())


def _registry_has(c: Ctx, sql: str, params: tuple) -> bool:
    """One read-only existence query against the registry, if there is one."""
    reg = Path(c.cfg.registry_root) / "registry.db"
    if not reg.is_file():
        return False
    try:
        con = sqlite3.connect(f"file:{reg}?mode=ro", uri=True)
        try:
            return con.execute(sql, params).fetchone() is not None
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _label_taken(c: Ctx, name: str) -> bool:
    """A label is taken when a promoted snapshot or a registry release set
    carries it (labels are immutable). The registry is read if present."""
    if (Path(c.cfg.labels_root) / name).exists():
        return True
    return _registry_has(c, "SELECT 1 FROM release_set WHERE label=?", (name,))


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


def _corpus_bindings(c: Ctx) -> bool:
    """True when the wiring env names a corpus root: the bindings live
    there and a seed build needs none of its own (stage graph, step 2)."""
    return bool(read_env_value(c.cfg.builds_wiring, "CORPUS_DIR"))


def _stack_recipe(c: Ctx) -> str:
    """The recipe the stack serves: `NORA_RECIPE` in its serving env ('' = none)."""
    return read_env_value(c.cfg.stack_env, "NORA_RECIPE")


def _build_recipe(c: Ctx):
    """The recipe frozen in the ingest's build, or None."""
    from core.src.pipeline.recipes import RecipeError, load_frozen
    if not c.build:
        return None
    try:
        return load_frozen(c.cfg.build_dir(c.build))
    except RecipeError as exc:
        raise StageError(str(exc))


def _scheme(c: Ctx) -> str:
    """The prompt scheme: the build's recipe's when it names one, else the
    runner config's. A disagreement is logged once; the recipe wins."""
    r = _build_recipe(c)
    scheme = (r.prompt_scheme if r else "") or c.cfg.prompt_scheme
    if r and r.prompt_scheme and c.cfg.prompt_scheme and r.prompt_scheme != c.cfg.prompt_scheme \
            and not c.notes.get("scheme_logged"):
        c.notes["scheme_logged"] = True
        c.log(f"scheme: recipe {r.name} names {r.prompt_scheme}, the runner config {c.cfg.prompt_scheme}; using the recipe's")
    return scheme


def st_start(c: Ctx) -> None:
    seed = c.db.last_served_build() or ""
    fallback = f"seeding from the configured {c.cfg.seed_build or '-'}"
    if seed and not c.cfg.build_dir(seed).is_dir():
        c.log(f"start: last served build {seed} is gone from the builds root; {fallback}")
        seed = ""
    elif seed and not _corpus_bindings(c) and not (c.cfg.build_dir(seed) / "profiles.json").is_file():
        # a purged or hand-cleaned build can stay on disk without its bindings;
        # cycle.sh start would refuse it for want of bindings
        c.log(f"start: last served build {seed} holds no profiles.json; {fallback}")
        seed = ""
    seed = seed or c.cfg.seed_build
    if not seed:
        raise StageError("no seed build: set seed_build in the runner config for the first ingest")
    c.build = _next_build_id(c)
    c.label = c.build
    c.db.update_ingest(c.ingest["id"], build=c.build, label=c.label)
    recipe = _stack_recipe(c)
    c.cycle("start", c.build, "--seed", seed, *(["--recipe", recipe] if recipe else []))
    c.log(f"start: build {c.build} seeded from {seed}" + (f", recipe {recipe}" if recipe else ""))


def st_parse(c: Ctx) -> None:
    c.cycle("parse", "--cell", c.cell, "--skip-standards")
    c.wait_detached("parse")


def st_prompts(c: Ctx) -> None:
    c.cycle("prompts", "--yes")


def st_taxonomy(c: Ctx) -> None:
    r = _build_recipe(c)
    if r and not r.has("taxonomy"):
        c.log(f"taxonomy: recipe {r.name} runs none; skipped")
        return
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
    if _scheme(c):
        args += ["--scheme", _scheme(c)]
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
    if _scheme(c):
        cmd += ["--prompt-scheme", _scheme(c)]
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


@contextlib.contextmanager
def _stack_lock(c: Ctx, name: str):
    """A stack-wide lock (stage graph step 6) for a short section that two
    ingests must not run at once: the staging gate (one staging store per
    stack) and serving (flip, rollback, release-map advance). A file under
    the work dir, held with flock; every runner worker of the stack uses it."""
    d = Path(c.cfg.work_dir) / ".locks"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{name}.lock", "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            c.log(f"waiting for the stack's {name} lock (another ingest holds it)")
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def st_eval(c: Ctx) -> None:
    with _stack_lock(c, "staging"):
        _eval(c)


def _eval(c: Ctx) -> None:
    stage_env = {"SERVE_RELEASE_SET": c.label}
    c.compose(c.cfg.staging_project, c.cfg.staging_env, "--profile", "serve", "up", "-d", "nora-store",
              extra_env=stage_env, what="staging store up")
    try:
        _wait_healthz(c, _staging_healthz(c))
        cmd = ["python", "-m", "core.src.eval.golden_cli", "--env-dir", CTR_ENV,
               "--stack-url", c.cfg.staging_store_url, "--stack-label", c.label,
               "--stage", "1", "--allow-map-mismatch"]
        if _scheme(c):
            cmd += ["--sira-prompt-scheme", _scheme(c)]
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


def _record_origins(c: Ctx) -> None:
    """Copy where each stage's output for the ingest's cell came from
    (`computed`, `store`, `forced`; the build's STAGES.json) onto the
    ingest row, for the Ingest page. Best effort: a page detail."""
    if not c.build:
        return
    try:
        data = json.loads((c.cfg.build_dir(c.build) / "STAGES.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    key = f"{c.ingest['mno']}/{c.ingest['release']}"
    origins = {stage: e.get("origin", "computed")
               for stage, cells in (data.get("stages") or {}).items()
               for k, e in cells.items() if k == key}
    if origins:
        c.db.update_ingest(c.ingest["id"], stage_origins=json.dumps(origins, sort_keys=True))


def _start_redo(c: Ctx) -> None:
    """A redo of a served cell: bump the cell's generation at the corpus
    root, so every variant stage of that cell gets a new fingerprint and
    is computed again; the corpus prefix is found as before."""
    corpus = read_env_value(c.cfg.builds_wiring, "CORPUS_DIR")
    if not corpus:
        raise StageError("a redo needs the corpus root (CORPUS_DIR in the ingestion wiring env)")
    from core.src.pipeline.generations import bump
    try:
        n = bump(corpus, f"{c.ingest['mno']}/{c.ingest['release']}",
                 by=str(c.job.get("submitted_by") or c.cfg.runner_name),
                 note=f"redo of ingest {str(c.ingest.get('redo_of') or '')[:8]}")
    except (OSError, ValueError) as exc:
        raise StageError(f"redo: generation not bumped: {exc}")
    c.log(f"redo: {c.ingest['mno']}/{c.ingest['release']} is now generation {n}; its variant stages run again")


def run_ingest(c: Ctx) -> None:
    """Run the stages from the start, from `params.retry_from`, or, for a
    redo (`params.redo`), from `start` after bumping the cell's generation."""
    params = c.job.get("params") or {}
    retry_from = params.get("retry_from") or ""
    if params.get("redo"):
        if not c.ingest.get("redo_of"):
            raise StageError("a redo job needs an ingest made by the Redo action")
        _start_redo(c)
        retry_from = "start"
    elif retry_from:
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
        if name in ("parse", "taxonomy", "verify"):
            _record_origins(c)
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


REBASE_ATTEMPTS = 3


def _served_set(ing: dict) -> str:
    """The release set this ingest serves (or would): its rebased set when
    Serve rebased it, else its label."""
    return ing.get("release_set") or ing.get("label") or ""


def _rebase(c: Ctx, set_label: str) -> str:
    """Publish this ingest's units again on the active set (stage graph,
    step 6: another ingest was served since this one was published), gate
    the new set like the first, and return its label. Units are reused by
    fingerprint; History is rebuilt when the active set changed the MNO."""
    ing = c.ingest
    label = ing["label"]
    c.build = ing.get("build") or ""    # the recipe's prompt scheme comes from the build
    active = _active_label(c)
    new = _next_free(c, f"{label}-r", builds=False)
    c.log(f"flip: {set_label} was built on a set that is no longer active ({active or 'none'} is); "
          f"publishing it again on {active or 'none'} as {new}")
    cmd = ["python", "-m", "core.src.stores.publish", "publish", "--serve-root", CTR_SERVE,
           "--label", new, "--cells-src", f"{CTR_WORK}/{label}/cells",
           "--env-dir", f"{CTR_LABELS}/{label}/nora", "--base", active or "none"]
    if _scheme(c):
        cmd += ["--prompt-scheme", _scheme(c)]
    c.batch_oneshot(c.cfg.stack_project, c.cfg.stack_env, "nora-batch", cmd,
                    mounts=_publish_mounts(c), what="publish publish (rebase)")
    c.db.update_ingest(ing["id"], release_set=new)
    ing["release_set"] = new
    c.label = new                       # the set the gate stages and records
    st_eval(c)
    st_gate(c)
    row = c.db.get_ingest(ing["id"]) or {}
    if row.get("verdict") != "pass":
        c.set_state("staged", job_id=c.job_id)
        raise StageError(f"the rebased set {new} did not pass the gate ({row.get('verdict_detail') or '-'}); "
                         "it is staged, not served")
    return new


def run_flip(c: Ctx) -> None:
    with _stack_lock(c, "serve"):
        _flip(c)


def _flip(c: Ctx) -> None:
    ing = c.ingest
    if ing.get("state") != "staged" or ing.get("verdict") != "pass":
        raise StageError(f"ingest is {ing.get('state')} with verdict {ing.get('verdict') or '-'}: not flippable")
    set_label = _served_set(ing)
    for attempt in range(REBASE_ATTEMPTS):
        cmd = ["python", "-m", "core.src.stores.publish", "flip", "--serve-root", CTR_SERVE,
               "--label", set_label, "--store-url", c.cfg.store_url]
        try:
            res = c.batch_oneshot(c.cfg.stack_project, c.cfg.stack_env, "nora-batch", cmd,
                                  mounts=[(c.cfg.registry_root, CTR_SERVE)], what="publish flip")
            break
        except CommandError as exc:
            if not any("PUB-E011" in line for line in exc.result.lines) or attempt == REBASE_ATTEMPTS - 1:
                raise
            set_label = _rebase(c, set_label)
    out = _publish_json(res)
    previous = out.get("from_label") or None
    c.set_state("served", previous_label=previous, flipped_at=now_iso(), job_id=c.job_id)
    c.log(f"flip: {set_label} is active (previous: {previous or '-'})")
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


def _active_label(c: Ctx) -> str:
    """The registry's active release set ('' when unknown, none or mixed)."""
    reg = Path(c.cfg.registry_root) / "registry.db"
    if not reg.is_file():
        return ""
    try:
        con = sqlite3.connect(f"file:{reg}?mode=ro", uri=True)
        try:
            labels = {r[0] for r in con.execute("SELECT DISTINCT label FROM active")}
        finally:
            con.close()
    except sqlite3.Error:
        return ""
    return labels.pop() if len(labels) == 1 else ""


def run_rollback(c: Ctx) -> None:
    with _stack_lock(c, "serve"):
        _rollback(c)


def _rollback(c: Ctx) -> None:
    ing = c.ingest
    if ing.get("state") != "served" or not ing.get("previous_label"):
        raise StageError(f"ingest is {ing.get('state')} (previous label {ing.get('previous_label') or '-'}): nothing to roll back to")
    # A later flip (from this page or by hand) leaves this row `served` while
    # another set is active; rolling back to this row's previous label would
    # drop that newer set as well.
    active = _active_label(c)
    if active and active != _served_set(ing):
        raise StageError(f"{_served_set(ing)} is no longer the active set ({active} is): rolling back to "
                         f"{ing['previous_label']} would drop {active} too; roll back by hand if that is meant")
    cmd = ["python", "-m", "core.src.stores.publish", "rollback", "--serve-root", CTR_SERVE,
           "--to", ing["previous_label"], "--store-url", c.cfg.store_url]
    c.batch_oneshot(c.cfg.stack_project, c.cfg.stack_env, "nora-batch", cmd,
                    mounts=[(c.cfg.registry_root, CTR_SERVE)], what="publish rollback")
    c.set_state("rolled_back", rolled_back_at=now_iso(), job_id=c.job_id)
    c.log(f"rollback: {ing['previous_label']} is active again; the release map still names "
          f"{ing['release']} for {ing['mno']} — golden runs pass --allow-map-mismatch until the next flip")


def _remove_tree(c: Ctx, what: str, path: Path, problems: list[str]) -> None:
    if not path.exists() and not path.is_symlink():
        c.log(f"purge: {what} absent ({path})")
        return
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        problems.append(f"{what} {path}: {exc.strerror or exc}")
        return
    c.log(f"purge: removed {what} ({path})")


def run_purge(c: Ctx) -> None:
    """Remove every artifact of a dead ingest: the cycle baton if it still
    holds it, its release set (never an active one), promoted snapshot, work
    dir, build (and SIRA tree), golden run, and its documents unless the
    active set serves that cell. Each removal is logged; one that fails
    (ownership, a busy mount) fails the job with the list and leaves the
    state unchanged, so the purge is re-run after the fix — every step
    tolerates what is already gone. The ingest row stays, state `purged`."""
    ing = c.ingest
    if ing.get("state") not in PURGEABLE_STATES:
        raise StageError(f"ingest is {ing.get('state')}: only {', '.join(PURGEABLE_STATES)} ingests can be purged"
                         " (abandon a staged one first)")
    build = ing.get("build") or ""
    label = ing.get("label") or ""
    # Purge removes by name. Another row that carries the same label or build
    # and is not itself dead (or purged) owns those artifacts now: refuse before
    # touching anything. The registry's own guard protects only the active set.
    shared = [r for r in c.db.ingests_sharing(ing["id"], label, build)
              if r["state"] not in (*PURGEABLE_STATES, "purged")]
    if shared:
        who = ", ".join(f"{r['id'][:8]} ({r['state']})" for r in shared)
        raise StageError(f"purge refused: label {label or '-'} / build {build or '-'} is also carried by ingest "
                         f"{who}; nothing removed — a row that shares its names with a live ingest is left as it is")
    problems: list[str] = []
    active = c.cfg.active_file
    if build and active.is_file() and active.read_text(encoding="utf-8").strip() == build:
        c.cycle("abandon", "--note", f"self-serve ingest {ing['id']} purged")
        c.log("purge: the cycle baton was this build's; abandoned")
    # the ingest's own set, and every set Serve rebased it into (<label>-r<n>)
    sets = [label] if label else []
    if label:
        sets += _registry_labels(c, f"{label}-r")
    for name in sets:
        if _registry_has(c, "SELECT 1 FROM release_set WHERE label=?", (name,)):
            cmd = ["python", "-m", "core.src.stores.publish", "purge", "--serve-root", CTR_SERVE, "--label", name]
            res = c.batch_oneshot(c.cfg.stack_project, c.cfg.stack_env, "nora-batch", cmd,
                                  mounts=[(c.cfg.registry_root, CTR_SERVE)], what="publish purge")
            c.log(f"purge: release set {name} removed from the registry" + _purge_summary(res))
        elif name == label:
            c.log(f"purge: no release set {label} in the registry")
    if label:
        _remove_tree(c, "promoted snapshot", Path(c.cfg.labels_root) / label, problems)
        _remove_tree(c, "work dir", Path(c.cfg.work_dir) / label, problems)
    if build:
        bdir = c.cfg.build_dir(build)
        sdir = c.cfg.sira_build_dir(build)
        if not sdir.is_relative_to(bdir):
            _remove_tree(c, "SIRA build", sdir, problems)
        _remove_tree(c, "build", bdir, problems)
    if ing.get("run_ref"):
        _remove_tree(c, "golden run", Path(c.cfg.golden_dir) / "runs" / ing["run_ref"], problems)
    cell = Path(c.cfg.requirements_dir) / ing["mno"] / ing["release"]
    if cell.is_dir():
        kept = _documents_kept(c, ing)
        if kept:
            c.log(f"purge: documents kept — {kept}")
        else:
            _remove_tree(c, "documents", cell, problems)
    if problems:
        raise StageError("purge incomplete: " + "; ".join(problems)
                         + " — fix the ownership or remove by hand, then purge again")
    c.set_state("purged", job_id=c.job_id)


def _purge_summary(res: Result) -> str:
    for line in res.lines:
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            return f" ({len(d.get('removed') or [])} orphaned version file(s) removed)"
    try:
        d = json.loads("\n".join(res.lines))
        return f" ({len(d.get('removed') or [])} orphaned version file(s) removed)"
    except ValueError:
        return ""


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
    cell = Path(c.cfg.requirements_dir) / ing["mno"] / ing["release"]
    if c.cfg.abandon_removes_documents and cell.is_dir():
        kept = _documents_kept(c, ing)
        if kept:
            c.log(f"abandon: documents kept — {kept}")
        else:
            shutil.rmtree(cell)
            c.log(f"abandon: removed the staged documents under {cell}")
    c.set_state("abandoned", job_id=c.job_id)
