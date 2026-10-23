#!/usr/bin/env bash
# Start the dashboard backend and frontend.
#
# Deliberately runs UNPRIVILEGED. The API reads telemetry the controller and
# runner already write to disk, which needs no special rights, and it issues no
# OpenFlow requests of its own -- so it cannot disturb a running experiment or
# inflate the controller-message metric the project measures.
#
# Running this as root would let the browser launch experiments, but it would
# also put a root-capable web service on a loopback port. The default is to
# stay unprivileged and print the runner command instead.
#
#   ./dashboard/run_dashboard.sh            # live mode (default)
#   ./dashboard/run_dashboard.sh --mock     # demo mode, synthetic data
#
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${SDNRL_API_PORT:-8000}"
UI_PORT="${SDNRL_UI_PORT:-5173}"

if [[ "${1:-}" == "--mock" ]]; then
  export SDNRL_DASHBOARD_MOCK=1
  echo "==> MOCK MODE: all data will be synthetic and labelled as such."
fi

if [[ "$(id -u)" -eq 0 ]]; then
  cat <<'WARN'
==> WARNING: running as root.
    The dashboard will be able to start Mininet experiments from the browser.
    Only do this on a machine you control, and never expose the ports.
WARN
fi

cleanup() {
  echo
  echo "==> shutting down"
  [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null || true
  [[ -n "${UI_PID:-}" ]] && kill "$UI_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for port in "$API_PORT" "$UI_PORT"; do
  if ss -ltn 2>/dev/null | grep -q ":${port}\b"; then
    echo "ERROR: port ${port} is already in use. Stop the process using it first." >&2
    exit 1
  fi
done

cd "$PROJECT_ROOT"

echo "==> API    http://127.0.0.1:${API_PORT}  (docs at /docs)"
python3 -W ignore::SyntaxWarning -m uvicorn dashboard.backend.app:app \
  --host 127.0.0.1 --port "$API_PORT" &
API_PID=$!

if [[ ! -d dashboard/frontend/node_modules ]]; then
  echo "==> installing frontend dependencies (first run only)"
  (cd dashboard/frontend && npm install --no-audit --no-fund)
fi

echo "==> UI     http://127.0.0.1:${UI_PORT}"
(cd dashboard/frontend && npx vite --host 127.0.0.1 --port "$UI_PORT") &
UI_PID=$!

echo
if [[ -S /run/sdn-rl/launcher.sock ]]; then
  echo "==> Privileged launcher detected: experiments can be started from the browser."
else
  cat <<'HINT'
==> The privileged launcher is not running, so the browser cannot start
    experiments (everything else works). To enable it, in another terminal:

        sudo python3 -m dashboard.launcher.daemon

    Security model and systemd unit: dashboard/launcher/README.md
HINT
fi

echo
echo "==> Open http://127.0.0.1:${UI_PORT} — Ctrl-C to stop both."
wait
