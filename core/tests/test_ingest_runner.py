"""Tests for core/src/ingest_runner — the host-side self-serve ingest runner.

A fake executor stands in for the host: it answers every command the
plan issues and simulates the cycle driver's files (build dir,
CYCLE.json phase entries, detached logs with CYC-EXIT) so the plan's
waits and reads run against real files. The jobs database is the real
one, written through the web's `JobQueue` to pin the contract between
the two sides.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from core.src.ingest_runner import plan
from core.src.ingest_runner import verdict as V
from core.src.ingest_runner.config import ConfigError, RunnerConfig
from core.src.ingest_runner.db import RunnerDB
from core.src.ingest_runner.execute import Result
from core.src.ingest_runner.ingest_runner_cli import main as cli_main
from core.src.ingest_runner.runner import Runner

aiosqlite = pytest.importorskip("aiosqlite")
from core.src.web.jobs import JobQueue  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# -- fixtures --------------------------------------------------------------------

@pytest.fixture()
def host(tmp_path: Path) -> dict:
    root = tmp_path / "srv"
    for d in ("builds/nora", "builds/sira", "requirements", "labels", "registry", "work",
              "golden/runs", "share/OADocs/Rel", "docker", "state"):
        (root / d).mkdir(parents=True)
    (root / "share/OADocs/Rel/spec.pdf").write_bytes(b"%PDF fake")
    (root / "share/OADocs/Rel/notes.txt").write_text("ignored")
    (root / "docker/.env.builds").write_text("NORA_ENV_DIR=/x\nSTORE_ROOT=/y\n")
    (root / "docker/.env.stack").write_text("STORE_PORT=8040\n")
    (root / "docker/.env.stage").write_text("STORE_PORT=8041\n")
    cfg = RunnerConfig.from_dict({
        "jobs_db": str(root / "state/nora_jobs.db"),
        "docker_dir": str(root / "docker"),
        "builds_wiring": str(root / "docker/.env.builds"),
        "requirements_dir": str(root / "requirements"),
        "labels_root": str(root / "labels"),
        "registry_root": str(root / "registry"),
        "work_dir": str(root / "work"),
        "golden_dir": str(root / "golden"),
        "stack_project": "stackp",
        "stack_env": str(root / "docker/.env.stack"),
        "staging_project": "stagep",
        "staging_env": str(root / "docker/.env.stage"),
        "share_mappings": [{"windows": "\\\\SERVER\\OADocs", "host": str(root / "share/OADocs")}],
        "cycle_env": {"NORA_ROOT": str(root)},
        "runner_name": "runner-test",
        "prompt_scheme": "v9",
        "seed_build": "seed0",
        "eval_tolerance": 0.03,
        "heartbeat_seconds": 0.05,
        "detached_poll_seconds": 0.0,
    })
    (root / "builds/nora/seed0/out/parse").mkdir(parents=True)
    q = JobQueue(cfg.jobs_db)
    _run(q.init_db())
    return {"root": root, "cfg": cfg, "q": q}


class Sim:
    """Answers the plan's commands and simulates the cycle driver's files."""

    def __init__(self, root: Path, golden_recall: float = 0.80, fail_phase: str = "",
                 gev_error: str = "") -> None:
        self.root = root
        self.calls: list[tuple[list[str], dict]] = []
        self.golden_recall = golden_recall
        self.fail_phase = fail_phase
        self.gev_error = gev_error
        self.run_id = "20310101T000000-lbl"

    def _cycle(self, args: list[str]) -> Result:
        verb = args[0]
        nb_root = self.root / "builds/nora"
        if verb == "start":
            build = args[1]
            nb = nb_root / build
            (nb / "reports").mkdir(parents=True)
            (nb / "CYCLE.json").write_text(json.dumps({"build": build, "phases": []}))
            (self.root / "builds/ACTIVE").write_text(build)
            return Result(0, [f"CYC build={build} phase=start status=ok"])
        build = (self.root / "builds/ACTIVE").read_text().strip()
        nb = nb_root / build
        if verb in ("parse", "taxonomy", "enrich"):
            log = nb / "reports" / f"{verb}.log"
            rc = 1 if self.fail_phase == verb else 0
            log.write_text(f"{verb} ran\nCYC-EXIT rc={rc}\n")
            cj = json.loads((nb / "CYCLE.json").read_text())
            cj["phases"].append({"phase": verb, "status": "running", "log": str(log)})
            (nb / "CYCLE.json").write_text(json.dumps(cj))
            return Result(0, [f"{verb}: detached — log {log}"])
        if verb == "abandon":
            (self.root / "builds/ACTIVE").unlink()
            return Result(0, ["abandoned"])
        return Result(0, [f"{verb} ok"])

    def _compose(self, args: list[str], env: dict) -> Result:
        text = " ".join(args)
        if "golden_cli" in text:
            rep = {"run_id": self.run_id, "stage1": [{"recall": self.golden_recall}] * 4, "errors": []}
            if self.gev_error:
                rep["errors"].append({"code": self.gev_error})
            d = self.root / "golden/runs" / self.run_id
            d.mkdir(parents=True, exist_ok=True)
            (d / "report.json").write_text(json.dumps(rep))
            return Result(1 if self.gev_error else 0,
                          ["GEV ...", f"Run artifacts: /data/env/eval/golden/runs/{self.run_id}"])
        if "publish flip" in text:
            return Result(0, [json.dumps({"from_label": "old-label", "label": "x"})])
        if "golden_release_cli advance" in text:
            return Result(0, ["s1 -> carried over", "s2 -> REVIEW", "wrote release map v3"])
        return Result(0, ["ok"])

    def run(self, argv, *, env=None, cwd=None, on_line=None, stdin=None) -> Result:
        self.calls.append((list(argv), dict(env or {})))
        if argv[0].endswith("cycle.sh"):
            res = self._cycle(argv[1:])
        elif argv[:2] == ["docker", "compose"]:
            res = self._compose(argv[2:], env or {})
        else:
            res = Result(0, [])
        if on_line:
            for line in res.lines:
                on_line(line)
        return res

    def cmds(self) -> list[str]:
        return [" ".join(a) for a, _ in self.calls]


def _submit_ingest(host, mno="MNO-A", release="Apr2031", source="\\\\SERVER\\OADocs\\Rel",
                   retry_from: str | None = None, ingest_id: str | None = None):
    q = host["q"]
    if ingest_id is None:
        ing = _run(q.create_ingest(mno=mno, release=release, source=source, submitted_by="t"))
        ingest_id = ing.id
    params = {"ingest_id": ingest_id}
    if retry_from:
        params["retry_from"] = retry_from
    job = _run(q.submit("ingest", "t", params=params))
    _run(q.update_ingest(ingest_id, job_id=job.id))
    return job.id, ingest_id


def _runner(host, sim: Sim) -> Runner:
    return Runner(host["cfg"], db=RunnerDB(host["cfg"].jobs_db), ex=sim, sleep=lambda s: None,
                  healthz=lambda url: True)


# -- config ----------------------------------------------------------------------------

def test_config_requires_the_paths():
    with pytest.raises(ConfigError, match="jobs_db"):
        RunnerConfig.from_dict({"docker_dir": "x"})


def test_config_resolves_share_paths(host):
    cfg = host["cfg"]
    root = host["root"]
    assert cfg.resolve_source("\\\\SERVER\\OADocs\\Rel") == root / "share/OADocs/Rel"
    assert cfg.resolve_source("\\\\server\\oadocs\\Rel\\Sub") == root / "share/OADocs/Rel/Sub"
    assert cfg.resolve_source("//SERVER/OADocs/Rel") == root / "share/OADocs/Rel"
    assert cfg.resolve_source(str(root / "share/OADocs/Rel")) == root / "share/OADocs/Rel"
    with pytest.raises(ConfigError):
        cfg.resolve_source("\\\\OTHER\\Share\\x")
    with pytest.raises(ConfigError):
        cfg.resolve_source("/etc")


def test_config_build_roots_follow_cycle_sh_rules(host):
    cfg = host["cfg"]
    root = host["root"]
    assert cfg.nora_builds_root == root / "builds/nora"
    assert cfg.active_file == root / "builds/ACTIVE"
    cfg.cycle_env = {"NORA_BUILDS_NORA_ROOT": "/other/nb"}
    assert cfg.nora_builds_root == Path("/other/nb")
    assert cfg.active_file == Path("/other/nb/ACTIVE")


# -- db --------------------------------------------------------------------------------------

def test_db_claims_oldest_runner_job_only_once(host):
    q = host["q"]
    _run(q.submit("pipeline", "t"))
    j1 = _run(q.submit("ingest", "t", params={"ingest_id": "i1"}))
    j2 = _run(q.submit("ingest-flip", "t", params={"ingest_id": "i1"}))
    db = RunnerDB(host["cfg"].jobs_db)
    a = db.claim_next("r1")
    assert a["id"] == j1.id and a["params"] == {"ingest_id": "i1"}
    assert a["status"] == "running" and a["runner"] == "r1" and a["heartbeat_at"]
    b = db.claim_next("r2")
    assert b["id"] == j2.id
    assert db.claim_next("r1") is None
    # the pipeline job is still submitted — not ours
    assert _run(q.list_jobs(status="submitted"))[0].job_type == "pipeline"


def test_db_heartbeat_logs_and_baseline(host):
    q = host["q"]
    db = RunnerDB(host["cfg"].jobs_db)
    j = _run(q.submit("ingest", "t", params={"ingest_id": "x"}))
    db.heartbeat(j.id)
    assert _run(q.get_meta(j.id)).heartbeat_at
    db.append_log(j.id, "one")
    db.append_log(j.id, "two")
    assert _run(q.get_logs(j.id)) == ["one", "two"]
    assert db.baseline_recall() is None and db.last_served_build() is None
    a = _run(q.create_ingest(mno="M", release="Jan2031", source="s", submitted_by="t"))
    db.update_ingest(a.id, verdict="pass", recall_avg=0.71, build="b1", flipped_at="2031-01-02T00:00:00Z")
    b = _run(q.create_ingest(mno="M", release="Apr2031", source="s", submitted_by="t"))
    db.update_ingest(b.id, verdict="fail", recall_avg=0.40, build="b2")
    assert db.baseline_recall() == 0.71
    assert db.last_served_build() == "b1"


# -- verdict -------------------------------------------------------------------------------

def test_verdict_rule():
    assert V.decide(0.80, None, 0.03, [])[0] == "pass"
    assert V.decide(0.78, 0.80, 0.03, [])[0] == "pass"
    assert V.decide(0.76, 0.80, 0.03, [])[0] == "fail"
    assert V.decide(0.99, 0.80, 0.03, ["GEV-E006"])[0] == "fail"
    assert V.decide(None, 0.80, 0.03, [])[0] == "fail"
    assert V.recall_avg({"stage1": [{"recall": 1.0}, {"recall": 0.5}]}) == 0.75
    assert V.recall_avg({"stage1": []}) is None
    assert V.gev_errors({"errors": [{"code": "GEV-W002"}, {"code": "GEV-E003"}]}) == ["GEV-E003"]


def test_latest_recorded_recall_picks_newest_with_stage1(tmp_path):
    runs = tmp_path / "runs"
    for name, rep in (("20310101T000000-a", {"stage1": [{"recall": 0.5}]}),
                      ("20310102T000000-b", {"stage1": [{"recall": 0.9}]}),
                      ("20310103T000000-c", {"stage1": []})):
        (runs / name).mkdir(parents=True)
        (runs / name / "report.json").write_text(json.dumps(rep))
    assert V.latest_recorded_recall(tmp_path) == (0.9, "20310102T000000-b")
    assert V.latest_recorded_recall(tmp_path / "nope") is None


# -- the ingest plan -------------------------------------------------------------------------

def test_ingest_happy_path(host):
    sim = Sim(host["root"])
    job_id, ingest_id = _submit_ingest(host)
    r = _runner(host, sim)
    assert r.run_once() == "completed"
    q = host["q"]
    job = _run(q.get_meta(job_id))
    assert job.status == "completed" and job.progress == 100 and job.runner == "runner-test"
    ing = _run(q.get_ingest(ingest_id))
    assert ing.state == "staged" and ing.verdict == "pass" and ing.stage is None
    assert ing.build.startswith("Apr2031-MNO-A-") and ing.label == ing.build
    assert ing.run_ref == sim.run_id and ing.recall_avg == 0.80 and ing.baseline is None
    # documents staged (pdf only)
    cell = host["root"] / "requirements/MNO-A/Apr2031"
    assert sorted(p.name for p in cell.iterdir()) == ["spec.pdf"]
    cmds = sim.cmds()
    assert any(c.endswith(f"cycle.sh start {ing.build} --seed seed0") for c in cmds)
    assert any("cycle.sh parse --skip-standards" in c for c in cmds)
    assert any("cycle.sh prompts --yes" in c for c in cmds)
    assert any("cycle.sh enrich --cell MNO-A__Apr2031" in c and "--retry-failed" not in c for c in cmds)
    assert any("cycle.sh verify-enrich --yes" in c for c in cmds)
    assert any(f"cycle.sh promote --label {ing.label} --scheme v9" in c for c in cmds)
    conv_argv = next(a for a, _ in sim.calls if "sandbox.cell_db_convert" in a)
    conv = " ".join(conv_argv)
    assert "--cells MNO-A__Apr2031" in conv and "--verify 25" in conv
    assert "-p" not in conv_argv and "sira-batch" in conv_argv   # builds wiring, no stack project
    pub = next(c for c in cmds if "publish publish" in c)
    assert "-p stackp" in pub and f"--cells-src /data/work/{ing.label}/cells" in pub and "--prompt-scheme v9" in pub
    up = next((a, e) for a, e in sim.calls if "nora-store" in a and "up" in a)
    assert "-p" in up[0] and "stagep" in up[0] and up[1]["SERVE_RELEASE_SET"] == ing.label
    gold = next(c for c in cmds if "golden_cli" in c)
    assert "--stage 1" in gold and "--allow-map-mismatch" in gold and "-p stagep" in gold
    assert any("down" in c and "stagep" in c for c in cmds)
    gate = next(c for c in cmds if "publish gate" in c)
    assert f"--verdict pass --run-ref {sim.run_id}" in gate
    logs = _run(q.get_logs(job_id))
    assert any(l.startswith("[1/12] documents") for l in logs)
    assert any("gate: pass" in l and "seeds it" in l for l in logs)


def test_ingest_fails_at_a_detached_phase_and_retries(host):
    sim = Sim(host["root"], fail_phase="enrich")
    job_id, ingest_id = _submit_ingest(host)
    r = _runner(host, sim)
    assert r.run_once() == "failed"
    q = host["q"]
    ing = _run(q.get_ingest(ingest_id))
    job = _run(q.get_meta(job_id))
    assert ing.state == "failed" and ing.stage == "enrich" and "enrich failed rc=1" in ing.error
    assert job.status == "failed" and job.current_stage == "enrich"
    assert not any("promote" in c for c in sim.cmds())
    # retry from enrich: documents/start/parse are not re-run, enrich gets --retry-failed
    sim.fail_phase = ""
    sim.calls.clear()
    job2, _ = _submit_ingest(host, ingest_id=ingest_id, retry_from="enrich")
    assert r.run_once() == "completed"
    cmds = sim.cmds()
    assert not any("cycle.sh start" in c or "cycle.sh parse" in c for c in cmds)
    assert any("cycle.sh enrich --cell MNO-A__Apr2031 --retry-failed" in c for c in cmds)
    ing = _run(q.get_ingest(ingest_id))
    assert ing.state == "staged" and ing.verdict == "pass" and ing.job_id == job2


def test_ingest_cancelled_between_stages(host):
    sim = Sim(host["root"])
    job_id, ingest_id = _submit_ingest(host)
    db = RunnerDB(host["cfg"].jobs_db)
    orig = sim.run

    def run_and_cancel(argv, **kw):
        res = orig(argv, **kw)
        if argv[0].endswith("cycle.sh") and argv[1] == "parse":
            _run(host["q"].cancel(job_id))
        return res
    sim.run = run_and_cancel
    r = _runner(host, sim)
    assert r.run_once() == "cancelled"
    ing = _run(host["q"].get_ingest(ingest_id))
    assert ing.state == "cancelled"
    assert not any("prompts" in c for c in sim.cmds())
    assert _run(host["q"].get_meta(job_id)).status == "cancelled"
    assert db.job_status(job_id) == "cancelled"


def test_gate_fails_against_baseline_and_blocks_flip(host):
    host["cfg"].baseline_recall = 0.90
    sim = Sim(host["root"], golden_recall=0.70)
    job_id, ingest_id = _submit_ingest(host)
    r = _runner(host, sim)
    assert r.run_once() == "completed"       # the job ran to its end; the verdict is the result
    q = host["q"]
    ing = _run(q.get_ingest(ingest_id))
    assert ing.state == "staged" and ing.verdict == "fail" and ing.baseline == 0.90
    assert "0.70 < baseline 0.90" in ing.verdict_detail
    assert any("--verdict fail" in c for c in sim.cmds())
    flip = _run(q.submit("ingest-flip", "t", params={"ingest_id": ingest_id}))
    assert r.run_once() == "failed"
    assert "not flippable" in _run(q.get_meta(flip.id)).error_message
    assert _run(q.get_ingest(ingest_id)).state == "staged"


def test_gate_fails_on_gev_error(host):
    sim = Sim(host["root"], gev_error="GEV-E004")
    job_id, ingest_id = _submit_ingest(host)
    assert _runner(host, sim).run_once() == "completed"
    ing = _run(host["q"].get_ingest(ingest_id))
    assert ing.verdict == "fail" and "GEV-E004" in ing.verdict_detail


def test_baseline_seeds_from_recorded_run_then_from_passed_ingest(host):
    runs = host["root"] / "golden/runs/20300101T000000-old"
    runs.mkdir(parents=True)
    (runs / "report.json").write_text(json.dumps({"stage1": [{"recall": 0.85}]}))
    sim = Sim(host["root"], golden_recall=0.70)   # 0.70 < 0.85 - 0.03
    job_id, ingest_id = _submit_ingest(host)
    assert _runner(host, sim).run_once() == "completed"
    ing = _run(host["q"].get_ingest(ingest_id))
    assert ing.verdict == "fail" and ing.baseline == 0.85
    assert any("recorded run 20300101T000000-old" in l for l in _run(host["q"].get_logs(job_id)))


def test_flip_rollback_abandon(host):
    sim = Sim(host["root"])
    q = host["q"]
    job_id, ingest_id = _submit_ingest(host)
    r = _runner(host, sim)
    assert r.run_once() == "completed"
    build = _run(q.get_ingest(ingest_id)).build
    # flip
    sim.calls.clear()
    flip = _run(q.submit("ingest-flip", "t", params={"ingest_id": ingest_id}))
    assert r.run_once() == "completed"
    ing = _run(q.get_ingest(ingest_id))
    assert ing.state == "served" and ing.previous_label == "old-label" and ing.flipped_at
    cmds = sim.cmds()
    assert any(f"publish flip --serve-root /data/serve --label {ing.label} --store-url http://nora-store:8040" in c for c in cmds)
    adv = next((a, e) for a, e in sim.calls if "golden_release_cli" in " ".join(a))
    assert "--mno MNO-A --release Apr2031" in " ".join(adv[0]) and "--yes" in adv[0]
    assert adv[1]["NORA_ENV_DIR"] == str(host["root"] / "builds/nora" / build)
    assert any("1 sample(s) flagged" in l for l in _run(q.get_logs(flip.id)))
    assert RunnerDB(host["cfg"].jobs_db).last_served_build() == build
    # a second ingest seeds from the served build
    sim.calls.clear()
    job2, ingest2 = _submit_ingest(host, release="Jul2031")
    assert r.run_once() == "completed"
    assert any(f"--seed {build}" in c for c in sim.cmds())
    # rollback the first
    sim.calls.clear()
    rb = _run(q.submit("ingest-rollback", "t", params={"ingest_id": ingest_id}))
    assert r.run_once() == "completed"
    ing = _run(q.get_ingest(ingest_id))
    assert ing.state == "rolled_back" and ing.rolled_back_at
    assert any("publish rollback --serve-root /data/serve --to old-label" in c for c in sim.cmds())
    # abandon the second (staged) one: cycle in flight for its build → cycle.sh abandon; documents removed
    sim.calls.clear()
    (host["root"] / "builds/ACTIVE").write_text(_run(q.get_ingest(ingest2)).build)
    ab = _run(q.submit("ingest-abandon", "t", params={"ingest_id": ingest2}))
    assert r.run_once() == "completed"
    assert _run(q.get_ingest(ingest2)).state == "abandoned"
    assert any("cycle.sh abandon --note" in c for c in sim.cmds())
    assert not (host["root"] / "requirements/MNO-A/Jul2031").exists()
    assert (host["root"] / "requirements/MNO-A/Apr2031").exists()
    # rollback of a staged ingest is refused
    rb2 = _run(q.submit("ingest-rollback", "t", params={"ingest_id": ingest2}))
    assert r.run_once() == "failed"
    assert "nothing to roll back" in _run(q.get_meta(rb2.id)).error_message


def test_documents_stage_refusals(host):
    sim = Sim(host["root"])
    q = host["q"]
    # an existing non-empty cell
    cell = host["root"] / "requirements/MNO-A/Apr2031"
    cell.mkdir(parents=True)
    (cell / "old.pdf").write_bytes(b"x")
    job_id, ingest_id = _submit_ingest(host)
    assert _runner(host, sim).run_once() == "failed"
    assert "already exists" in _run(q.get_ingest(ingest_id)).error
    assert not sim.calls
    # an unmapped share
    job2, ingest2 = _submit_ingest(host, release="Jul2031", source="\\\\OTHER\\x")
    assert _runner(host, sim).run_once() == "failed"
    assert "not under a mapped share" in _run(q.get_ingest(ingest2)).error
    # no documents
    (host["root"] / "share/OADocs/Empty").mkdir()
    job3, ingest3 = _submit_ingest(host, release="Oct2031", source="\\\\SERVER\\OADocs\\Empty")
    assert _runner(host, sim).run_once() == "failed"
    assert "no documents" in _run(q.get_ingest(ingest3)).error


def test_job_without_ingest_fails_cleanly(host):
    j = _run(host["q"].submit("ingest", "t", params={"ingest_id": "missing"}))
    assert _runner(host, Sim(host["root"])).run_once() == "failed"
    assert "names no ingest" in _run(host["q"].get_meta(j.id)).error_message


def test_jobs_schema_migrates_an_old_database(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE jobs (id TEXT PRIMARY KEY, job_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'submitted', submitted_by TEXT NOT NULL, created_at TEXT NOT NULL,
        started_at TEXT, completed_at TEXT, environment TEXT, stages TEXT NOT NULL DEFAULT '[]',
        current_stage TEXT, query_text TEXT, progress INTEGER NOT NULL DEFAULT 0,
        result_summary TEXT, error_message TEXT)""")
    con.execute("INSERT INTO jobs (id, job_type, submitted_by, created_at) VALUES ('a', 'pipeline', 't', 'now')")
    con.commit()
    con.close()
    RunnerDB(db)
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    assert {"params", "runner", "heartbeat_at"} <= cols
    assert "ingests" in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert con.execute("SELECT status FROM jobs WHERE id='a'").fetchone()[0] == "submitted"


