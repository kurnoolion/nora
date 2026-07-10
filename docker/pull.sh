#!/usr/bin/env bash
# Fetch image tarballs from an internal-GitHub release and docker-load them.
# Reassembles multi-part assets (<asset>.tar.gz.partNN, produced by push.sh
# for images over the GHES per-asset size cap) transparently.
#
# Usage:
#   ./pull.sh <release-tag>              # load every image on the release
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

work=$(mktemp -d -p "$HERE"); trap 'rm -rf "$work"' EXIT

# 1. download everything matching the filter
downloaded=0
while read -r aid name; do
    [ -n "$FILTER" ] && case "$name" in *"$FILTER"*) ;; *) continue;; esac
    echo "==> downloading $name"
    curl -sfL "${AUTH[@]}" -H "Accept: application/octet-stream" \
        "$API/releases/assets/$aid" -o "$work/$name"
    downloaded=$((downloaded+1))
done <<< "$assets"
[ "$downloaded" -gt 0 ] || { echo "nothing matched filter '$FILTER'" >&2; exit 1; }

# 2. reassemble split assets, then load every tarball
loaded=0
# multi-part: group <base>.partNN back into <base>
for first in "$work"/*.part00; do
    [ -e "$first" ] || continue
    base="${first%.part00}"
    echo "==> reassembling $(basename "$base") from $(ls "$base".part* | wc -l) parts"
    cat $(ls "$base".part* | sort) > "$base"
    rm -f "$base".part*
done
for tarball in "$work"/*.tar.gz; do
    [ -e "$tarball" ] || continue
    echo "==> docker load < $(basename "$tarball")"
    gunzip -c "$tarball" | docker load
    loaded=$((loaded+1))
done
[ "$loaded" -gt 0 ] || { echo "downloaded assets but none were loadable tarballs" >&2; exit 1; }
echo "done — $loaded image(s) loaded. Set IMAGE_PREFIX/IMAGE_TAG in .env to match."
