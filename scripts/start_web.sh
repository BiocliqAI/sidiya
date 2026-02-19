#!/usr/bin/env bash
set -euo pipefail

cd /Users/rengarajanbashyam/Desktop/Oyster
PORT=8010
HOST=127.0.0.1
PID_FILE=.web.pid
LOG_FILE=.web.log

pyenv local 3.12.8
pyenv exec python -m venv .venv312

./.venv312/bin/python -m pip install -r requirements.txt >/dev/null

if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(cat "${PID_FILE}" || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
    kill "${OLD_PID}" 2>/dev/null || true
    sleep 1
  fi
  rm -f "${PID_FILE}"
fi

if command -v lsof >/dev/null 2>&1; then
  PORT_PIDS="$(lsof -ti tcp:${PORT} -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${PORT_PIDS}" ]]; then
    echo "${PORT_PIDS}" | xargs -n1 kill 2>/dev/null || true
    sleep 1
  fi
fi

nohup ./.venv312/bin/uvicorn app.main:app --host "${HOST}" --port "${PORT}" > "${LOG_FILE}" 2>&1 &
NEW_PID=$!
echo "${NEW_PID}" > "${PID_FILE}"

READY=0
for _ in {1..20}; do
  if curl -fsS "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done

if [[ "${READY}" -ne 1 ]]; then
  echo "Failed to start Oyster UI. Recent logs:"
  tail -n 80 "${LOG_FILE}" || true
  exit 1
fi

if ! curl -fsS "http://${HOST}:${PORT}/care-plan" >/dev/null 2>&1; then
  echo "Server started but /care-plan route is not reachable."
  tail -n 80 "${LOG_FILE}" || true
  exit 1
fi

echo "Started Oyster UI on http://${HOST}:${PORT}"
echo "PID: $(cat "${PID_FILE}")"
echo "Log: /Users/rengarajanbashyam/Desktop/Oyster/${LOG_FILE}"