# -- cli ------------------------------------------------------------------------------------------

def test_cli_check_and_once(host, tmp_path, capsys):
    cfgp = tmp_path / "ingest_runner.json"
    data = {k: getattr(host["cfg"], k) for k in RunnerConfig.REQUIRED}
    data["runner_name"] = "cli-test"
    cfgp.write_text(json.dumps(data))
    assert cli_main(["--config", str(cfgp), "--check"]) == 0
    assert "cli-test" in capsys.readouterr().out
    assert cli_main(["--config", str(cfgp), "--once"]) == 0
    assert "no job" in capsys.readouterr().out
    assert cli_main(["--config", str(tmp_path / "nope.json"), "--check"]) == 2


# -- share map from the ingestion wiring env ------------------------------------------------------

def test_share_map_from_wiring_env(host, tmp_path):
    from core.src.ingest_runner.config import parse_share_map, read_env_value
    assert parse_share_map("") == []
    maps = parse_share_map(r"\\SERVER\A=/mnt/a ; //SERVER/B=/mnt/b")
    assert [(m.windows, m.host) for m in maps] == [(r"\\SERVER\A", "/mnt/a"), ("//SERVER/B", "/mnt/b")]
    with pytest.raises(ConfigError):
        parse_share_map(r"\\SERVER\A")
    with pytest.raises(ConfigError):
        parse_share_map(r"C:\x=/mnt/a")
    wiring = host["root"] / "docker/.env.builds"
    wiring.write_text("NORA_ENV_DIR=/x\n# comment\nNORA_SHARE_MAP=\\\\SERVER\\OADocs=/mnt/new;\\\\SERVER\\Extra=/mnt/extra\n")
    assert read_env_value(wiring, "NORA_SHARE_MAP").startswith("\\\\SERVER\\OADocs=/mnt/new")
    assert read_env_value(wiring, "MISSING") == "" and read_env_value(tmp_path / "nope", "X") == ""
    cfgp = tmp_path / "ingest_runner.json"
    data = {k: getattr(host["cfg"], k) for k in RunnerConfig.REQUIRED}
    data["share_mappings"] = [{"windows": "\\\\SERVER\\OADocs", "host": "/mnt/json"},
                              {"windows": "\\\\SERVER\\Keep", "host": "/mnt/keep"}]
    cfgp.write_text(json.dumps(data))
    cfg = RunnerConfig.load(cfgp)
    got = {m.windows.lower(): m.host for m in cfg.share_mappings}
    assert got == {"\\\\server\\oadocs": "/mnt/new", "\\\\server\\extra": "/mnt/extra", "\\\\server\\keep": "/mnt/keep"}
    assert cfg.resolve_source("\\\\SERVER\\OADocs\\Rel") == Path("/mnt/new/Rel")
    # a quoted value in the env file
    wiring.write_text('NORA_SHARE_MAP="\\\\SERVER\\Q=/mnt/q"\n')
    assert RunnerConfig.load(cfgp).resolve_source("\\\\SERVER\\Q\\x") == Path("/mnt/q/x")


