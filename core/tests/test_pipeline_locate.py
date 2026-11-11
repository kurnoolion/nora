"""pipeline/locate.py: where a stage's output for a cell is (stage-graph §10c)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.src.pipeline.cells import Cell
from core.src.pipeline.error_codes import PipelineError
from core.src.pipeline.locate import cells, locate, main
from core.src.pipeline.manifest import BuildManifest

REPO = Path(__file__).resolve().parents[2]
A = Cell("MNOA", "Apr2031")
B = Cell("MNOA", "Oct2031")


def _stored(root: Path, stage: str, cell: Cell, fp: str) -> Path:
    d = root / stage / cell.mno / cell.release / fp
    d.mkdir(parents=True)
    (d / "x.json").write_text("{}")
    return d


def _record(build: Path, stage: str, cell: Cell, fp: str, origin: str = "store") -> None:
    BuildManifest(build / "STAGES.json").record(stage, f"{cell.mno}/{cell.release}", fp, origin=origin)


def test_build_out_wins(tmp_path):
    build, root = tmp_path / "b", tmp_path / "s"
    own = build / "out" / "parse" / A.mno / A.release
    own.mkdir(parents=True)
    _stored(root, "parse", A, "fp1")
    _record(build, "parse", A, "fp1")
    assert locate(build, "parse", A, root) == own


def test_falls_back_to_store_entry(tmp_path):
    build, root = tmp_path / "b", tmp_path / "s"
    entry = _stored(root, "parse", A, "fp1")
    _record(build, "parse", A, "fp1")
    assert locate(build, "parse", A, root) == entry


def test_none_without_root_entry_or_stored_output(tmp_path):
    build, root = tmp_path / "b", tmp_path / "s"
    build.mkdir()
    assert locate(build, "parse", A, root) is None              # no manifest
    _record(build, "parse", A, "fp1")
    assert locate(build, "parse", A, root) is None              # store lacks it
    _stored(root, "parse", A, "fp1")
    assert locate(build, "parse", A, None) is None              # no stages root


def test_forced_entry_is_not_read_from_store(tmp_path):
    build, root = tmp_path / "b", tmp_path / "s"
    _stored(root, "parse", A, "fp1")
    _record(build, "parse", A, "fp1", origin="forced")
    assert locate(build, "parse", A, root) is None


def test_stages_root_from_env(tmp_path, monkeypatch):
    build, root = tmp_path / "b", tmp_path / "s"
    entry = _stored(root, "parse", A, "fp1")
    _record(build, "parse", A, "fp1")
    monkeypatch.setenv("NORA_STAGES_ROOT", str(root))
    assert locate(build, "parse", A) == entry


def test_global_stage_reads_build_only(tmp_path):
    build = tmp_path / "b"
    assert locate(build, "graph") is None
    (build / "out" / "graph").mkdir(parents=True)
    assert locate(build, "graph") == build / "out" / "graph"


def test_unreadable_manifest_raises_only_when_needed(tmp_path):
    build, root = tmp_path / "b", tmp_path / "s"
    own = build / "out" / "parse" / A.mno / A.release
    own.mkdir(parents=True)
    (build / "STAGES.json").write_text("not json")
    assert locate(build, "parse", A, root) == own
    with pytest.raises(PipelineError):
        locate(build, "parse", B, root)


def test_cells_unions_build_and_store(tmp_path):
    build, root = tmp_path / "b", tmp_path / "s"
    (build / "out" / "parse" / A.mno / A.release).mkdir(parents=True)
    _stored(root, "parse", B, "fp2")
    _record(build, "parse", B, "fp2")
    _record(build, "parse", Cell("MNOB", "Apr2031"), "missing")   # not in the store
    assert cells(build, "parse", root) == [A, B]
    assert cells(build, "parse", None) == [A]


def test_cli(tmp_path, capsys):
    build, root = tmp_path / "b", tmp_path / "s"
    entry = _stored(root, "parse", A, "fp1")
    _record(build, "parse", A, "fp1")
    assert main([str(build), "parse", "MNOA/Apr2031", "--stages-root", str(root)]) == 0
    assert capsys.readouterr().out.strip() == str(entry)
    assert main([str(build), "parse", "MNOA/Oct2031", "--stages-root", str(root)]) == 1
    assert main([str(build), "parse", "--cells", "--stages-root", str(root)]) == 0
    assert capsys.readouterr().out.split() == ["MNOA/Apr2031"]
    (build / "STAGES.json").write_text("not json")
    assert main([str(build), "parse", "MNOA/Apr2031", "--stages-root", str(root)]) == 2


def test_cli_imports_no_third_party_module(tmp_path):
    """The host runs the CLI with the system python: standard library only."""
    code = ("import sys, json; before=set(sys.modules); "
            "import core.src.pipeline.locate; "
            "bad=[n for n in set(sys.modules)-before if n.split('.')[0] not in sys.stdlib_module_names "
            "and n.split('.')[0] != 'core']; "
            "print(json.dumps(sorted(bad)))")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == []
