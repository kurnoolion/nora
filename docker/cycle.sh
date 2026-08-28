#!/usr/bin/env bash
# cycle.sh — phase-gated driver for one ingest cycle (README "Ingest a new
# release", Phases 0–6). Thin by design: every verb wraps the exact command
# the runbook shows (ingest lane, taxonomy stage, sira_lane, sira_multi
# --verify, promote.sh, serve-push.sh) — nothing is reimplemented and every
# phase stays runnable by hand. What the driver adds:
#
#   * a baton — ONE cycle in flight at a time, recorded in
#     <builds>/ACTIVE + <build>/CYCLE.json (owner, phase log, run name,
#     image ids); `start` refuses while a cycle is neither promoted nor
#     abandoned;
#   * artifact-checked preconditions — each verb checks its predecessor's
#     OUTPUT (parse dirs, taxonomy ledger, verify exit), not just the
#     recorded state, and fails loud with a CYC-E code + one-line fix;
#   * human gates as verbs (`prompts`, `verify-enrich`): evidence, explicit
#     "yes", who confirmed — recorded in CYCLE.json and the CYC block;
#   * CYC compact blocks per phase (counts/codes/digests only, never
#     corpus text) under <build>/reports/CYC-*.txt — paste-safe.
#
# Usage:
#   ./cycle.sh start <build-id> [--profiles <path|prev-build-id>]
#   ./cycle.sh status
#   ./cycle.sh parse        [--force] [--skip-standards]
#   ./cycle.sh prompts                          # gate: per-MNO prompts present + shaped, publish to <build>/prompts/
#   ./cycle.sh taxonomy     [--force]           # re-run = retry of failed docs
#   ./cycle.sh enrich       [--cell <MNO>__<MMMYYYY>] [--retry-failed] [--run-name <r>]
#   ./cycle.sh verify-enrich                    # gate: sira_multi --verify must PASS
#   ./cycle.sh promote --label <label> [--host <serving-host>] [--scheme <s>] [--sira-only]
#   ./cycle.sh abandon      [--note "<why>"]
#
# Long phases (parse, taxonomy, enrich) run DETACHED: the verb returns at
# once with the log path; `status` (and the next verb's precondition)
# reads completion from the log's trailing CYC-EXIT line. Re-run `status`
# until the phase shows ok. Phase 2 (prompt derivation, AI-assisted) is
# outside the driver — `prompts` gates on its result.
#
# Layout (override with env vars): NORA_ROOT=/srv/nora →
#   $NORA_ROOT/builds/{nora,sira}/<build-id>   NORA_BUILDS_ROOT
#   $NORA_ROOT/requirements                    NORA_REQUIREMENTS_DIR
#   $NORA_ROOT/serve                           NORA_SERVE_ROOT
#   $NORA_ROOT/env/.env.builds                 NORA_ENV_ROOT (falls back to docker/)
#
# Not covered (by hand, as before): image snapshot/rebake (Phase 0.1–0.2 —
# architect, rides the next cycle), NORA-native retrieval lanes
# (`./ingest.sh -l nora`), serve-flip (serving host).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

NORA_ROOT="${NORA_ROOT:-/srv/nora}"
BUILDS="${NORA_BUILDS_ROOT:-$NORA_ROOT/builds}"
REQS="${NORA_REQUIREMENTS_DIR:-$NORA_ROOT/requirements}"
SERVE_ROOT="${NORA_SERVE_ROOT:-$NORA_ROOT/serve}"
ENV_ROOT="${NORA_ENV_ROOT:-$NORA_ROOT/env}"
WIRING="$ENV_ROOT/.env.builds"; [ -f "$WIRING" ] || WIRING="$HERE/.env.builds"
ME="$(whoami)"
TS="$(date +%Y%m%d_%H%M%S)"

