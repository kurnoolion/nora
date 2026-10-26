#!/usr/bin/env bash
# skopeo-pull-bases.sh — fetch the BASE images the docker/*.Dockerfile builds
# need, via skopeo (proxy-friendly), then docker-load each. For hosts where
# `docker pull` fails through the corp proxy but skopeo doesn't — same
# workaround as ~/work/dgx/spark-srv/skopeo-pull-stack.sh, minimal variant.
#
# Base images come from the FROM lines of docker/*.Dockerfile. Build-stage
# aliases are excluded by name — every `FROM x AS name` declares one, and a
# `FROM name` or `FROM name-${ARG}` (an ARG-selected stage) is never a
# registry image.
#
# Usage:
#   ./skopeo-pull-bases.sh              # bases from the Dockerfiles
#   ./skopeo-pull-bases.sh redis:7      # explicit list instead
# Env:
#   TMP=<dir>       scratch for tarballs (default /tmp)
#   MAX_RETRIES=N   per-image retries (default 3)
#   REFRESH=1       re-fetch a base that is already present. The default
#                   skips present images, which is wrong when the point
#                   is a newer build of the same tag (a security re-spin:
#                   `docker build --pull` is what cannot get through the
#                   proxy, so this flag stands in for it).
set -uo pipefail
cd "$(dirname "$0")"
# Default scratch: a NON-HIDDEN dir under $HOME. snap-packaged docker cannot
# read /tmp ("no such file or directory") NOR hidden top-level dirs like
# ~/.cache ("permission denied") — its home interface excludes dotdirs. Any
# visible $HOME path works.
TMP="${TMP:-$HOME/nora-image-tars}"; MAX_RETRIES="${MAX_RETRIES:-3}"; REFRESH="${REFRESH:-0}"
mkdir -p "$TMP"
command -v skopeo >/dev/null || { echo "ERROR: skopeo not installed (sudo apt install skopeo)" >&2; exit 1; }
command -v docker >/dev/null || { echo "ERROR: docker not installed" >&2; exit 1; }

declare -a IMAGES
if (( $# > 0 )); then
  IMAGES=("$@")
else
  # FROM targets minus the stage aliases the same Dockerfiles declare
  # (`FROM x AS alias`), and minus any target with a `$` in it — that is an
  # ARG-selected stage such as `sira-src-${OFFLINE}`, never a registry image.
  mapfile -t ALIASES < <(
    grep -hiE '^FROM .* AS ' ./*.Dockerfile | awk '{print $NF}' | sort -u
  )
  mapfile -t IMAGES < <(
    grep -hE '^FROM ' ./*.Dockerfile | awk '{print $2}' | grep -vF '$' | sort -u \
      | grep -vxF -f <(printf '%s\n' "${ALIASES[@]}") || true
  )
fi
(( ${#IMAGES[@]} > 0 )) || { echo "ERROR: no base images found" >&2; exit 1; }

to_src() {  # normalize to a full registry path (docker.io default)
  local raw="$1" repo tag first
  tag="${raw##*:}"; repo="${raw%:*}"; first="${repo%%/*}"
  if [[ "$repo" != */* ]]; then echo "docker.io/library/$repo:$tag"
  elif [[ "$first" == *.* || "$first" == *:* ]]; then echo "$repo:$tag"
  else echo "docker.io/$repo:$tag"; fi
}

failed=()
for img in "${IMAGES[@]}"; do
  if docker image inspect "$img" >/dev/null 2>&1; then
    if [[ "$REFRESH" == "1" ]]; then echo "REFRESH $img (present; re-fetching)"
    else echo "SKIP $img (already present; REFRESH=1 to re-fetch)"; continue; fi
  fi
  tar="$TMP/$(echo "$img" | tr '/:' '__').tar"
  ok=0
  for ((a=1; a<=MAX_RETRIES; a++)); do
    echo "skopeo copy docker://$(to_src "$img") -> $tar (attempt $a)"
    skopeo copy --src-tls-verify=true "docker://$(to_src "$img")" \
        "docker-archive:${tar}:${img}" && { ok=1; break; }
    sleep $((a*10))
  done
  if (( ok )) && docker load -i "$tar"; then echo "OK $img"; else failed+=("$img"); fi
  rm -f "$tar"
done
if (( ${#failed[@]} > 0 )); then
  printf 'FAILED: %s\n' "${failed[@]}" >&2; exit 1
fi
echo "done — all base images loaded."
