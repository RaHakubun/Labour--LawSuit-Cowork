#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="${ROOT_DIR}/jobpilot-front"

BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

ensure_command() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "[ERROR] Missing command: ${cmd}"
    exit 1
  fi
}

cleanup() {
  echo
  echo "[INFO] Stopping services..."
  if [[ -n "${BACKEND_PID:-}" ]] && kill -0 "${BACKEND_PID}" >/dev/null 2>&1; then
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${FRONTEND_PID:-}" ]] && kill -0 "${FRONTEND_PID}" >/dev/null 2>&1; then
    kill "${FRONTEND_PID}" >/dev/null 2>&1 || true
  fi
}

ensure_command lsof

for required_var in LLM_BASE_URL LLM_API_KEY LLM_MODEL OCR_BASE_URL OCR_API_KEY OCR_MODEL PKULAW_MCP_TOKEN DATABASE_URL APP_API_TOKENS_JSON; do
  if [[ -z "${!required_var:-}" ]]; then
    echo "[ERROR] ${required_var} is required."
    exit 1
  fi
done

resolve_python_bin() {
  # Prefer the active env interpreter (usually `python` in conda/venv).
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  echo "[ERROR] Missing command: python or python3"
  exit 1
}

PYTHON_BIN="${PYTHON_BIN:-$(resolve_python_bin)}"

if ! "${PYTHON_BIN}" -c "import uvicorn" >/dev/null 2>&1; then
  echo "[ERROR] ${PYTHON_BIN} cannot import uvicorn."
  echo "[ERROR] Install it in the current environment, e.g.:"
  echo "[ERROR]   ${PYTHON_BIN} -m pip install uvicorn"
  exit 1
fi

if ! "${PYTHON_BIN}" -c "import alembic, asyncpg, sqlalchemy" >/dev/null 2>&1; then
  echo "[ERROR] Database dependencies are missing. Install the project first:"
  echo "[ERROR]   ${PYTHON_BIN} -m pip install -e ."
  exit 1
fi

START_FRONTEND=0
if [[ -d "${FRONTEND_DIR}" ]]; then
  ensure_command npm
  START_FRONTEND=1
  if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
    echo "[INFO] Installing frontend dependencies..."
    (cd "${FRONTEND_DIR}" && npm i --cache .npm-cache)
  fi
else
  echo "[INFO] Frontend source is not present; starting the backend only."
fi

clear_frontend_quarantine() {
  # Some copied node_modules trees carry macOS quarantine flags
  # (e.g. copied via chat tools), which blocks native bindings at runtime.
  if command -v xattr >/dev/null 2>&1 && [[ -d "${FRONTEND_DIR}/node_modules" ]]; then
    xattr -dr com.apple.quarantine "${FRONTEND_DIR}/node_modules" 2>/dev/null || true
  fi
}

clear_frontend_quarantine

trap cleanup INT TERM EXIT

find_port_pids() {
  local port="$1"
  lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t 2>/dev/null | sort -u || true
}

assert_port_available() {
  local port="$1"
  local name="$2"
  local pids
  pids="$(find_port_pids "${port}")"
  if [[ -n "${pids}" ]]; then
    echo "[ERROR] ${name} port ${port} is already in use by PID(s): ${pids}"
    echo "[ERROR] Stop the existing process first, then rerun ./start_project.sh"
    exit 1
  fi
}

assert_process_started() {
  local pid="$1"
  local name="$2"
  if ! kill -0 "${pid}" >/dev/null 2>&1; then
    echo "[ERROR] ${name} failed to start."
    exit 1
  fi
}

assert_port_available "${BACKEND_PORT}" "Backend"
if [[ "${START_FRONTEND}" -eq 1 ]]; then
  assert_port_available "${FRONTEND_PORT}" "Frontend"
fi

echo "[INFO] Starting backend on http://${BACKEND_HOST}:${BACKEND_PORT}"
echo "[INFO] Applying database migrations..."
"${PYTHON_BIN}" -m alembic upgrade head
(
  cd "${ROOT_DIR}"
  "${PYTHON_BIN}" -m uvicorn Agents.async_api:app \
    --host "${BACKEND_HOST}" \
    --port "${BACKEND_PORT}" \
    --reload
) &
BACKEND_PID=$!

sleep 1
assert_process_started "${BACKEND_PID}" "Backend"

if [[ "${START_FRONTEND}" -eq 1 ]]; then
  echo "[INFO] Starting frontend on http://localhost:${FRONTEND_PORT}"
  (
    cd "${FRONTEND_DIR}"
    npm run dev -- --host 0.0.0.0 --port "${FRONTEND_PORT}"
  ) &
  FRONTEND_PID=$!
  sleep 1
  assert_process_started "${FRONTEND_PID}" "Frontend"
fi

echo "[INFO] Services started."
if [[ "${START_FRONTEND}" -eq 1 ]]; then
  echo "[INFO] Frontend:      http://localhost:${FRONTEND_PORT}"
fi
echo "[INFO] Backend:       http://127.0.0.1:${BACKEND_PORT}/api/v1/health"
echo "[INFO] Press Ctrl+C to stop both."

if [[ "${START_FRONTEND}" -eq 1 ]]; then
  wait "${BACKEND_PID}" "${FRONTEND_PID}"
else
  wait "${BACKEND_PID}"
fi
