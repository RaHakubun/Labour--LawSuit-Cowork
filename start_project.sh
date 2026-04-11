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

ensure_command python
ensure_command npm
ensure_command uvicorn

if [[ ! -d "${FRONTEND_DIR}" ]]; then
  echo "[ERROR] Frontend directory not found: ${FRONTEND_DIR}"
  exit 1
fi

if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  echo "[INFO] Installing frontend dependencies..."
  (cd "${FRONTEND_DIR}" && npm i --cache .npm-cache)
fi

trap cleanup INT TERM EXIT

echo "[INFO] Starting backend on http://${BACKEND_HOST}:${BACKEND_PORT}"
(
  cd "${ROOT_DIR}"
  python -m uvicorn Agents.api_server:app \
    --host "${BACKEND_HOST}" \
    --port "${BACKEND_PORT}" \
    --reload
) &
BACKEND_PID=$!

sleep 1

echo "[INFO] Starting frontend on http://localhost:${FRONTEND_PORT}"
(
  cd "${FRONTEND_DIR}"
  VITE_AGENT_API_BASE="http://127.0.0.1:${BACKEND_PORT}" npm run dev -- --host 0.0.0.0 --port "${FRONTEND_PORT}"
) &
FRONTEND_PID=$!

echo "[INFO] Services started."
echo "[INFO] Frontend: http://localhost:${FRONTEND_PORT}"
echo "[INFO] Backend:  http://127.0.0.1:${BACKEND_PORT}/api/v1/health"
echo "[INFO] Press Ctrl+C to stop both."

wait "${BACKEND_PID}" "${FRONTEND_PID}"
