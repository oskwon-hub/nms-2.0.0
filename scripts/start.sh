#!/usr/bin/env bash
# 백엔드(FastAPI)와 프런트엔드(React/Vite)를 한 번에 기동한다.
#
# 사용법:
#   scripts/start.sh          # 개발 모드: backend(--reload) + frontend(vite dev server) 동시 실행
#   scripts/start.sh prod     # 운영 모드: backend가 frontend 빌드 결과물을 함께 서빙 (deploy.sh가 준비)
#
# 환경변수로 포트를 바꿀 수 있다 (기본값은 이 서버의 다른 NMS 인스턴스와 충돌하지
# 않도록 18090/15180으로 분리되어 있다):
#   NMS_BACKEND_HOST, NMS_BACKEND_PORT, NMS_FRONTEND_PORT
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh

MODE="${1:-dev}"

if is_pid_running "$(read_pid "$BACKEND_PID_FILE")" || is_pid_running "$(read_pid "$FRONTEND_PID_FILE")"; then
  log "이미 실행 중인 프로세스가 있습니다. 먼저 scripts/stop.sh를 실행하세요."
  exit 1
fi

check_port_or_fail() {
  local label="$1" port="$2"
  if is_port_in_use "$port"; then
    log "오류: $label 포트 $port 이(가) 이미 사용 중입니다."
    log "  이 서버에는 다른 NMS 인스턴스(nms-1.3.0 등)가 8000/5173 포트로 떠 있을 수 있습니다."
    log "  NMS_BACKEND_PORT / NMS_FRONTEND_PORT 환경변수로 포트를 바꾸거나, 점유 프로세스를 확인하세요 (ss -ltnp 또는 lsof -i :$port)."
    exit 1
  fi
}

check_port_or_fail "백엔드" "$NMS_BACKEND_PORT"

if [[ "$MODE" == "dev" ]]; then
  check_port_or_fail "프런트엔드" "$NMS_FRONTEND_PORT"

  if [[ ! -x "$BACKEND_DIR/.venv/bin/uvicorn" ]]; then
    log "오류: backend/.venv가 준비되지 않았습니다. 먼저 scripts/deploy.sh를 실행하세요."
    exit 1
  fi
  if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    log "오류: frontend/node_modules가 없습니다. 먼저 scripts/deploy.sh를 실행하세요."
    exit 1
  fi

  log "백엔드 기동 중 (개발 모드, --reload)... 포트 $NMS_BACKEND_PORT"
  (
    cd "$BACKEND_DIR"
    # shellcheck disable=SC1091
    source .venv/bin/activate
    nohup uvicorn app.main:app --host "$NMS_BACKEND_HOST" --port "$NMS_BACKEND_PORT" --reload \
      >>"$BACKEND_LOG_FILE" 2>&1 &
    echo $! >"$BACKEND_PID_FILE"
  )

  log "프런트엔드 기동 중 (Vite dev server)... 포트 $NMS_FRONTEND_PORT"
  (
    cd "$FRONTEND_DIR"
    NMS_BACKEND_PORT="$NMS_BACKEND_PORT" NMS_FRONTEND_PORT="$NMS_FRONTEND_PORT" \
      nohup npm run dev >>"$FRONTEND_LOG_FILE" 2>&1 &
    echo $! >"$FRONTEND_PID_FILE"
  )

  sleep 1
  log "완료. Web UI: http://127.0.0.1:${NMS_FRONTEND_PORT}  API: http://${NMS_BACKEND_HOST}:${NMS_BACKEND_PORT}/docs"
  log "로그: $BACKEND_LOG_FILE / $FRONTEND_LOG_FILE"

elif [[ "$MODE" == "prod" ]]; then
  if [[ ! -x "$BACKEND_DIR/.venv/bin/uvicorn" ]]; then
    log "오류: backend/.venv가 준비되지 않았습니다. 먼저 scripts/deploy.sh를 실행하세요."
    exit 1
  fi
  if [[ ! -d "$BACKEND_DIR/app/static" ]]; then
    log "경고: backend/app/static이 없습니다. Web UI 없이 API만 서빙됩니다. scripts/deploy.sh 실행을 권장합니다."
  fi

  log "백엔드 기동 중 (운영 모드, Web UI 동시 서빙)... 포트 $NMS_BACKEND_PORT"
  (
    cd "$BACKEND_DIR"
    # shellcheck disable=SC1091
    source .venv/bin/activate
    nohup uvicorn app.main:app --host "$NMS_BACKEND_HOST" --port "$NMS_BACKEND_PORT" --workers 1 \
      >>"$BACKEND_LOG_FILE" 2>&1 &
    echo $! >"$BACKEND_PID_FILE"
  )

  sleep 1
  log "완료. Web UI/API: http://${NMS_BACKEND_HOST}:${NMS_BACKEND_PORT}  (API 문서: /docs)"
  log "로그: $BACKEND_LOG_FILE"

else
  log "알 수 없는 모드: $MODE (dev|prod 중 하나를 사용하세요)"
  exit 1
fi
