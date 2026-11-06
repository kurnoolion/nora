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
  log="$(printf '%s' "$inner" | grep -oE '> /data/env/reports/[^ ]+' | head -1 | sed 's#^> /data/env#'"$envdir"'#')"
  if [ -n "$log" ]; then mkdir -p "$(dirname "$log")"; { echo "fake phase ran"; echo "CYC-EXIT rc=0"; } > "$log"; fi
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
    assert "seed=- seeded=- inherited=-" in r.stdout
    assert "no profiles.json" in r.stdout  # the deliberate NOTE, unchanged
    assert (host["root"] / "builds" / "ACTIVE").read_text().strip() == "b1"
    assert "seed" not in _cycle_json(host, "b1")


# -- start --seed --------------------------------------------------------------

def test_start_seed_copies_outputs_and_inherits_binding(host):
    host["cell"]("MNO-A", "Jan2031")
    host["cell"]("MNO-A", "Apr2031")  # the new release of a known MNO
    prev = _seed_build(host, "b0", [("MNO-A", "Jan2031")],
                       [{"mno": "MNO-A", "release": "Jan2031", "profile": "customizations/profiles/p1.json"}])
    r = host["run"]("start", "b1", "--seed", "b0")
    assert r.returncode == 0, r.stderr
    nb = host["nora"] / "b1"
    # outputs copied, not linked: a different inode, same bytes
    src = prev / "out" / "taxonomy" / "extraction_state.json"
    dst = nb / "out" / "taxonomy" / "extraction_state.json"
    assert dst.read_bytes() == src.read_bytes()
    assert dst.stat().st_ino != src.stat().st_ino
    assert (nb / "out" / "parse" / "MNO-A" / "Jan2031" / "doc_tree.json").exists()
    assert (nb / "out" / "extract" / "MNO-A" / "Jan2031" / "doc_ir.json").exists()
    # the new cell is bound to the MNO's latest bound release's profile
    pb = json.loads((nb / "profiles.json").read_text(encoding="utf-8"))
    added = [b for b in pb["bindings"] if b["release"] == "Apr2031"]
    assert added == [{"mno": "MNO-A", "release": "Apr2031", "profile": "customizations/profiles/p1.json"}]
    assert (nb / "profiles.json").stat().st_mode & 0o444 == 0o444
    # the seed's own profiles.json is untouched
    assert len(json.loads((prev / "profiles.json").read_text())["bindings"]) == 1
    # recorded
    assert _cycle_json(host, "b1")["seed"] == "b0"
    assert "seed=b0 seeded=extract,parse,taxonomy inherited=MNO-A__Apr2031<-Jan2031" in r.stdout


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
    assert not (host["root"] / "builds" / "sira" / "b1").exists()
    assert not (host["root"] / "builds" / "ACTIVE").exists()


def test_start_seed_unknown_build_fails(host):
    host["cell"]("MNO-A", "Jan2031")
    r = host["run"]("start", "b1", "--seed", "nope")
    assert r.returncode == 1
    assert "CYC-E026" in r.stderr
    assert not (host["nora"] / "b1").exists()


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


def test_start_appends_missing_wiring_lines(host):
    """A stack env without STORE_ROOT= (field-found) gets the line appended,
    so need_wiring holds for the next verb."""
    host["cell"]("MNO-A", "Jan2031")
    wiring = host["root"] / "env" / ".env.builds"
    wiring.write_text("NORA_ENV_DIR=/old\nOTHER=1\n", encoding="utf-8")   # no STORE_ROOT line
    r = host["run"]("start", "b1")
    assert r.returncode == 0, r.stderr
    lines = wiring.read_text().splitlines()
    assert lines[0] == f"NORA_ENV_DIR={host['nora'] / 'b1'}" and "OTHER=1" in lines
    assert f"STORE_ROOT={host['root'] / 'builds' / 'sira' / 'b1'}" in lines
    assert sum(1 for l in lines if l.startswith("STORE_ROOT=")) == 1
    # the next verb's need_wiring passes
    r = host["run"]("parse")
    assert r.returncode == 1 and "CYC-E030" in r.stderr   # profiles missing, not wiring (E004)
