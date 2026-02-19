#!/usr/bin/env bash
set -euo pipefail

cd /Users/rengarajanbashyam/Desktop/Oyster
PORT=8010

if [[ -f .web.pid ]]; then
  PID="$(cat .web.pid || true)"
  if [[ -n "${PID}" ]] && kill -0 "${PID}" 2>/dev/null; then
    kill "${PID}" 2>/dev/null || true
    sleep 1
  fi
  rm -f .web.pid
fi

if command -v lsof >/dev/null 2>&1; then
  PORT_PIDS="$(lsof -ti tcp:${PORT} -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${PORT_PIDS}" ]]; then
    echo "${PORT_PIDS}" | xargs -n1 kill 2>/dev/null || true
    sleep 1
  fi
fi

echo "Stopped Oyster UI"