die() { echo "CYC-E$1: $2" >&2; echo "  fix: $3" >&2; exit 1; }
usage() { sed -n '2,50p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

VERB="${1:-}"; [ -n "$VERB" ] || usage; shift
BUILD="" PROFILES="" FORCE="" SKIPSTD="" CELL="" RETRY=0 RUN="" LABEL="" HOST="" SCHEME="" SIRA_ONLY=0 NOTE=""
while (( $# > 0 )); do
  case "$1" in
    --profiles)      PROFILES="$2"; shift 2;;
    --force)         FORCE="--force"; shift;;
    --skip-standards) SKIPSTD="--skip-standards"; shift;;
    --cell)          CELL="$2"; shift 2;;
    --retry-failed)  RETRY=1; shift;;
    --run-name)      RUN="$2"; shift 2;;
    --label)         LABEL="$2"; shift 2;;
    --host)          HOST="$2"; shift 2;;
    --scheme)        SCHEME="$2"; shift 2;;
    --sira-only)     SIRA_ONLY=1; shift;;
    --note)          NOTE="$2"; shift 2;;
    -h|--help)       usage;;
    -*) echo "unknown option: $1" >&2; exit 2;;
    *) [ -z "$BUILD" ] && BUILD="$1" || { echo "unexpected arg: $1" >&2; exit 2; }; shift;;
  esac
done

# ---- CYCLE.json helpers (python3 on the host: the only JSON writer) ----------
cyc() {  # cyc <op> <cycle.json> [args...]
  python3 - "$@" <<'PY'
import json, sys, datetime, os
op, path, *a = sys.argv[1:]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%FT%TZ")
def load(): return json.load(open(path))
def save(d): json.dump(d, open(path, "w"), indent=2); open(path, "a").write("\n")
if op == "init":
    build, owner, images = a
    save({"build": build, "owner": owner, "status": "open", "opened": now,
          "run_name": build, "images": json.loads(images), "phases": []})
elif op == "phase":            # phase <name> <status> <by> [log]
    d = load(); name, status, by = a[:3]; log = a[3] if len(a) > 3 else ""
    d["phases"].append({"phase": name, "status": status, "by": by, "at": now, "log": log})
    save(d)
elif op == "set":              # set <key> <value>
    d = load(); d[a[0]] = a[1]; save(d)
elif op == "get":
    print(load().get(a[0], ""))
elif op == "last":             # last <phase> -> status of the latest entry for that phase
    d = load(); m = [p for p in d["phases"] if p["phase"] == a[0]]
    print(m[-1]["status"] if m else "")
elif op == "lastlog":
    d = load(); m = [p for p in d["phases"] if p["phase"] == a[0]]
    print(m[-1]["log"] if m else "")
elif op == "summary":
    d = load()
    print(f"build={d['build']} owner={d['owner']} status={d['status']} run_name={d['run_name']} opened={d['opened']}")
    for p in d["phases"]:
        print(f"  {p['at']}  {p['phase']:<14} {p['status']:<10} by={p['by']}" + (f"  log={os.path.basename(p['log'])}" if p['log'] else ""))
PY
}

