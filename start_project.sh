#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="${ROOT_DIR}/jobpilot-front"

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
BACKEND_BROWSER_HOST="${BACKEND_BROWSER_HOST:-127.0.0.1}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
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

resolve_python_bin() {
  local candidate
  local candidates=("${ROOT_DIR}/.venv/bin/python")
  if command -v python >/dev/null 2>&1; then
    candidates+=("$(command -v python)")
  fi
  if command -v python3 >/dev/null 2>&1; then
    candidates+=("$(command -v python3)")
  fi
  for candidate in "${candidates[@]}"; do
    if [[ -x "${candidate}" ]] \
      && "${candidate}" -c "import alembic, asyncpg, sqlalchemy, uvicorn" >/dev/null 2>&1; then
      echo "${candidate}"
      return
    fi
  done
  echo "[ERROR] No Python interpreter with project dependencies was found." >&2
  echo "[ERROR] Create .venv and install the project with: python3 -m pip install -e ." >&2
  exit 1
}

PYTHON_BIN="${PYTHON_BIN:-$(resolve_python_bin)}"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[ERROR] DATABASE_URL is required."
  exit 1
fi

configure_workbench_session() {
  if [[ -z "${APP_API_TOKENS_JSON:-}" ]]; then
    if [[ -z "${VITE_WORKBENCH_ACCESS_TOKEN:-}" ]]; then
      VITE_WORKBENCH_ACCESS_TOKEN="$("${PYTHON_BIN}" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    fi
    APP_API_TOKENS_JSON="$(
      WORKBENCH_TOKEN="${VITE_WORKBENCH_ACCESS_TOKEN}" "${PYTHON_BIN}" -c \
        'import json, os; print(json.dumps({os.environ["WORKBENCH_TOKEN"]: "local-user"}))'
    )"
    export APP_API_TOKENS_JSON VITE_WORKBENCH_ACCESS_TOKEN
    echo "[INFO] Created an automatic local workbench session."
    return
  fi

  VITE_WORKBENCH_ACCESS_TOKEN="$("${PYTHON_BIN}" -c '
import json
import os
import sys

try:
    mapping = json.loads(os.environ["APP_API_TOKENS_JSON"])
except (KeyError, json.JSONDecodeError):
    print("[ERROR] APP_API_TOKENS_JSON must be valid JSON.", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(mapping, dict) or not mapping:
    print("[ERROR] APP_API_TOKENS_JSON must map tokens to actor IDs.", file=sys.stderr)
    raise SystemExit(1)

explicit = os.getenv("VITE_WORKBENCH_ACCESS_TOKEN", "").strip()
if explicit:
    if explicit not in mapping:
        print("[ERROR] VITE_WORKBENCH_ACCESS_TOKEN is not present in APP_API_TOKENS_JSON.", file=sys.stderr)
        raise SystemExit(1)
    print(explicit)
    raise SystemExit(0)

if len(mapping) == 1:
    print(next(iter(mapping)))
    raise SystemExit(0)

admins = {
    item.strip()
    for item in os.getenv("INTEGRATION_ADMIN_ACTOR_IDS", "").split(",")
    if item.strip()
}
admin_tokens = [token for token, actor in mapping.items() if str(actor) in admins]
if len(admin_tokens) == 1:
    print(admin_tokens[0])
    raise SystemExit(0)

print(
    "[ERROR] Multiple workbench users are configured; set VITE_WORKBENCH_ACCESS_TOKEN "
    "to the local browser actor token.",
    file=sys.stderr,
)
raise SystemExit(1)
')"
  export VITE_WORKBENCH_ACCESS_TOKEN
  echo "[INFO] Connected the browser to the configured local workbench session."
}

configure_workbench_session

VITE_AGENT_API_BASE="${VITE_AGENT_API_BASE:-http://${BACKEND_BROWSER_HOST}:${BACKEND_PORT}/api/v1}"
export VITE_AGENT_API_BASE

echo "[INFO] No workbench token input is required in the browser."
echo "[INFO] LLM, OCR, and MCP credentials are configured in the web settings panel."
echo "[INFO] They are encrypted in PostgreSQL; the encryption key remains outside the database."

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
  echo "[INFO] Starting frontend on http://${FRONTEND_HOST}:${FRONTEND_PORT}"
  (
    cd "${FRONTEND_DIR}"
    npm run dev -- --host "${FRONTEND_HOST}" --port "${FRONTEND_PORT}"
  ) &
  FRONTEND_PID=$!
  sleep 1
  assert_process_started "${FRONTEND_PID}" "Frontend"
fi

echo "[INFO] Services started."
if [[ "${START_FRONTEND}" -eq 1 ]]; then
  echo "[INFO] Frontend:      http://${FRONTEND_HOST}:${FRONTEND_PORT}"
fi
echo "[INFO] Backend:       http://${BACKEND_HOST}:${BACKEND_PORT}/api/v1/health"
echo "[INFO] Press Ctrl+C to stop both."

if [[ "${START_FRONTEND}" -eq 1 ]]; then
  wait "${BACKEND_PID}" "${FRONTEND_PID}"
else
  wait "${BACKEND_PID}"
fi
