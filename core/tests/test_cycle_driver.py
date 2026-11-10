"""Tests for docker/cycle.sh — the phase-gated cycle driver.

The driver shells out to `docker` (image ids at `start`, detached phases
via `docker compose run -d`). A fake `docker` on PATH stands in: it
returns a fixed image id, prints a fake container id for `run -d`, and
writes the phase log the real container would have written, ending with
`CYC-EXIT rc=0`. Everything else (the baton, CYCLE.json, the seed copy,
profile inheritance, the gates) is the real script.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CYCLE = REPO / "docker" / "cycle.sh"

FAKE_DOCKER = r'''#!/usr/bin/env bash
# fake docker for cycle.sh tests
if [ "$1" = image ] && [ "$2" = inspect ]; then
  echo "$5" >> "$NORA_ROOT/inspected.txt"   # the image name the driver asked about
  echo "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"; exit 0
fi
if [ "$1" = compose ]; then
  envfile=""; inner=""; detached=0
  while (( $# > 0 )); do
    case "$1" in
      --env-file) envfile="$2"; shift 2;;
      -d) detached=1; shift;;
      -c) inner="$2"; shift 2;;
      *) shift;;
    esac
  done
  envdir="$(grep '^NORA_ENV_DIR=' "$envfile" | cut -d= -f2-)"
  echo "WEB_STATE_DIR=${WEB_STATE_DIR:-} FEEDBACK_DIR=${FEEDBACK_DIR:-} CORRECTIONS_DIR=${CORRECTIONS_DIR:-}" >> "$NORA_ROOT/compose-env.txt"
  echo "JOB_UID=${JOB_UID:-} JOB_GID=${JOB_GID:-}" >> "$NORA_ROOT/compose-user.txt"
  log="$(printf '%s' "$inner" | grep -oE '> /data/env/reports/[^ ]+' | head -1 | sed 's#^> /data/env#'"$envdir"'#')"
  if [ -n "$log" ]; then mkdir -p "$(dirname "$log")"; { echo "INNER: $inner"; echo "fake phase ran"; echo "CYC-EXIT rc=0"; } > "$log"; fi
  (( detached )) && echo "feedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedface"
  exit 0
fi
echo "fake docker: unexpected args: $*" >&2; exit 1
'''


@pytest.fixture()
def host(tmp_path: Path):
    """A NORA_ROOT layout plus a fake docker on PATH; returns a runner."""
    root = tmp_path / "srv"
    (root / "builds" / "nora").mkdir(parents=True)
    (root / "builds" / "sira").mkdir(parents=True)
    (root / "requirements").mkdir()
    (root / "env").mkdir()
    wiring = root / "env" / ".env.builds"
    wiring.write_text("NORA_ENV_DIR=/nowhere\nSTORE_ROOT=/nowhere\n", encoding="utf-8")
    # the batch env files the prompts gate checks (README Phase 3)
    (root / "env" / ".env.nora-batch").write_text("NORA_TAXONOMY_OVERVIEW_DIR=/data/env/prompts\n", encoding="utf-8")
    (root / "env" / ".env.sira-batch").write_text("NORA_SIRA_DOC_PROMPT_DIR=/data/env/prompts\n", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "docker"
    fake.write_text(FAKE_DOCKER, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["NORA_ROOT"] = str(root)

    def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(CYCLE), *args], cwd=str(tmp_path), env=env,
            input=stdin, capture_output=True, text=True,
        )

    def cell(mno: str, rel: str) -> Path:
        d = root / "requirements" / mno / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
        return d

    return {"root": root, "run": run, "cell": cell, "nora": root / "builds" / "nora"}


def _seed_build(host, build: str, cells: list[tuple[str, str]], bindings: list[dict]) -> Path:
    """A previous build with parse output for `cells` and a profiles.json."""
    nb = host["nora"] / build
    for mno, rel in cells:
        pd = nb / "out" / "parse" / mno / rel
        pd.mkdir(parents=True)
        (pd / "doc_tree.json").write_text('{"tree": 1}', encoding="utf-8")
        ed = nb / "out" / "extract" / mno / rel
        ed.mkdir(parents=True)
        (ed / "doc_ir.json").write_text('{"ir": 1}', encoding="utf-8")
    tx = nb / "out" / "taxonomy"
    tx.mkdir(parents=True)
    (tx / "extraction_state.json").write_text('{"prompt_fp": "x", "docs": {}}', encoding="utf-8")
    (nb / "profiles.json").write_text(
        json.dumps({"bindings": bindings, "default": None}), encoding="utf-8")
    (nb / "CYCLE.json").write_text(json.dumps({
        "build": build, "owner": "someone", "status": "promoted",
        "opened": "2031-01-01T00:00:00Z", "run_name": build, "images": {}, "phases": []}),
        encoding="utf-8")
    return nb


def _cycle_json(host, build: str) -> dict:
    return json.loads((host["nora"] / build / "CYCLE.json").read_text(encoding="utf-8"))


# -- start without --seed: unchanged -----------------------------------------

def test_start_plain_records_no_seed(host):
    host["cell"]("MNO-A", "Jan2031")
    r = host["run"]("start", "b1")
    assert r.returncode == 0, r.stderr
    # the SIRA cells are the build's last stage dir, and the wiring env points there
    sira = host["nora"] / "b1" / "out" / "sira"
    assert sira.is_dir() and not (host["root"] / "builds" / "sira" / "b1").exists()
    env_text = (host["root"] / "env" / ".env.builds").read_text()
    assert f"NORA_BUILD_DIR={host['nora'] / 'b1'}" in env_text and f"STORE_ROOT={sira}" in env_text
    assert "seed=- inherited=-" in r.stdout
    assert "no profiles.json" in r.stdout  # the deliberate NOTE, unchanged
    assert (host["root"] / "builds" / "ACTIVE").read_text().strip() == "b1"
    assert "seed" not in _cycle_json(host, "b1")


# -- start --seed --------------------------------------------------------------

def test_start_seed_inherits_binding_and_copies_nothing(host):
    host["cell"]("MNO-A", "Jan2031")
    host["cell"]("MNO-A", "Apr2031")  # the new release of a known MNO
    prev = _seed_build(host, "b0", [("MNO-A", "Jan2031")],
                       [{"mno": "MNO-A", "release": "Jan2031", "profile": "customizations/profiles/p1.json"}])
    r = host["run"]("start", "b1", "--seed", "b0")
    assert r.returncode == 0, r.stderr
    nb = host["nora"] / "b1"
    # every stage is per cell: nothing of the previous build's out/ is copied
    # (out/sira is the build's own empty SIRA dir, created by start)
    assert sorted(p.name for p in (nb / "out").iterdir()) == ["sira"]
    assert (prev / "out" / "parse" / "MNO-A" / "Jan2031" / "doc_tree.json").exists()
    # the new cell is bound to the MNO's latest bound release's profile
    pb = json.loads((nb / "profiles.json").read_text(encoding="utf-8"))
    added = [b for b in pb["bindings"] if b["release"] == "Apr2031"]
    assert added == [{"mno": "MNO-A", "release": "Apr2031", "profile": "customizations/profiles/p1.json"}]
    assert (nb / "profiles.json").stat().st_mode & 0o444 == 0o444
    # the seed's own profiles.json is untouched
    assert len(json.loads((prev / "profiles.json").read_text())["bindings"]) == 1
    # recorded
    assert _cycle_json(host, "b1")["seed"] == "b0"
    assert "seed=b0 inherited=MNO-A/Apr2031<-Jan2031" in r.stdout


def test_start_seed_picks_latest_release_binding(host):
    host["cell"]("MNO-A", "Jan2031")
    host["cell"]("MNO-A", "Apr2031")
    host["cell"]("MNO-A", "Jul2031")
    _seed_build(host, "b0", [("MNO-A", "Jan2031"), ("MNO-A", "Apr2031")], [
        {"mno": "MNO-A", "release": "Apr2031", "profile": "customizations/profiles/p2.json"},
        {"mno": "MNO-A", "release": "Jan2031", "profile": "customizations/profiles/p1.json"},
    ])
    r = host["run"]("start", "b1", "--seed", "b0")
    assert r.returncode == 0, r.stderr
    pb = json.loads((host["nora"] / "b1" / "profiles.json").read_text(encoding="utf-8"))
    assert [b for b in pb["bindings"] if b["release"] == "Jul2031"][0]["profile"].endswith("p2.json")


def test_start_seed_wildcard_binding_needs_no_inheritance(host):
    host["cell"]("MNO-A", "Jan2031")
    host["cell"]("MNO-A", "Apr2031")
    _seed_build(host, "b0", [("MNO-A", "Jan2031")],
                [{"mno": "MNO-A", "release": "*", "profile": "customizations/profiles/p1.json"}])
    r = host["run"]("start", "b1", "--seed", "b0")
    assert r.returncode == 0, r.stderr
    assert "inherited=-" in r.stdout
    pb = json.loads((host["nora"] / "b1" / "profiles.json").read_text(encoding="utf-8"))
    assert len(pb["bindings"]) == 1


def test_start_seed_new_mno_fails_loud_and_leaves_nothing(host):
    host["cell"]("MNO-A", "Jan2031")
    host["cell"]("MNO-B", "Jan2031")  # a new MNO: no binding to inherit
    _seed_build(host, "b0", [("MNO-A", "Jan2031")],
                [{"mno": "MNO-A", "release": "Jan2031", "profile": "customizations/profiles/p1.json"}])
    r = host["run"]("start", "b1", "--seed", "b0")
    assert r.returncode == 1
    assert "CYC-E027" in r.stderr and "MNO-B" in r.stderr
    assert not (host["nora"] / "b1").exists()
    assert not (host["root"] / "builds" / "ACTIVE").exists()


def test_sira_root_override_keeps_the_old_twin_layout(host):
    host["cell"]("MNO-A", "Jan2031")
    import os
    env_extra = {"NORA_BUILDS_SIRA_ROOT": str(host["root"] / "sira-twins")}
    orig = host["run"]
    import subprocess
    r = subprocess.run(["bash", str(CYCLE), "start", "b1"], cwd=str(host["root"]),
                       env={**os.environ, "PATH": f"{host['root'].parent / 'bin'}:{os.environ['PATH']}",
                            "NORA_ROOT": str(host["root"]), **env_extra},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (host["root"] / "sira-twins" / "b1").is_dir()
    assert not (host["nora"] / "b1" / "out" / "sira").exists()


def test_start_seed_unknown_build_fails(host):
    host["cell"]("MNO-A", "Jan2031")
    r = host["run"]("start", "b1", "--seed", "nope")
    assert r.returncode == 1
    assert "CYC-E026" in r.stderr
    assert not (host["nora"] / "b1").exists()


def test_parse_and_taxonomy_take_a_cell(host):
    """`--cell` narrows the lane to one (MNO, release); the detached log is
    tagged with the cell, and an unknown cell fails loud."""
    host["cell"]("MNO-A", "Jan2031")
    host["cell"]("MNO-A", "Apr2031")
    _seed_build(host, "b0", [("MNO-A", "Jan2031")],
                [{"mno": "MNO-A", "release": "*", "profile": "customizations/profiles/p1.json"}])
    assert host["run"]("start", "b1", "--seed", "b0").returncode == 0
    r = host["run"]("parse", "--cell", "MNO-B/Jan2031")
    assert r.returncode == 1 and "CYC-E032" in r.stderr
    r = host["run"]("parse", "--cell", "MNO-A/Apr2031", "--skip-standards")
    assert r.returncode == 0, r.stderr
    phases = _cycle_json(host, "b1")["phases"]
    assert phases[-1]["phase"] == "parse" and "lane-ingestion-MNO-A-Apr2031-" in phases[-1]["log"]
    # prompts: a partial build (one of two cells parsed) is a NOTE, not a refusal
    (host["nora"] / "b1" / "out" / "parse" / "MNO-A" / "Apr2031").mkdir(parents=True)
    r = host["run"]("prompts", "--yes")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "partial build" in r.stdout
    r = host["run"]("taxonomy", "--cell", "MNO-A/Apr2031")
    assert r.returncode == 0, r.stderr
    phases = _cycle_json(host, "b1")["phases"]
    assert phases[-1]["phase"] == "taxonomy" and "stage-taxonomy-MNO-A-Apr2031-" in phases[-1]["log"]


def test_start_seed_explicit_profiles_wins(host, tmp_path):
    host["cell"]("MNO-A", "Jan2031")
    _seed_build(host, "b0", [("MNO-A", "Jan2031")],
                [{"mno": "MNO-A", "release": "Jan2031", "profile": "customizations/profiles/p1.json"}])
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"bindings": [
        {"mno": "MNO-A", "release": "*", "profile": "customizations/profiles/p9.json"}]}), encoding="utf-8")
    r = host["run"]("start", "b1", "--seed", "b0", "--profiles", str(other))
    assert r.returncode == 0, r.stderr
    pb = json.loads((host["nora"] / "b1" / "profiles.json").read_text(encoding="utf-8"))
    assert pb["bindings"][0]["profile"].endswith("p9.json")


# -- detached phases record the container id ----------------------------------

def test_parse_records_container_id(host):
    host["cell"]("MNO-A", "Jan2031")
    _seed_build(host, "b0", [("MNO-A", "Jan2031")],
                [{"mno": "MNO-A", "release": "Jan2031", "profile": "customizations/profiles/p1.json"}])
    assert host["run"]("start", "b1", "--seed", "b0").returncode == 0
    r = host["run"]("parse")
    assert r.returncode == 0, r.stderr
    assert "container feedfacefeed" in r.stdout
    last = _cycle_json(host, "b1")["phases"][-1]
    assert last["phase"] == "parse" and last["container"].startswith("feedface")
    st = host["run"]("status")
    assert "container=feedfacefeed" in st.stdout
    assert "parse          ok" in st.stdout  # the fake log ends with CYC-EXIT rc=0


# -- gates: --yes --------------------------------------------------------------

def _to_parsed(host):
    host["cell"]("MNO-A", "Jan2031")
    _seed_build(host, "b0", [("MNO-A", "Jan2031")],
                [{"mno": "MNO-A", "release": "Jan2031", "profile": "customizations/profiles/p1.json"}])
    assert host["run"]("start", "b1", "--seed", "b0").returncode == 0
    assert host["run"]("parse").returncode == 0
    # the fake lane wrote only the log; the real one writes the cell's parse dir
    (host["nora"] / "b1" / "out" / "parse" / "MNO-A" / "Jan2031").mkdir(parents=True, exist_ok=True)


def test_prompts_gate_declines_without_yes(host):
    _to_parsed(host)
    r = host["run"]("prompts", stdin="no\n")
    assert r.returncode == 1
    assert "not confirmed" in r.stdout
    assert _cycle_json(host, "b1")["phases"][-1]["status"] == "declined"


def test_prompts_gate_yes_records_auto(host):
    _to_parsed(host)
    r = host["run"]("prompts", "--yes", stdin="")  # nothing on stdin: no prompt may be read
    assert r.returncode == 0, r.stderr + r.stdout
    assert "confirmed by --yes" in r.stdout
    last = _cycle_json(host, "b1")["phases"][-1]
    assert last["phase"] == "prompts" and last["status"] == "confirmed"
    assert last["by"].endswith(" (auto)")
    blocks = list((host["nora"] / "b1" / "reports").glob("CYC-prompts-*.txt"))
    assert len(blocks) == 1
    assert "gate=confirmed_by:" in blocks[0].read_text() and "(auto)" in blocks[0].read_text()


def test_prompts_gate_typed_yes_records_user(host):
    _to_parsed(host)
    r = host["run"]("prompts", stdin="yes\n")
    assert r.returncode == 0, r.stderr + r.stdout
    last = _cycle_json(host, "b1")["phases"][-1]
    assert last["status"] == "confirmed" and not last["by"].endswith("(auto)")


def test_start_writes_the_build_var_and_rewrites_older_lines(host):
    """One line names the build (NORA_BUILD_DIR); an older env's
    NORA_ENV_DIR= line is rewritten to agree, a missing STORE_ROOT= is not
    added (the compose file derives /data/db from the build dir)."""
    host["cell"]("MNO-A", "Jan2031")
    wiring = host["root"] / "env" / ".env.builds"
    wiring.write_text("NORA_ENV_DIR=/old\nOTHER=1\n", encoding="utf-8")   # older layout, no build var
    r = host["run"]("start", "b1")
    assert r.returncode == 0, r.stderr
    lines = wiring.read_text().splitlines()
    assert lines[0] == f"NORA_ENV_DIR={host['nora'] / 'b1'}" and "OTHER=1" in lines
    assert f"NORA_BUILD_DIR={host['nora'] / 'b1'}" in lines
    assert not any(l.startswith("STORE_ROOT=") for l in lines)
    # the next verb's need_wiring passes
    r = host["run"]("parse")
    assert r.returncode == 1 and "CYC-E030" in r.stderr   # profiles missing, not wiring (E004)
    # a hand edit that makes an older line disagree is refused
    wiring.write_text(wiring.read_text().replace(f"NORA_ENV_DIR={host['nora'] / 'b1'}", "NORA_ENV_DIR=/elsewhere"))
    r = host["run"]("parse")
    assert r.returncode == 1 and "CYC-E004" in r.stderr and "disagrees" in r.stderr


def _to_taxonomy_ok(host, cell=("MNO-A", "Jan2031")):
    """Through start, parse, prompts and taxonomy with a clean per-cell ledger."""
    _to_parsed(host)
    assert host["run"]("prompts", "--yes").returncode == 0
    assert host["run"]("taxonomy").returncode == 0
    tx = host["nora"] / "b1" / "out" / "taxonomy" / cell[0] / cell[1]
    tx.mkdir(parents=True, exist_ok=True)
    (tx / "extraction_state.json").write_text(json.dumps({"prompt_fp": "x", "docs": {"d": {"status": "ok"}}}))


def test_enrich_cell_log_name_and_retry_flags(host):
    """The detached log is named with the cell's dashes (no slash in a file
    name); a retry re-queues failed rows batched, and --single adds the
    one-request-per-call mode (D-178) only when asked."""
    _to_taxonomy_ok(host)
    r = host["run"]("enrich", "--cell", "MNO-A/Jan2031")
    assert r.returncode == 0, r.stderr
    log = Path(_cycle_json(host, "b1")["phases"][-1]["log"])
    assert log.name.startswith("enrich-MNO-A-Jan2031-") and "/" not in log.name
    inner = log.read_text().splitlines()[0]
    assert "--only MNO-A/Jan2031" in inner and "--retry-failed" not in inner and "--max-reqs" not in inner
    assert "NORA_SIRA_TAXONOMY_DIR=/data/env/out/taxonomy/MNO-A/Jan2031" in inner
    # retry: batched
    log.write_text("CYC-EXIT rc=1\n")   # the first run failed
    r = host["run"]("enrich", "--cell", "MNO-A/Jan2031", "--retry-failed")
    assert r.returncode == 0, r.stderr
    inner = Path(_cycle_json(host, "b1")["phases"][-1]["log"]).read_text().splitlines()[0]
    assert "--retry-failed" in inner and "--max-reqs" not in inner
    # retry, single-request mode on request
    Path(_cycle_json(host, "b1")["phases"][-1]["log"]).write_text("CYC-EXIT rc=1\n")
    r = host["run"]("enrich", "--cell", "MNO-A/Jan2031", "--retry-failed", "--single")
    assert r.returncode == 0, r.stderr
    inner = Path(_cycle_json(host, "b1")["phases"][-1]["log"]).read_text().splitlines()[0]
    assert "--retry-failed --max-reqs 1" in inner
    # the older spelling of the cell is accepted
    Path(_cycle_json(host, "b1")["phases"][-1]["log"]).write_text("CYC-EXIT rc=1\n")
    r = host["run"]("enrich", "--cell", "MNO-A__Jan2031")
    assert r.returncode == 0, r.stderr
    assert "--only MNO-A/Jan2031" in Path(_cycle_json(host, "b1")["phases"][-1]["log"]).read_text().splitlines()[0]


def test_serving_only_variables_get_a_placeholder_for_one_shots(host):
    """compose interpolates every service: the serving-only :? mounts resolve
    for an ingest one-shot without the wiring env carrying them. A value in
    the wiring env or the shell wins over the placeholder."""
    host["cell"]("MNO-A", "Jan2031")
    _seed_build(host, "b0", [("MNO-A", "Jan2031")],
                [{"mno": "MNO-A", "release": "*", "profile": "customizations/profiles/p1.json"}])
    assert host["run"]("start", "b1", "--seed", "b0").returncode == 0
    r = host["run"]("parse", "--skip-standards")
    assert r.returncode == 0, r.stderr
    seen = (host["root"] / "compose-env.txt").read_text().strip().splitlines()[-1]
    empty = str(CYCLE.parent / "empty")
    assert seen == f"WEB_STATE_DIR={empty} FEEDBACK_DIR={empty} CORRECTIONS_DIR={empty}"
    wiring = host["root"] / "env" / ".env.builds"
    wiring.write_text(wiring.read_text() + "WEB_STATE_DIR=/srv/web-state\n")
    r = host["run"]("parse", "--skip-standards")
    assert r.returncode == 0, r.stderr
    seen = (host["root"] / "compose-env.txt").read_text().strip().splitlines()[-1]
    assert seen == f"WEB_STATE_DIR= FEEDBACK_DIR={empty} CORRECTIONS_DIR={empty}"  # compose reads it from the env file, not the shell


def test_one_shots_run_as_the_caller_unless_a_job_user_is_named(host):
    """JOB_UID/JOB_GID default to the caller's ids; a value in the wiring env
    (or the shell) is left to compose."""
    import os
    host["cell"]("MNO-A", "Jan2031")
    _seed_build(host, "b0", [("MNO-A", "Jan2031")],
                [{"mno": "MNO-A", "release": "*", "profile": "customizations/profiles/p1.json"}])
    assert host["run"]("start", "b1", "--seed", "b0").returncode == 0
    assert host["run"]("parse", "--skip-standards").returncode == 0
    seen = (host["root"] / "compose-user.txt").read_text().strip().splitlines()[-1]
    assert seen == f"JOB_UID={os.getuid()} JOB_GID={os.getgid()}"
    wiring = host["root"] / "env" / ".env.builds"
    wiring.write_text(wiring.read_text() + "JOB_UID=4242\nJOB_GID=4243\n")
    assert host["run"]("parse", "--skip-standards").returncode == 0
    seen = (host["root"] / "compose-user.txt").read_text().strip().splitlines()[-1]
    assert seen == "JOB_UID= JOB_GID="


def test_start_inspects_the_images_the_wiring_env_names(host):
    """The recorded image ids are those of the tag and prefix compose will
    run (IMAGE_TAG / IMAGE_PREFIX in the wiring env), not a fixed `:dev`."""
    host["cell"]("MNO-A", "Jan2031")
    assert host["run"]("start", "b0").returncode == 0
    seen = (host["root"] / "inspected.txt").read_text().split()
    assert seen[-2:] == ["local/nora-batch:dev", "local/sira-batch:dev"]
    wiring = host["root"] / "env" / ".env.builds"
    wiring.write_text(wiring.read_text() + 'IMAGE_TAG="v9"\nIMAGE_PREFIX=reg.example.test/nora\n')
    (host["root"] / "builds" / "ACTIVE").unlink()
    assert host["run"]("start", "b1").returncode == 0
    seen = (host["root"] / "inspected.txt").read_text().split()
    assert seen[-2:] == ["reg.example.test/nora/nora-batch:v9", "reg.example.test/nora/sira-batch:v9"]



# -- stage graph, step 2: the corpus root and the stage store ----------------

def _corpus_wiring(host, bindings: list[dict] | None, stages: bool = True) -> Path:
    """Name a corpus root (and a stage store) in the wiring env."""
    corpus = host["root"] / "corpus"
    corpus.mkdir(exist_ok=True)
    if bindings is not None:
        (corpus / "profiles.json").write_text(
            json.dumps({"bindings": bindings, "default": None}), encoding="utf-8")
    store = host["root"] / "stages"
    if stages:
        store.mkdir(exist_ok=True)
    wiring = host["root"] / "env" / ".env.builds"
    wiring.write_text(wiring.read_text() + f"CORPUS_DIR={corpus}\nSTAGES_ROOT={store}\n", encoding="utf-8")
    return corpus


def test_corpus_start_binds_at_the_corpus_root_and_not_in_the_build(host):
    host["cell"]("MNO-A", "Jan2031")
    host["cell"]("MNO-A", "Apr2031")
    corpus = _corpus_wiring(host, [
        {"mno": "MNO-A", "release": "Jan2031", "profile": "customizations/profiles/p1.json"}])
    # a seed without bindings (a purged remnant) is fine: the corpus binds
    (host["nora"] / "b0").mkdir()
    r = host["run"]("start", "b1", "--seed", "b0")
    assert r.returncode == 0, r.stderr
    assert not (host["nora"] / "b1" / "profiles.json").exists()
    pb = json.loads((corpus / "profiles.json").read_text(encoding="utf-8"))
    assert {"mno": "MNO-A", "release": "Apr2031", "profile": "customizations/profiles/p1.json"} in pb["bindings"]
    assert "profiles=corpus stages=store" in r.stdout
    assert "inherited=MNO-A/Apr2031<-Jan2031" in r.stdout
    assert not list(corpus.glob(".profiles.json.*"))
    # parse checks the corpus bindings, status names them
    r = host["run"]("parse", "--cell", "MNO-A/Apr2031")
    assert r.returncode == 0, r.stderr
    r = host["run"]("status")
    assert f"bindings     : {corpus / 'profiles.json'}" in r.stdout
    assert f"stage store  : {host['root'] / 'stages'}" in r.stdout


def test_corpus_start_leaves_a_covered_corpus_file_alone(host):
    host["cell"]("MNO-A", "Jan2031")
    corpus = _corpus_wiring(host, [{"mno": "MNO-A", "release": "*", "profile": "customizations/profiles/p1.json"}])
    before = (corpus / "profiles.json").read_bytes()
    r = host["run"]("start", "b1")
    assert r.returncode == 0, r.stderr
    assert (corpus / "profiles.json").read_bytes() == before


def test_corpus_start_refuses_profiles_and_missing_roots(host, tmp_path):
    host["cell"]("MNO-A", "Jan2031")
    _corpus_wiring(host, None)
    r = host["run"]("start", "b1")
    assert r.returncode == 1 and "CYC-E029" in r.stderr and "corpus root" in r.stderr
    (host["root"] / "corpus" / "profiles.json").write_text(json.dumps({"bindings": [
        {"mno": "MNO-A", "release": "*", "profile": "customizations/profiles/p1.json"}]}), encoding="utf-8")
    other = tmp_path / "other.json"
    other.write_text("{}", encoding="utf-8")
    r = host["run"]("start", "b1", "--profiles", str(other))
    assert r.returncode == 1 and "CYC-E028" in r.stderr
    (host["root"] / "stages").rmdir()
    r = host["run"]("start", "b1")
    assert r.returncode == 1 and "CYC-E029" in r.stderr and "stage store" in r.stderr
    assert not (host["nora"] / "b1").exists()


def test_corpus_new_mno_fails_loud_and_leaves_the_corpus_file(host):
    host["cell"]("MNO-A", "Jan2031")
    host["cell"]("MNO-B", "Jan2031")
    corpus = _corpus_wiring(host, [{"mno": "MNO-A", "release": "*", "profile": "customizations/profiles/p1.json"}])
    before = (corpus / "profiles.json").read_bytes()
    r = host["run"]("start", "b1")
    assert r.returncode == 1 and "CYC-E027" in r.stderr
    assert (corpus / "profiles.json").read_bytes() == before
    assert not (host["nora"] / "b1").exists()


# -- the prompts gate checks the batch env files compose reads ----------------

def _prompts_ready(host) -> None:
    host["cell"]("MNO-A", "Jan2031")
    _seed_build(host, "b0", [("MNO-A", "Jan2031")],
                [{"mno": "MNO-A", "release": "*", "profile": "customizations/profiles/p1.json"}])
    assert host["run"]("start", "b1", "--seed", "b0").returncode == 0
    assert host["run"]("parse").returncode == 0
    (host["nora"] / "b1" / "out" / "parse" / "MNO-A" / "Jan2031").mkdir(parents=True)


def test_prompts_gate_refuses_a_batch_env_without_the_overview_dir(host):
    _prompts_ready(host)
    (host["root"] / "env" / ".env.nora-batch").write_text("OTHER=1\n", encoding="utf-8")
    r = host["run"]("prompts", "--yes")
    assert r.returncode == 1 and "CYC-E042" in r.stderr
    assert "MISSING: NORA_TAXONOMY_OVERVIEW_DIR" in r.stdout


def test_prompts_gate_reads_the_batch_env_the_wiring_names(host, tmp_path):
    _prompts_ready(host)
    (host["root"] / "env" / ".env.nora-batch").write_text("OTHER=1\n", encoding="utf-8")
    named = tmp_path / "stack-batch.env"
    named.write_text("NORA_TAXONOMY_OVERVIEW_DIR=/data/env/prompts\n", encoding="utf-8")
    wiring = host["root"] / "env" / ".env.builds"
    wiring.write_text(wiring.read_text() + f"BATCH_ENV_FILE={named}\n", encoding="utf-8")
    r = host["run"]("prompts", "--yes")
    assert r.returncode == 0, r.stderr + r.stdout
    assert f"NORA_TAXONOMY_OVERVIEW_DIR: set in {named}" in r.stdout
