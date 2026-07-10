#!/usr/bin/env bash
# Publish built images as Release assets on the internal GitHub repo.
# (GitHub Packages/container registry is disabled on our GHES — D-DRAFT-2.
# Assets = docker-save tarballs; no layer dedup, so the base/app split keeps
# routine pushes small.)
#
# Large images are SPLIT into <asset>.partNN chunks under the GHES per-asset
# size cap (2 GB default) — pull.sh reassembles them transparently.
#
# Usage:
#   ./push.sh <release-tag> <image[:tag]> [<image[:tag]> ...]
#   e.g. ./push.sh images-v1-$(git rev-parse --short HEAD) \
#            local/nora-web:dev local/sira-query:dev
#
# Env:
#   SPLIT_MB=1900   split threshold/chunk size in MB (stay under the asset cap)
#   DRY_RUN=1       save+split into ./push-staging/ and print upload commands,
#                   but do not touch the GHES API (no token needed)
#   TMP=<dir>       scratch dir (default: ./push-staging under docker/)
#
# Reads GHHOST / GHORG / GHREPO / GHTOKEN from ./.env (or the environment).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/.env" ] && set -a && . "$HERE/.env" && set +a
DRY_RUN="${DRY_RUN:-0}"
SPLIT_MB="${SPLIT_MB:-1900}"
TMP="${TMP:-$HERE/push-staging}"
if [ "$DRY_RUN" != "1" ]; then
    : "${GHHOST:?set GHHOST}" "${GHORG:?set GHORG}" "${GHREPO:?set GHREPO}" "${GHTOKEN:?set GHTOKEN}"
fi

TAG="${1:?usage: push.sh <release-tag> <image> [...]}"; shift
[ $# -ge 1 ] || { echo "no images given" >&2; exit 1; }

upload_url=""
if [ "$DRY_RUN" != "1" ]; then
    API="https://$GHHOST/api/v3/repos/$GHORG/$GHREPO"
    AUTH=(-H "Authorization: token $GHTOKEN")
    # Create (or reuse) the release for this tag.
    rel_json=$(curl -sf "${AUTH[@]}" "$API/releases/tags/$TAG" 2>/dev/null) || \
    rel_json=$(curl -sf "${AUTH[@]}" -X POST "$API/releases" \
        -d "{\"tag_name\":\"$TAG\",\"name\":\"$TAG\",\"prerelease\":true,\"body\":\"docker images ($(date -u +%F))\"}")
    upload_url=$(printf '%s' "$rel_json" | python3 -c \
        'import json,sys; print(json.load(sys.stdin)["upload_url"].split("{")[0])')
fi

mkdir -p "$TMP"
[ "$DRY_RUN" = "1" ] || trap 'rm -rf "$TMP"' EXIT

upload_one() {  # <path> — upload a single file as a release asset
    local path="$1" name; name="$(basename "$path")"
    if [ "$DRY_RUN" = "1" ]; then
        echo "DRY: would upload $name ($(du -h "$path" | cut -f1))"
        return 0
    fi
    echo "==> uploading $name"
    curl -sf "${AUTH[@]}" -H "Content-Type: application/gzip" \
        --data-binary @"$path" "$upload_url?name=$name" > /dev/null
}

for img in "$@"; do
    asset="$(echo "$img" | tr '/:' '__').tar.gz"
    echo "==> saving $img -> $asset"
    docker save "$img" | gzip > "$TMP/$asset"
    size_mb=$(( $(stat -c%s "$TMP/$asset") / 1024 / 1024 ))
    if [ "$size_mb" -gt "$SPLIT_MB" ]; then
        echo "    ${size_mb}MB > ${SPLIT_MB}MB — splitting into .partNN chunks"
        split -b "${SPLIT_MB}m" -d --suffix-length=2 "$TMP/$asset" "$TMP/$asset.part"
        rm "$TMP/$asset"
        for part in "$TMP/$asset".part*; do upload_one "$part"; done
    else
        echo "    ${size_mb}MB — single asset"
        upload_one "$TMP/$asset"
    fi
done
if [ "$DRY_RUN" = "1" ]; then
    echo "DRY_RUN done — staged files in $TMP (inspect, then rm -rf it)"
else
    echo "done — release $TAG on $GHORG/$GHREPO now carries: $*"
fi