# ---- baton --------------------------------------------------------------------
ACTIVE="$BUILDS/ACTIVE"
resolve_build() {
  [ -f "$ACTIVE" ] || die 001 "no cycle in flight ($ACTIVE missing)" "./cycle.sh start <build-id>"
  BUILD="$(cat "$ACTIVE")"
  NB="$BUILDS/nora/$BUILD"; SB="$BUILDS/sira/$BUILD"; CJ="$NB/CYCLE.json"
  [ -f "$CJ" ] || die 002 "ACTIVE names $BUILD but $CJ is missing" "restore CYCLE.json or rm $ACTIVE and start over"
  OWNER="$(cyc get "$CJ" owner)"
  [ "$OWNER" = "$ME" ] || echo "NOTE: cycle $BUILD is owned by $OWNER; you ($ME) are acting on it — recorded as such" >&2
  RUN="${RUN:-$(cyc get "$CJ" run_name)}"
}
log_done() {  # log_done <log> -> 0 if the detached job finished rc=0, 1 if running/absent, 2 if failed
  [ -f "$1" ] || return 1
  local l; l="$(grep -o 'CYC-EXIT rc=[0-9]*' "$1" | tail -1 || true)"
  [ -n "$l" ] || return 1
  [ "$l" = "CYC-EXIT rc=0" ] && return 0 || return 2
}
phase_state() {  # phase_state <phase> -> ok | running | failed | none  (artifact-checked via log)
  local st log; st="$(cyc last "$CJ" "$1")"; log="$(cyc lastlog "$CJ" "$1")"
  case "$st" in
    "") echo none;;
    confirmed|ok|promoted) echo ok;;
    abandoned) echo none;;
    *) if [ -n "$log" ]; then
         if log_done "$log"; then echo ok; elif [ $? -eq 2 ]; then echo failed; else echo running; fi
       else echo "$st"; fi;;
  esac
}
require_phase() {  # require_phase <phase> <fix-hint>
  local s; s="$(phase_state "$1")"
  [ "$s" = ok ] || die 010 "phase '$1' is $s (need ok)" "$2"
}
need_wiring() {
  [ -f "$WIRING" ] || die 003 "no builds wiring env ($ENV_ROOT/.env.builds or docker/.env.builds)" \
    "copy a stack env to .env.builds (README Phase 0) — all seven path vars present"
  grep -q "^NORA_ENV_DIR=$NB\$" "$WIRING" && grep -q "^SIRA_DB_ROOT=$SB\$" "$WIRING" \
    || die 004 "wiring env does not point at build $BUILD" "./cycle.sh start rewrote it; check $WIRING for a later manual edit"
}
detach() {  # detach <phase> <service> <log-in-container> <inner command...>
  local phase="$1" svc="$2" clog="$3"; shift 3
  local inner="mkdir -p /data/env/reports && ( $* ) > $clog 2>&1; echo \"CYC-EXIT rc=\$?\" >> $clog"
  ( cd "$HERE" && docker compose --env-file "$WIRING" --profile ingest run -d --rm -T "$svc" sh -c "$inner" >/dev/null )
  local hlog="$NB/reports/$(basename "$clog")"
  cyc phase "$CJ" "$phase" running "$ME" "$hlog"
  echo "$phase: detached — log $hlog"
  echo "  follow: tail -f $hlog      completion: ./cycle.sh status"
}
cyc_block() {  # cyc_block <phase> <status> <kv lines via stdin>
  local f="$NB/reports/CYC-$1-$TS.txt"
  { echo "CYC build=$BUILD phase=$1 op=$ME ts=$(date -u +%FT%TZ) status=$2"; cat; } | tee "$f"
  echo "  (saved $f)"
}
cells() { find "$REQS" -mindepth 2 -maxdepth 2 -type d -printf '%P\n' | sort | sed 's#/#__#'; }
cells_by_size() {  # smallest first (doc count) — inspect the small cell before fanning out
  for c in $(cells); do echo "$(find "$REQS/${c%%__*}/${c##*__}" -type f | wc -l) $c"; done | sort -n | cut -d' ' -f2
}
gate() {  # gate <phase> — explicit yes, recorded
  local ans; read -r -p "confirm gate '$1' for $BUILD? type yes: " ans
  [ "$ans" = yes ] || { cyc phase "$CJ" "$1" declined "$ME"; echo "not confirmed (recorded)"; exit 1; }
  cyc phase "$CJ" "$1" confirmed "$ME"
}

# =============================================================================
case "$VERB" in

start)
  [ -n "$BUILD" ] || usage
  echo "$BUILD" | grep -qE '^[A-Za-z0-9][A-Za-z0-9._-]{1,40}$' || die 020 "bad build-id '$BUILD'" "letters/digits/._- only"
  if [ -f "$ACTIVE" ]; then
    prev="$(cat "$ACTIVE")"; pst="$(cyc get "$BUILDS/nora/$prev/CYCLE.json" status 2>/dev/null || echo unknown)"
    [ "$pst" = promoted ] || [ "$pst" = abandoned ] \
      || die 021 "cycle '$prev' is still in flight (status=$pst, owner=$(cyc get "$BUILDS/nora/$prev/CYCLE.json" owner))" \
         "finish it (./cycle.sh promote) or ./cycle.sh abandon — one cycle at a time"
  fi
  NB="$BUILDS/nora/$BUILD"; SB="$BUILDS/sira/$BUILD"; CJ="$NB/CYCLE.json"
  [ ! -e "$NB" ] && [ ! -e "$SB" ] || die 022 "build dirs for $BUILD already exist" "pick a new build-id (builds are never reused)"
  [ -d "$REQS" ] && [ -n "$(cells)" ] || die 023 "no <MNO>/<MMMYYYY> cells under $REQS" "stage the source docs first (README Phase 0.3)"
  [ -f "$WIRING" ] || die 003 "no builds wiring env at $ENV_ROOT/.env.builds (nor docker/)" "README Phase 0: cp <stack env> .env.builds"
  # profile bindings (Phase 0.4): explicit path, a previous build-id, or the last promoted build
  src=""
  if [ -n "$PROFILES" ]; then
    if [ -f "$PROFILES" ]; then src="$PROFILES"; elif [ -f "$BUILDS/nora/$PROFILES/profiles.json" ]; then src="$BUILDS/nora/$PROFILES/profiles.json"; fi
    [ -n "$src" ] || die 024 "--profiles '$PROFILES' is neither a file nor a build-id with profiles.json" "point at a profiles.json"
  fi
  images="{}"
  for i in nora-pipeline sira-batch; do
    id="$(docker image inspect -f '{{.Id}}' "local/$i:dev" 2>/dev/null | cut -c8-19)" || true
    [ -n "$id" ] || die 025 "image local/$i:dev not found" "rebake: docker compose --env-file $WIRING --profile ingest build nora-pipeline sira-batch"
    images="$(printf '%s' "$images" | python3 -c "import json,sys; d=json.load(sys.stdin); d['$i']='$id'; print(json.dumps(d))")"
  done
  mkdir -p "$NB/reports" "$NB/prompts" "$SB"
  [ -n "$src" ] && cp "$src" "$NB/profiles.json"
  cyc init "$CJ" "$BUILD" "$ME" "$images"
  sed -i "s|^NORA_ENV_DIR=.*|NORA_ENV_DIR=$NB|; s|^SIRA_DB_ROOT=.*|SIRA_DB_ROOT=$SB|" "$WIRING"
  echo "$BUILD" > "$ACTIVE"
  n=$(cells | wc -l)
  cyc_block start ok <<EOF
