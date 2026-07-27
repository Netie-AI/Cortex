#!/usr/bin/env bash
# Bring up Cortex (:8000) + OpenVault (:5000) + AirGPT shell (:8765) for full demos.
set -euo pipefail

CORTEX_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Sibling checkout used by the cloud multi-repo environment
OPENVAULT_ROOT="${OPENVAULT_ROOT:-$(cd "$CORTEX_ROOT/../openvault" 2>/dev/null && pwd || true)}"
if [[ -z "${OPENVAULT_ROOT}" || ! -d "$OPENVAULT_ROOT/OpenMW" ]]; then
  echo "Set OPENVAULT_ROOT to your OpenVault clone (expected sibling ../openvault)." >&2
  exit 1
fi

export PATH="${HOME}/.local/bin:${PATH}"
export CORTEX_URL="${CORTEX_URL:-http://127.0.0.1:8000}"
export OPENVAULT_URL="${OPENVAULT_URL:-http://127.0.0.1:5000}"
export OPENIDE_URL="${OPENIDE_URL:-http://127.0.0.1:8765}"
export PACK="${PACK:-dms}"

LOG_DIR="${LOG_DIR:-$CORTEX_ROOT/demo/logs}"
mkdir -p "$LOG_DIR"

if [[ -f "$CORTEX_ROOT/env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$CORTEX_ROOT/env.local"
  set +a
fi

cd "$CORTEX_ROOT"
if [[ ! -d .venv ]]; then
  uv venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
uv pip install -e ".[api,dms]" >/dev/null

if ! curl -sf "$CORTEX_URL/health" >/dev/null 2>&1; then
  echo "==> Starting Cortex on :8000"
  nohup env PACK="$PACK" python -m uvicorn CortexOS.api.main:app --host 127.0.0.1 --port 8000 \
    >"$LOG_DIR/cortex.out.log" 2>"$LOG_DIR/cortex.err.log" &
  echo $! >"$LOG_DIR/cortex.pid"
  for _ in $(seq 1 60); do
    curl -sf "$CORTEX_URL/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -sf "$CORTEX_URL/health" >/dev/null || {
    echo "Cortex failed; see $LOG_DIR/cortex.err.log" >&2
    tail -n 50 "$LOG_DIR/cortex.err.log" >&2 || true
    exit 1
  }
else
  echo "==> Cortex already up"
fi

echo "==> Starting OpenVault mesh (+ AirGPT shell if needed)"
SKIP_BROWSER=1 WITH_AIRGPT_STUB=1 \
  bash "$OPENVAULT_ROOT/scripts/start_local_mesh.sh"

echo ""
echo "Stack ready:"
echo "  Cortex     $CORTEX_URL/health"
echo "  OpenVault  $OPENVAULT_URL/#mesh"
echo "  AirGPT     $OPENIDE_URL/"
