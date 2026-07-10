#!/usr/bin/env bash
# debug-egress.sh — pinpoint WHY container network egress fails on this host.
# Gathers: IP/subnet inventory, a (bare-metal | host-net | bridge) x
# (TCP connect | TLS handshake) test matrix, and an optional packet capture
# showing who resets the connection. Output goes to a timestamped report file
# to share with IT (contains host/container IPs — internal use only).
#
# Usage:  ./debug-egress.sh [target-host]     # default target: pypi.org
#         sudo ./debug-egress.sh              # sudo enables the tcpdump section
set -uo pipefail
TARGET="${1:-pypi.org}"
OUT="$HOME/egress-debug-$(date +%Y%m%d-%H%M%S).txt"
IMG="python:3.12-slim"

log() { echo "$@" | tee -a "$OUT"; }
section() { log ""; log "===== $* ====="; }

PYTEST='
import socket, ssl, sys, time
host = sys.argv[1]
t0 = time.time()
try:
    ip = socket.gethostbyname(host)
    print(f"DNS       OK   {host} -> {ip}")
except Exception as e:
    print(f"DNS       FAIL {e!r}"); sys.exit(0)
try:
    s = socket.create_connection((host, 443), timeout=10)
    la = s.getsockname()
    print(f"TCP:443   OK   local={la[0]}:{la[1]} ({time.time()-t0:.2f}s)")
except Exception as e:
    print(f"TCP:443   FAIL {e!r}"); sys.exit(0)
try:
    ctx = ssl.create_default_context(); ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    w = ctx.wrap_socket(s, server_hostname=host)
    print(f"TLS       OK   {w.version()} cipher={w.cipher()[0]}")
    w.close()
except Exception as e:
    print(f"TLS       FAIL {e!r}")
'

section "1. IP inventory (share with IT)"
log "host addresses:"
ip -4 -o addr show | awk '{print "  " $2, $4}' | tee -a "$OUT" >/dev/null
log "default route: $(ip route show default | head -1)"
log "docker networks + subnets:"
for n in $(docker network ls -q); do
    docker network inspect "$n" --format '  {{.Name}}: {{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null
done | tee -a "$OUT" >/dev/null
log "daemon.json: $(cat /var/snap/docker/current/config/daemon.json 2>/dev/null || cat /etc/docker/daemon.json 2>/dev/null || echo '(none)')"

section "2. Test matrix vs $TARGET (DNS -> TCP -> TLS; the FAIL layer is the clue)"
log "--- [A] bare-metal (host process) ---"
python3 -c "$PYTEST" "$TARGET" 2>&1 | tee -a "$OUT"
log "--- [B] container, --network=host (host IP, host netns) ---"
docker run --rm --network=host "$IMG" python -c "$PYTEST" "$TARGET" 2>&1 | tee -a "$OUT"
log "--- [C] container, default bridge (pre-NAT source = 172.x container IP) ---"
docker run --rm "$IMG" python -c "$PYTEST" "$TARGET" 2>&1 | tee -a "$OUT"
log "--- [D] control: INTERNAL endpoint from bridge (edit TARGET2 below if wanted) ---"
log "(skipped unless TARGET2 set)"; [ -n "${TARGET2:-}" ] && docker run --rm "$IMG" python -c "$PYTEST" "$TARGET2" 2>&1 | tee -a "$OUT"

section "3. Who sends the RST? (needs sudo; 15s capture during a bridge attempt)"
if [ "$(id -u)" = "0" ] && command -v tcpdump >/dev/null; then
    TIP=$(python3 -c "import socket;print(socket.gethostbyname('$TARGET'))")
    timeout 15 tcpdump -i any -nn "host $TIP and tcp" -c 60 >> "$OUT" 2>&1 &
    TD=$!
    sleep 1
    docker run --rm "$IMG" python -c "$PYTEST" "$TARGET" >/dev/null 2>&1 || true
    wait $TD 2>/dev/null || true
    log "(capture appended — look at WHERE the RST appears: from the remote IP"
    log " after ~RTT = network-side block; instantly/locally = on-host agent)"
else
    log "SKIPPED (run with sudo and tcpdump installed for the capture)"
fi

section "4. Timestamp for IT log correlation"
log "tests ran at: $(date -u +%FT%TZ) (UTC) / $(date) (local)"
echo ""
echo "Report written to: $OUT — share sections 1+2 (+3) with IT."