# -- webhooks ------------------------------------------------------------------------------------

class FakePost:
    def __init__(self, statuses=(200,)):
        self.statuses = list(statuses)
        self.calls: list[tuple[str, dict, dict[str, str]]] = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, json.loads(body), dict(headers)))
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]


def test_webhook_signature_and_retries(monkeypatch):
    from core.src.ingest_runner import webhook
    post = FakePost([500, 200])
    ok, note = webhook.deliver("http://hooks.example.test/x", {"event": "ingest.staged", "a": 1},
                               secret="s3", post=post, sleep=lambda s: None)
    assert ok and "attempt 2" in note and len(post.calls) == 2
    url, body, headers = post.calls[-1]
    assert body == {"event": "ingest.staged", "a": 1}
    raw = json.dumps(body, sort_keys=True).encode()
    assert headers["X-NORA-Signature"] == webhook.sign("s3", raw) and headers["X-NORA-Event"] == "ingest.staged"
    post = FakePost([404])
    ok, note = webhook.deliver("http://hooks.example.test/x", {"event": "e"}, post=post, sleep=lambda s: None)
    assert not ok and "not retried" in note and len(post.calls) == 1
    post = FakePost([0])
    ok, note = webhook.deliver("http://hooks.example.test/x", {"event": "e"}, post=post, sleep=lambda s: None)
    assert not ok and "3 attempts" in note and len(post.calls) == 3


