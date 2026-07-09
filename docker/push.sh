#!/usr/bin/env bash
# Publish built images as Release assets on the internal GitHub repo.
# (GitHub Packages/container registry is disabled on our GHES — D-DRAFT'd in
# strand docker-distro. Assets = docker-save tarballs; no layer dedup, so the
# base/app split keeps routine pushes small.)
#
# Usage:
#   ./push.sh <release-tag> <image[:tag]> [<image[:tag]> ...]
#   e.g. ./push.sh images-v1-$(git rev-parse --short HEAD) \
#            local/nora-web:dev local/sira-query:dev
#
# Reads GHHOST / GHORG / GHREPO / GHTOKEN from ./.env (or the environment).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/.env" ] && set -a && . "$HERE/.env" && set +a
: "${GHHOST:?set GHHOST}" "${GHORG:?set GHORG}" "${GHREPO:?set GHREPO}" "${GHTOKEN:?set GHTOKEN}"

TAG="${1:?usage: push.sh <release-tag> <image> [...]}"; shift
[ $# -ge 1 ] || { echo "no images given" >&2; exit 1; }

API="https://$GHHOST/api/v3/repos/$GHORG/$GHREPO"
AUTH=(-H "Authorization: token $GHTOKEN")

# Create (or reuse) the release for this tag.
rel_json=$(curl -sf "${AUTH[@]}" "$API/releases/tags/$TAG" 2>/dev/null) || \
rel_json=$(curl -sf "${AUTH[@]}" -X POST "$API/releases" \
    -d "{\"tag_name\":\"$TAG\",\"name\":\"$TAG\",\"prerelease\":true,\"body\":\"docker images ($(date -u +%F))\"}")
upload_url=$(printf '%s' "$rel_json" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["upload_url"].split("{")[0])')

work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
for img in "$@"; do
    asset="$(echo "$img" | tr '/:' '__').tar.gz"
    echo "==> saving $img -> $asset"
    docker save "$img" | gzip > "$work/$asset"
    ls -lh "$work/$asset"
    echo "==> uploading $asset"
    curl -sf "${AUTH[@]}" -H "Content-Type: application/gzip" \
        --data-binary @"$work/$asset" \
        "$upload_url?name=$asset" > /dev/null
done
echo "done — release $TAG on $GHORG/$GHREPO now carries: $*"
