"""legacy-refusal.patch: the one-request-per-call enrich path handles a
permanent refusal like the batched path (fallback once, else llm_refused).

The patched SIRA script imports hydra, bm25x and polars, so it is not run
here: the patch is checked for the calls it must make, the helpers it uses
are checked to be reachable through enrich_batching (the only sandbox
module the patched script imports), and, when a SIRA clone is present, the
whole patch chain is applied to a scratch copy and compiled."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import sandbox.enrich_batching as eb

SANDBOX = Path(eb.__file__).parent
PATCH = SANDBOX / "sira_patches" / "legacy-refusal.patch"


def test_patch_routes_refusals_like_the_batched_path():
    text = PATCH.read_text(encoding="utf-8")
    added = "\n".join(l[1:] for l in text.splitlines() if l.startswith("+"))
    assert "NORA legacy-refusal" in added
    assert "enrich_batching.is_permanent_refusal(raw_response, _lr_markers)" in added
    assert 'os.environ.get("NORA_LLM_REFUSAL_MARKERS", "")' in added
    assert '"NORA_SIRA_ENRICH_FALLBACK_LLM_URL"' in added
    assert 'enrich_batching.fallback_reasoning_fields(' in added
    assert '"status": "llm_refused"' in added and '_llm_tag = {"llm": "fallback"}' in added
    # it only touches the one script, after batched-enrich
    assert {l for l in text.splitlines() if l.startswith("+++ ")} == {
        "+++ b/scripts/add_doc_index_adapter.py"}
    install = (SANDBOX / "install_configs.sh").read_text(encoding="utf-8")
    assert install.index("batched-enrich.patch") < install.index("legacy-refusal.patch")


def test_helpers_reach_the_patched_script_through_enrich_batching():
    markers = eb.parse_markers("Blocked by policy||Cannot help")
    assert eb.is_permanent_refusal("Blocked by policy: request denied", markers)
    assert not eb.is_permanent_refusal('Blocked by policy {"phrases": ["a"]}', markers)
    assert not eb.is_permanent_refusal("ordinary answer", markers)


@pytest.mark.skipif(not (SANDBOX / "sira" / ".git").exists() or not shutil.which("git"),
                    reason="no SIRA clone to patch")
def test_the_patch_chain_applies_and_compiles(tmp_path):
    clone = tmp_path / "sira"
    subprocess.run(["git", "-C", str(SANDBOX / "sira"), "worktree", "add", "-q", "--detach",
                    str(clone), "HEAD"], check=True)
    try:
        out = subprocess.run(["bash", str(SANDBOX / "install_configs.sh")], capture_output=True,
                             text=True, env={"SIRA_CLONE": str(clone), "PATH": "/usr/bin:/bin"})
        assert out.returncode == 0, out.stderr
        assert "applied legacy-refusal" in out.stdout
        subprocess.run([sys.executable, "-m", "py_compile",
                        str(clone / "scripts" / "add_doc_index_adapter.py")], check=True)
    finally:
        subprocess.run(["git", "-C", str(SANDBOX / "sira"), "worktree", "remove", "--force",
                        str(clone)], check=False)