def test_webhook_host_allowlist():
    from core.src.ingest_runner.config import webhook_host_allowed
    allowed = ["hooks.example.test", ".corp.example.test"]
    assert webhook_host_allowed("https://hooks.example.test/a", allowed)
    assert webhook_host_allowed("http://HOOKS.example.test:8080/a", allowed)
    assert webhook_host_allowed("https://chat.corp.example.test/x", allowed)
    assert webhook_host_allowed("https://corp.example.test/x", allowed)
    assert not webhook_host_allowed("https://evil.example.test/a", allowed)
    assert not webhook_host_allowed("ftp://hooks.example.test/a", allowed)
    assert not webhook_host_allowed("https://hooks.example.test/a", [])


def test_ingest_posts_webhooks_on_state_changes(host):
    host["cfg"].webhook_hosts = ["hooks.example.test"]
    host["cfg"].webhook_secret = "sek"
    q = host["q"]
    ing = _run(q.create_ingest(mno="MNO-A", release="Apr2031", source="\\\\SERVER\\OADocs\\Rel",
                               submitted_by="t", callback_url="https://hooks.example.test/nora"))
    job = _run(q.submit("ingest", "t", params={"ingest_id": ing.id}))
    post = FakePost([200])
    sim = Sim(host["root"])
    r = Runner(host["cfg"], db=RunnerDB(host["cfg"].jobs_db), ex=sim, sleep=lambda s: None,
               healthz=lambda u: True, post=post)
    assert r.run_once() == "completed"
    events = [b["event"] for _, b, _ in post.calls]
    assert events == ["ingest.running", "ingest.staged"]
    staged = post.calls[-1][1]
    assert staged["verdict"] == "pass" and staged["label"].startswith("Apr2031-MNO-A-") and staged["mno"] == "MNO-A"
    assert "X-NORA-Signature" in post.calls[-1][2]
    assert any("webhook: ingest.staged" in l and "delivered" in l for l in _run(q.get_logs(job.id)))
    # flip → served
    flip = _run(q.submit("ingest-flip", "t", params={"ingest_id": ing.id}))
    assert r.run_once() == "completed"
    assert [b["event"] for _, b, _ in post.calls][-1] == "ingest.served"
    assert post.calls[-1][1]["previous_label"] == "old-label"


