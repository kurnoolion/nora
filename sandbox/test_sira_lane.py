"""sira_lane command construction (docker-distro lane model)."""
from __future__ import annotations

import argparse
from pathlib import Path

from sandbox.sira_lane import build_commands


def _args(**kw):
    base = dict(env_dir=Path("/e"), db_root=Path("/d"),
                sira_clone=Path("sandbox/sira"), run_name="enrich-stable",
                only=None, wipe_stale_index=False, wipe_all_derived=False,
                stages="prepare,bm25,enrich_corpus", dry_run=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_two_steps_adapter_then_multi():
    cmds = build_commands(_args())
    assert "sandbox.adapter.nora_to_beir" in cmds[0]
    assert "sandbox.sira_multi" in cmds[1]
    assert "--multi-cell" in cmds[0]


def test_only_reaches_both_steps():
    cmds = build_commands(_args(only="MNOA__Feb2026"))
    assert cmds[0][cmds[0].index("--only") + 1] == "MNOA__Feb2026"
    assert cmds[1][cmds[1].index("--only") + 1] == "MNOA__Feb2026"


def test_wipe_all_takes_precedence_over_stale():
    cmds = build_commands(_args(wipe_all_derived=True, wipe_stale_index=True))
    assert "--wipe-all-derived" in cmds[0] and "--wipe-stale-index" not in cmds[0]
