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


def _own(d: Path) -> Path:
    """A build-held output directory (an empty one counts as absent)."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "x.json").write_text("{}")
    return d


def _stored(root: Path, stage: str, cell: Cell, fp: str) -> Path:
    d = root / stage / cell.mno / cell.release / fp
    d.mkdir(parents=True)
    (d / "x.json").write_text("{}")
    return d


def _record(build: Path, stage: str, cell: Cell, fp: str, origin: str = "store") -> None:
    BuildManifest(build / "STAGES.json").record(stage, f"{cell.mno}/{cell.release}", fp, origin=origin)


def test_build_out_wins(tmp_path):
    build, root = tmp_path / "b", tmp_path / "s"
    own = _own(build / "out" / "parse" / A.mno / A.release)
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
    _own(build / "out" / "graph")
    assert locate(build, "graph") == build / "out" / "graph"


def test_unreadable_manifest_raises_only_when_needed(tmp_path):
    build, root = tmp_path / "b", tmp_path / "s"
    own = _own(build / "out" / "parse" / A.mno / A.release)
    (build / "STAGES.json").write_text("not json")
    assert locate(build, "parse", A, root) == own
    with pytest.raises(PipelineError):
        locate(build, "parse", B, root)


def test_cells_unions_build_and_store(tmp_path):
    build, root = tmp_path / "b", tmp_path / "s"
    _own(build / "out" / "parse" / A.mno / A.release)
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


# -- slice 2: helpers for tools that take a document name --------------------

from core.src.pipeline.locate import cell_of, stage_dirs, stage_files  # noqa: E402


def test_stage_files_flat_then_cells_and_cell_of(tmp_path):
    build, root = tmp_path / "b", tmp_path / "s"
    flat = build / "out" / "extract"
    flat.mkdir(parents=True)
    (flat / "old_ir.json").write_text("{}")
    own = build / "out" / "extract" / A.mno / A.release
    own.mkdir(parents=True)
    (own / "d1_ir.json").write_text("{}")
    entry = _stored(root, "extract", B, "fpE")
    (entry / "d2_ir.json").write_text("{}")
    _record(build, "extract", B, "fpE")
    files = stage_files(build, "extract", "*_ir.json", stages_root=root)
    assert [p.name for p in files] == ["old_ir.json", "d1_ir.json", "d2_ir.json"]
    assert [cell_of(p, "extract") for p in files] == [None, A, B]
    assert stage_files(build, "extract", "*_ir.json", B, root) == [entry / "d2_ir.json"]
    assert stage_files(build, "extract", "*_ir.json", Cell("MNOB", "Apr2031"), root) == []
    assert stage_dirs(build, "extract", root) == [(A, own), (B, entry)]


def test_an_empty_build_dir_does_not_shadow_the_store(tmp_path):
    """A stage may create its cell directory before it finds the output
    stored; the empty directory must not hide the stored output."""
    build, root = tmp_path / "b", tmp_path / "s"
    (build / "out" / "parse" / A.mno / A.release).mkdir(parents=True)
    entry = _stored(root, "parse", A, "fp1")
    _record(build, "parse", A, "fp1")
    assert locate(build, "parse", A, root) == entry
    assert cells(build, "parse", root) == [A]


def test_missing_names_cells_with_no_output_anywhere(tmp_path):
    from core.src.pipeline.locate import missing
    build, root = tmp_path / "b", tmp_path / "s"
    _stored(root, "parse", A, "fp1")
    _record(build, "parse", A, "fp1")
    _record(build, "parse", B, "gone")
    assert missing(build, "parse", root) == [B]
    assert main([str(build), "parse", "--missing", "--stages-root", str(root)]) == 1
    _stored(root, "parse", B, "gone")
    assert missing(build, "parse", root) == []
