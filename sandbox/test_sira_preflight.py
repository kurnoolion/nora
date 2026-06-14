"""Tests for the multi-MNO input-layout pre-flight (multi-mno-sira D-DRAFT-5)."""

from __future__ import annotations

from sandbox.sira_preflight import (
    find_input_release_dirs,
    format_violations,
    main,
    validate_input_layout,
)


def _mk(env_dir, mno, release):
    (env_dir / "input" / mno / release).mkdir(parents=True)


# ── find_input_release_dirs ───────────────────────────────────────

def test_find_release_dirs(tmp_path):
    _mk(tmp_path, "VZW", "Feb2026")
    _mk(tmp_path, "VZW", "Oct2025")
    _mk(tmp_path, "TMO", "Jan2026")
    found = find_input_release_dirs(tmp_path)
    assert {(m, r) for _p, m, r in found} == {
        ("VZW", "Feb2026"), ("VZW", "Oct2025"), ("TMO", "Jan2026"),
    }


def test_find_release_dirs_no_input(tmp_path):
    assert find_input_release_dirs(tmp_path) == []


# ── validate_input_layout ─────────────────────────────────────────

def test_all_conforming(tmp_path):
    _mk(tmp_path, "VZW", "Feb2026")
    _mk(tmp_path, "TMO", "Jan2026")
    assert validate_input_layout(tmp_path) == []


def test_flags_non_mmmyyyy(tmp_path):
    _mk(tmp_path, "VZW", "Feb2026")
    _mk(tmp_path, "VZW", "OA-baseline")
    _mk(tmp_path, "TMO", "Q1-2026")
    violations = validate_input_layout(tmp_path)
    assert ("VZW", "OA-baseline") in violations
    assert ("TMO", "Q1-2026") in violations
    assert ("VZW", "Feb2026") not in violations


def test_format_violations_actionable():
    msg = format_violations([("VZW", "OA-baseline")])
    assert "OA-baseline" in msg
    assert "MMMYYYY" in msg
    assert "input/VZW/OA-baseline" in msg
    assert "rename" in msg.lower()


# ── CLI ───────────────────────────────────────────────────────────

def test_main_ok(tmp_path, capsys):
    _mk(tmp_path, "VZW", "Feb2026")
    rc = main(["--env-dir", str(tmp_path)])
    assert rc == 0
    assert "all MMMYYYY-conforming" in capsys.readouterr().out


def test_main_fails_loud(tmp_path, capsys):
    _mk(tmp_path, "VZW", "OA-baseline")
    rc = main(["--env-dir", str(tmp_path)])
    assert rc == 1
    assert "OA-baseline" in capsys.readouterr().err


def test_main_no_input_dirs(tmp_path, capsys):
    rc = main(["--env-dir", str(tmp_path)])
    assert rc == 0
    assert "no input" in capsys.readouterr().out