cells=$n profiles=$([ -f "$NB/profiles.json" ] && echo present || echo MISSING) wiring=$WIRING
images=$images
EOF
  [ -f "$NB/profiles.json" ] || echo "NOTE: no profiles.json — new cells need bindings before parse (architect-reviewed, README Phase 0.4)"
  echo "next: ./cycle.sh parse"
  ;;

status)
  resolve_build
  cyc summary "$CJ"
  echo "artifact state:"
  for p in parse prompts taxonomy enrich verify-enrich promote; do printf '  %-14s %s\n' "$p" "$(phase_state "$p")"; done
  [ -d "$NB/out/parse" ] && echo "  parse dirs   : $(find "$NB/out/parse" -mindepth 2 -maxdepth 2 -type d | wc -l) (cells staged: $(cells | wc -l))"
  [ -f "$NB/out/taxonomy/extraction_state.json" ] && echo "  taxonomy     : $(python3 -c "import json; d=json.load(open('$NB/out/taxonomy/extraction_state.json'))['docs']; print({s: sum(1 for v in d.values() if v['status']==s) for s in ('ok','failed')})") features=$(ls "$NB"/out/taxonomy/*_features.json 2>/dev/null | wc -l)"
  [ -d "$SB" ] && echo "  sira cells   : $(find "$SB" -mindepth 1 -maxdepth 1 -type d -name '*__*' | wc -l) run_name=$RUN"
  ;;

parse)
  resolve_build; need_wiring
  [ -f "$NB/profiles.json" ] || die 030 "no $NB/profiles.json (PIP-E003 would fail the lane)" "./cycle.sh start ... --profiles <prev-build> or author bindings (README Phase 0.4)"
  [ "$(phase_state parse)" != running ] || die 031 "parse already running" "./cycle.sh status; wait for CYC-EXIT"
  detach parse nora-pipeline "/data/env/reports/lane-ingestion-ALL-$TS.log" \
    "exec python -m core.src.pipeline.run_cli --env-dir /data/env --lane ingestion --skip-taxonomy $FORCE $SKIPSTD"
  ;;

