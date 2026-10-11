#!/usr/bin/env bash
# serve-up.sh — start a serve stack with SERVE_UID/SERVE_GID defaulted to
# the LAUNCHING user, so bind-mount writes land user-owned without anyone
# remembering to set the variables (the compose default stays 0:0 for
# backward compatibility; this launcher is the documented way up).
#
# Usage (global compose flags pass through, e.g. project + env file):
#   ./serve-up.sh -p <stack-project> --env-file <stack-env-file>
#
# Explicit SERVE_UID/SERVE_GID exported before calling win over id -u/-g.
set -euo pipefail
cd "$(dirname "$0")"
export SERVE_UID="${SERVE_UID:-$(id -u)}"
export SERVE_GID="${SERVE_GID:-$(id -g)}"
echo "serve-up: SERVE_UID=$SERVE_UID SERVE_GID=$SERVE_GID (export before calling to override)"
exec docker compose "$@" --profile serve up -d
