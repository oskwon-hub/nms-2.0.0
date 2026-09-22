#!/usr/bin/env bash
# 백엔드와 프런트엔드를 한 번에 정지한다 (start.sh가 dev/prod 어느 모드로
# 띄웠든 동일하게 동작 - prod 모드는 frontend.pid가 애초에 없으므로 자동 스킵됨).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./common.sh

stop_pid_file "백엔드" "$BACKEND_PID_FILE"
stop_pid_file "프런트엔드" "$FRONTEND_PID_FILE"

log "모두 중지되었습니다."
