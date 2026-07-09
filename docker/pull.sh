#!/usr/bin/env bash
# Fetch image tarballs from an internal-GitHub release and docker-load them.
#
# Usage:
#   ./pull.sh <release-tag>              # load every asset on the release
#   ./pull.sh <release-tag> nora-web     # only assets whose name matches
#
# Reads GHHOST / GHORG / GHREPO / GHTOKEN from ./.env (or the environment).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/.env" ] && set -a && . "$HERE/.env" && set +a
: "${GHHOST:?set GHHOST}" "${GHORG:?set GHORG}" "${GHREPO:?set GHREPO}" "${GHTOKEN:?set GHTOKEN}"

TAG="${1:?usage: pull.sh <release-tag> [name-filter]}"
FILTER="${2:-}"

API="https://$GHHOST/api/v3/repos/$GHORG/$GHREPO"
AUTH=(-H "Authorization: token $GHTOKEN")

assets=$(curl -sf "${AUTH[@]}" "$API/releases/tags/$TAG" | python3 -c '
import json, sys
for a in json.load(sys.stdin)["assets"]:
    print(a["id"], a["name"])')
[ -n "$assets" ] || { echo "no assets on release $TAG" >&2; exit 1; }

work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
loaded=0
while read -r aid name; do
    [ -n "$FILTER" ] && case "$name" in *"$FILTER"*) ;; *) continue;; esac
    echo "==> downloading $name"
    curl -sfL "${AUTH[@]}" -H "Accept: application/octet-stream" \
        "$API/releases/assets/$aid" -o "$work/$name"
    echo "==> docker load < $name"
    gunzip -c "$work/$name" | docker load
    loaded=$((loaded+1))
done <<< "$assets"
[ "$loaded" -gt 0 ] || { echo "nothing matched filter '$FILTER'" >&2; exit 1; }
echo "done — $loaded image(s) loaded. Set IMAGE_PREFIX/IMAGE_TAG in .env to match."
