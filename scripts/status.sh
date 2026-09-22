#!/usr/bin/env bash
# start.sh/stop.sh로 관리되는 백엔드/프런트엔드 프로세스 상태를 확인한다.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh

report() {
  local name="$1" pid_file="$2" port="$3"
  local pid
  pid="$(read_pid "$pid_file")"
  if is_pid_running "$pid"; then
    echo "[nms] $name: RUNNING (pid $pid, port $port)"
  else
    echo "[nms] $name: STOPPED"
  fi
}

report "백엔드" "$BACKEND_PID_FILE" "$NMS_BACKEND_PORT"
report "프런트엔드" "$FRONTEND_PID_FILE" "$NMS_FRONTEND_PORT"