prompts)
  resolve_build
  require_phase parse "./cycle.sh parse, then ./cycle.sh status until parse=ok"
  [ "$(find "$NB/out/parse" -mindepth 2 -maxdepth 2 -type d 2>/dev/null | wc -l)" -ge "$(cells | wc -l)" ] \
    || die 040 "parse dirs < staged cells" "check the parse log for failed cells; re-run ./cycle.sh parse"
  PD="$HERE/../customizations/prompts"
  echo "prompt evidence (per MNO found in parse output):"
  missing=0 bad=0 mnos=0
  for m in $(find "$NB/out/parse" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort); do
    mnos=$((mnos+1))
    doc="$(ls "$PD"/doc_requirement_"$m"_v*.txt 2>/dev/null | sort -V | tail -1 || true)"
    ov="$(ls "$PD"/corpus_overview_"$m"_v*.txt 2>/dev/null | sort -V | tail -1 || true)"
    if [ -z "$doc" ] || [ -z "$ov" ]; then echo "  $m: MISSING (falls back to generic prompts — run the derive playbook, Phase 2)"; missing=$((missing+1)); continue; fi
    shape=ok
    for ph in '{taxonomy_block}' '{requirements}' '{max_n}'; do grep -qF "$ph" "$doc" || shape="missing $ph"; done
    grep -qF '{doc_text}' "$doc" && shape="has {doc_text} (unbatched shape)"
    grep -qE '\{[a-z_]+\}' "$ov" && shape="$shape; overview has placeholders"
    [ "$shape" = ok ] || bad=$((bad+1))
    echo "  $m: doc=$(basename "$doc") overview=$(basename "$ov") shape=$shape"
  done
  (( bad == 0 )) || die 041 "$bad MNO prompt set(s) mis-shaped (see above)" "fix the files under customizations/prompts/ and re-run"
  for f in .env.nora-pipeline .env.sira-batch; do
    ef="$ENV_ROOT/$f"; [ -f "$ef" ] || ef="$HERE/$f"
    [ -f "$ef" ] && grep -qE '^NORA_(TAXONOMY_OVERVIEW|SIRA_DOC_PROMPT)_DIR=/data/env/prompts' "$ef" \
      || echo "  WARN: $f lacks the /data/env/prompts *_DIR setting (README Phase 3) — mounted prompts will be ignored"
  done
  gate prompts
  cp "$PD"/*_v*.txt "$NB/prompts/" 2>/dev/null || true
  cyc_block prompts confirmed <<EOF
mnos=$mnos missing=$missing misshaped=$bad published=$(ls "$NB/prompts" | wc -l) gate=confirmed_by:$ME
EOF
  echo "next: ./cycle.sh taxonomy"
  ;;

taxonomy)
  resolve_build; need_wiring
  require_phase prompts "./cycle.sh prompts (gate) first"
  [ "$(phase_state taxonomy)" != running ] || die 050 "taxonomy already running" "./cycle.sh status"
  detach taxonomy nora-pipeline "/data/env/reports/stage-taxonomy-$TS.log" \
    "exec python -m core.src.pipeline.run_cli --env-dir /data/env --start taxonomy --end taxonomy --no-skip-taxonomy $FORCE"
  echo "  re-running this verb after completion retries only failed docs (README Phase 4)"
  ;;

enrich)
  resolve_build; need_wiring
  require_phase taxonomy "./cycle.sh taxonomy, then status until ok"
  ES="$NB/out/taxonomy/extraction_state.json"
  [ -f "$ES" ] || die 060 "no taxonomy ledger at $ES" "./cycle.sh taxonomy"
  failed="$(python3 -c "import json; d=json.load(open('$ES'))['docs']; print(sum(1 for v in d.values() if v['status']=='failed'))")"
  [ "$failed" = 0 ] || die 061 "taxonomy ledger has $failed failed doc(s)" "./cycle.sh taxonomy again (retries only failures); tax_debug for persistent ones"
  tl="$(cyc lastlog "$CJ" taxonomy)"; [ -z "$tl" ] || ! grep -q TAX-W003 "$tl" \
    || die 062 "TAX-W003 present in taxonomy log (overview not attached)" "check NORA_TAXONOMY_OVERVIEW_DIR + <build>/prompts/ contents"
  [ "$(phase_state enrich)" != running ] || die 063 "enrich already running" "./cycle.sh status"
  cyc set "$CJ" run_name "$RUN"
  extra=""; (( RETRY )) && extra="--retry-failed --max-reqs 1"
  if [ -n "$CELL" ]; then
    [ -d "$REQS/${CELL%%__*}/${CELL##*__}" ] || die 064 "no such cell $CELL under $REQS" "cells: $(cells | tr '\n' ' ')"
    inner="python -m sandbox.sira_lane --env-dir /data/env --db-root /data/db --run-name $RUN --only $CELL --wipe-stale-index $extra --verify"
  else
    order="$(cells_by_size | tr '\n' ' ')"
    echo "enrich: all cells smallest-first: $order"
    inner="for c in $order; do python -m sandbox.sira_lane --env-dir /data/env --db-root /data/db --run-name $RUN --only \$c --wipe-stale-index $extra || exit 1; done; python -m sandbox.sira_multi --verify --db-root /data/db --run-name $RUN"
  fi
  detach enrich sira-batch "/data/env/reports/enrich-${CELL:-ALL}-$TS.log" "$inner"
  echo "  resume/retry: same verb (same run name $RUN); one cell: --cell <MNO>__<MMMYYYY>; failures: --retry-failed"
  ;;

verify-enrich)
  resolve_build; need_wiring
  es="$(phase_state enrich)"   # a FAILED enrich (lane verify tripped) may still be inspected here
  [ "$es" = ok ] || [ "$es" = failed ] || die 069 "phase 'enrich' is $es" "./cycle.sh enrich, then status until it finishes"
  out="$NB/reports/verify-enrich-$TS.txt"
  set +e
  ( cd "$HERE" && docker compose --env-file "$WIRING" --profile ingest run --rm -T sira-batch \
      python -m sandbox.sira_multi --verify --db-root /data/db --run-name "$RUN" ) | tee "$out"
  rc=${PIPESTATUS[0]}; set -e
  fails=$(grep -c 'FAIL' "$out" || true); warns=$(grep -c 'WARN' "$out" || true)
  if [ "$rc" != 0 ]; then
    cyc phase "$CJ" verify-enrich failed "$ME" "$out"
    cyc_block verify-enrich fail <<EOF
run_name=$RUN verify_rc=$rc fail_lines=$fails warn_lines=$warns
EOF
    die 070 "verify reports FAIL (rc=$rc)" "triage: sira_enrich_inspect --failed (local-only); then ./cycle.sh enrich --retry-failed [--cell X]"
  fi
  echo "verify PASS (warn lines: $warns). The output above is paste-safe."
  gate verify-enrich
  cyc_block verify-enrich confirmed <<EOF
run_name=$RUN verify_rc=0 warn_lines=$warns gate=confirmed_by:$ME
EOF
  echo "next: ./cycle.sh promote --label <label> [--host <serving-host>]"
  ;;

promote)
  resolve_build
  [ -n "$LABEL" ] || die 080 "promote needs --label <label>" "./cycle.sh promote --label <YYYY-MM-DD-a> [--host H]"
  require_phase verify-enrich "./cycle.sh verify-enrich (gate) first"
  args=(--serve-root "$SERVE_ROOT" --label "$LABEL" --sira-build "$SB")
  (( SIRA_ONLY )) || { [ -d "$NB/out" ] && args+=(--nora-build "$NB"); }
  [ -n "$SCHEME" ] && args+=(--scheme "$SCHEME")
  if [ -f "$SERVE_ROOT/$LABEL/MANIFEST.json" ] && grep -q "\"sira_build\": *\"$SB\"" "$SERVE_ROOT/$LABEL/MANIFEST.json"; then
    echo "label $LABEL already promoted from this build — resuming at push"
  else
    "$HERE/promote.sh" "${args[@]}"
  fi
  pushed="local-only"
  if [ -n "$HOST" ]; then
    "$HERE/serve-push.sh" "$LABEL" "$HOST" --serve-root "$SERVE_ROOT"
    pushed="$HOST"
  fi
  cyc phase "$CJ" promote promoted "$ME"
  cyc set "$CJ" status promoted; cyc set "$CJ" label "$LABEL"
  rm -f "$ACTIVE"
  cyc_block promote ok <<EOF
label=$LABEL pushed_to=$pushed scheme=${SCHEME:--} sira_only=$SIRA_ONLY run_name=$RUN
EOF
  echo "cycle $BUILD closed (baton free). On the serving host:"
  echo "  ./serve-flip.sh <stack> $LABEL          # staged: secondary stack first"
  ;;

abandon)
  resolve_build
  cyc phase "$CJ" abandon abandoned "$ME"
  cyc set "$CJ" status abandoned; [ -n "$NOTE" ] && cyc set "$CJ" abandon_note "$NOTE"
  rm -f "$ACTIVE"
  cyc_block abandon ok <<EOF
note=${NOTE:--} (build dirs kept on disk; GC by hand)
EOF
  ;;

*) echo "unknown verb: $VERB" >&2; usage;;
esac
