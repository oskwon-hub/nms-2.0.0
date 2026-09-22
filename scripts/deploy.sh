#!/usr/bin/env bash
# 원커맨드 배포 스크립트: 의존성 설치 -> 프런트엔드 빌드 -> 백엔드 정적 자산 반영
# -> (운영 모드) 기동까지 한 번에 수행한다.
#
#   scripts/deploy.sh            # 설치+빌드+운영 모드 기동까지 수행
#   scripts/deploy.sh --no-start # 설치+빌드만 수행하고 기동은 하지 않음
#
# 15.1절 SQLite3 적용 원칙(단일 파일, 별도 DB 서버 불필요)에 맞춰, 배포도
# venv + node_modules + nms.db 파일만으로 끝나도록 구성한다.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh

START_AFTER_DEPLOY=1
if [[ "${1:-}" == "--no-start" ]]; then
  START_AFTER_DEPLOY=0
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    log "오류: '$1' 명령을 찾을 수 없습니다. 설치 후 다시 시도하세요."
    exit 1
  }
}

log "=== 1/5. 사전 요구사항 확인 ==="
require_cmd python3
require_cmd npm
require_cmd node

log "=== 2/5. 백엔드 의존성 설치 (venv) ==="
if [[ ! -d "$BACKEND_DIR/.venv" ]]; then
  python3 -m venv "$BACKEND_DIR/.venv"
fi
(
  cd "$BACKEND_DIR"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install --upgrade pip -q
  pip install -r requirements.txt -q
)

log "=== 3/5. 백엔드 DB 스키마 초기화 (15.3절: schema_version 확인/자동 생성) ==="
(
  cd "$BACKEND_DIR"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -c "from app.db import init_db; init_db(); print('DB ready at', __import__('app.config', fromlist=['DB_PATH']).DB_PATH)"
)

log "=== 4/5. 프런트엔드 빌드 ==="
(
  cd "$FRONTEND_DIR"
  npm install --no-audit --no-fund
  npm run build
)

log "프런트엔드 빌드 결과물을 backend/app/static으로 반영 중..."
rm -rf "$BACKEND_DIR/app/static"
mkdir -p "$BACKEND_DIR/app/static"
cp -r "$FRONTEND_DIR/dist/"* "$BACKEND_DIR/app/static/"

log "=== 5/5. 배포 완료 ==="
if [[ "$START_AFTER_DEPLOY" -eq 1 ]]; then
  if is_pid_running "$(read_pid "$BACKEND_PID_FILE")" || is_pid_running "$(read_pid "$FRONTEND_PID_FILE")"; then
    log "기존 실행 중인 프로세스를 정지하고 재기동합니다."
    ./stop.sh
  fi
  ./start.sh prod
else
  log "빌드만 완료했습니다. 기동하려면 scripts/start.sh prod 를 실행하세요."
fi