def test_ingest_webhook_failure_and_unlisted_host(host):
    q = host["q"]
    # a failed ingest posts ingest.failed with the error
    host["cfg"].webhook_hosts = ["hooks.example.test"]
    ing = _run(q.create_ingest(mno="MNO-A", release="Apr2031", source="\\\\SERVER\\OADocs\\Rel",
                               submitted_by="t", callback_url="https://hooks.example.test/nora"))
    job = _run(q.submit("ingest", "t", params={"ingest_id": ing.id}))
    post = FakePost([200])
    sim = Sim(host["root"], fail_phase="parse")
    r = Runner(host["cfg"], db=RunnerDB(host["cfg"].jobs_db), ex=sim, sleep=lambda s: None,
               healthz=lambda u: True, post=post)
    assert r.run_once() == "failed"
    assert [b["event"] for _, b, _ in post.calls] == ["ingest.running", "ingest.failed"]
    assert "parse failed" in post.calls[-1][1]["error"]
    # an unlisted host is never posted to, and the job is unaffected
    ing2 = _run(q.create_ingest(mno="MNO-A", release="Jul2031", source="\\\\SERVER\\OADocs\\Rel",
                                submitted_by="t", callback_url="https://other.example.test/x"))
    job2 = _run(q.submit("ingest", "t", params={"ingest_id": ing2.id}))
    post.calls.clear()
    sim.fail_phase = ""
    (host["root"] / "builds/ACTIVE").unlink()
    assert r.run_once() == "completed"
    assert post.calls == []
    assert any("not an allowed host" in l for l in _run(q.get_logs(job2.id)))


