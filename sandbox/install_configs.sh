#!/usr/bin/env bash
# Install the NORA-specific SIRA hydra configs + prompts into the
# cloned upstream SIRA repo, and apply any patches under
# sandbox/sira_patches/. The clone (`sandbox/sira/`) is gitignored, so
# we don't commit anything inside it — this script copies our committed
# configs in on demand.
#
# Run from the repo root (after `git clone … sandbox/sira` succeeded):
#
#   bash sandbox/install_configs.sh
#
# Idempotent:
#   - File copies always overwrite (configs + prompts).
#   - Patches are skipped if their sentinel string is already present
#     in the target file. Re-run after a fresh SIRA clone to reapply.
#
# Re-run after editing any of:
#   sandbox/sira_configs/{data,enrich,rerank}/nora.yaml
#   sandbox/prompts/{doc,query,relevance}_requirement_v*.txt
#   sandbox/enrich_batching.py
#   sandbox/sira_patches/*.patch

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Overridable for out-of-tree clones (docker/prep-offline.sh vendors one).
SIRA_CLONE="${SIRA_CLONE:-$REPO_ROOT/sandbox/sira}"

if [ ! -d "$SIRA_CLONE" ]; then
    echo "error: $SIRA_CLONE not found — run 'git clone --depth 1 https://github.com/facebookresearch/sira.git sandbox/sira' first" >&2
    exit 1
fi

set -x

# Hydra configs — data, enrich, rerank.
cp "$REPO_ROOT/sandbox/sira_configs/data/nora.yaml"   "$SIRA_CLONE/scripts/configs/data/nora.yaml"
cp "$REPO_ROOT/sandbox/sira_configs/enrich/nora.yaml" "$SIRA_CLONE/scripts/configs/enrich/nora.yaml"
cp "$REPO_ROOT/sandbox/sira_configs/rerank/nora.yaml" "$SIRA_CLONE/scripts/configs/rerank/nora.yaml"

# Telecom-tuned prompts referenced by the enrich/rerank configs above.
cp "$REPO_ROOT/sandbox/prompts/doc_requirement_v01.txt"       "$SIRA_CLONE/scripts/configs/enrich/prompts/doc_requirement_v01.txt"
cp "$REPO_ROOT/sandbox/prompts/query_requirement_v01.txt"     "$SIRA_CLONE/scripts/configs/enrich/prompts/query_requirement_v01.txt"
cp "$REPO_ROOT/sandbox/prompts/relevance_requirement_v01.txt" "$SIRA_CLONE/scripts/configs/rerank/prompts/relevance_requirement_v01.txt"

# Batched doc-enrichment logic (strand sira-enrichment-pe) — imported by
# the batched-enrich.patch below. Per-MNO doc prompts are NOT copied:
# the adapter resolves them from $NORA_SIRA_DOC_PROMPT_DIR at run time
# (point it at sandbox/prompts/ or wherever the per-MNO files live).
cp "$REPO_ROOT/sandbox/enrich_batching.py" "$SIRA_CLONE/scripts/enrich_batching.py"

set +x

# ────────────────────────────────────────────────────────────────────
# Patches. Idempotent — each patch carries a unique sentinel string
# that we grep for in its primary target file. If the sentinel is
# present, the patch is assumed already applied and we skip silently.
# Otherwise apply with `git -C $SIRA_CLONE apply`.
# ────────────────────────────────────────────────────────────────────

apply_patch() {
    local patch_file="$1"
    local sentinel="$2"
    local sentinel_file="$3"
    local patch_name
    patch_name="$(basename "$patch_file" .patch)"

    if [ ! -f "$patch_file" ]; then
        echo "  skip $patch_name: patch file not found"
        return 0
    fi

    if grep -q -F "$sentinel" "$SIRA_CLONE/$sentinel_file" 2>/dev/null; then
        echo "  skip $patch_name: already applied (sentinel found in $sentinel_file)"
        return 0
    fi

    if ! git -C "$SIRA_CLONE" apply --check "$patch_file" 2>/dev/null; then
        echo "  error $patch_name: patch does not apply cleanly to $SIRA_CLONE" >&2
        echo "         The SIRA clone may have been edited by hand. To reset:" >&2
        echo "           cd $SIRA_CLONE && git checkout -- scripts/" >&2
        return 1
    fi

    git -C "$SIRA_CLONE" apply "$patch_file"
    echo "  applied $patch_name"
}

echo
echo "Applying patches under sandbox/sira_patches/ …"
apply_patch \
    "$REPO_ROOT/sandbox/sira_patches/per-stage-routing.patch" \
    "NORA_SIRA_ENRICH_LLM_URL" \
    "scripts/add_doc_index_adapter.py"

apply_patch \
    "$REPO_ROOT/sandbox/sira_patches/extra-config-dir.patch" \
    "SIRA_EXTRA_CONFIG_DIR" \
    "scripts/run_pipeline.py"

apply_patch \
    "$REPO_ROOT/sandbox/sira_patches/batched-enrich.patch" \
    "enrich_batching" \
    "scripts/add_doc_index_adapter.py"

echo
echo "OK — installed 3 hydra configs + 3 prompts into $SIRA_CLONE/scripts/configs/"
echo "     plus patches under sandbox/sira_patches/ (idempotent — re-run any time)."
