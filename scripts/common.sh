#!/usr/bin/env bash
# nms-2.0.0 start/stop/deploy 스크립트 공통 설정.
#
# 포트 기본값은 이 서버에서 이미 운영 중인 다른 NMS 인스턴스(nms-1.3.0: 백엔드 8000,
# 프런트엔드 5173)와 충돌하지 않도록 의도적으로 분리했다. 필요 시 환경변수로 재정의한다.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
RUN_DIR="$ROOT_DIR/run"
LOG_DIR="$ROOT_DIR/logs"

NMS_BACKEND_HOST="${NMS_BACKEND_HOST:-0.0.0.0}"
NMS_BACKEND_PORT="${NMS_BACKEND_PORT:-18090}"
NMS_FRONTEND_PORT="${NMS_FRONTEND_PORT:-15180}"

BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
BACKEND_LOG_FILE="$LOG_DIR/backend.log"
FRONTEND_LOG_FILE="$LOG_DIR/frontend.log"

mkdir -p "$RUN_DIR" "$LOG_DIR"

log() { echo "[nms] $*"; }

is_pid_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

read_pid() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] && cat "$pid_file" || true
}

is_port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[.:]${port}\$"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1
  fi
}

stop_pid_file() {
  local name="$1" pid_file="$2"
  local pid
  pid="$(read_pid "$pid_file")"
  if is_pid_running "$pid"; then
    log "$name 중지 중 (pid $pid)..."
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      is_pid_running "$pid" || break
      sleep 0.3
    done
    if is_pid_running "$pid"; then
      log "$name 이(가) 정상 종료되지 않아 강제 종료합니다 (pid $pid)."
      kill -9 "$pid" 2>/dev/null || true
    fi
  else
    log "$name 이(가) 이미 중지되어 있습니다."
  fi
  rm -f "$pid_file"
}