def test_config_defaults_wiring_env_and_check_refuses_template(host, tmp_path, capsys):
    cfg = host["cfg"]
    assert cfg.cycle_env["NORA_BUILDS_WIRING"] == cfg.builds_wiring
    explicit = RunnerConfig.from_dict({**{k: getattr(cfg, k) for k in RunnerConfig.REQUIRED},
                                       "cycle_env": {"NORA_BUILDS_WIRING": "/elsewhere"}})
    assert explicit.cycle_env["NORA_BUILDS_WIRING"] == "/elsewhere"
    assert cfg.template_values() == []
    bad = RunnerConfig.from_dict({**{k: getattr(cfg, k) for k in RunnerConfig.REQUIRED},
                                  "seed_build": "<the build the served set came from>",
                                  "golden_dir": "/path/to/golden",
                                  "cycle_env": {"NORA_ROOT": "/path/to/root"}})
    assert sorted(bad.template_values()) == ["cycle_env", "golden_dir", "seed_build"]
    cfgp = tmp_path / "ingest_runner.json"
    data = {k: getattr(cfg, k) for k in RunnerConfig.REQUIRED}
    data["registry_root"] = "/path/to/serve-root"
    cfgp.write_text(json.dumps(data))
    assert cli_main(["--config", str(cfgp), "--check"]) == 2
    err = capsys.readouterr().err
    assert "IRN-E003" in err and "registry_root" in err
