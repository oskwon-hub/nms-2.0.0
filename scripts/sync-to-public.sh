#!/usr/bin/env bash
# [KOS20260923] "nms-2.0.0을 개발용, nms-2.0.0-public을 release용으로 쓰려면" 요청 -
# 2026-09-22 오픈소스 공개 준비 때 수작업으로 한 rsync+재검사 절차를 재사용 가능한
# 스크립트로 만든다. 매번 전체를 다시 복사하고(증분이 아니라) 제외 목록을 이
# 스크립트 한 곳에만 두는 이유: docs/ai-log(실제 사내망 조사 기록), 설계서
# docx/pdf 등은 절대로 공개 저장소에 들어가면 안 되는데, 제외 목록이 여러 곳에
# 흩어져 있으면 하나를 빠뜨리기 쉽다. 복사 후에는 172.16.x/사내 도메인 문자열이
# 하나라도 남아있으면 실패하도록 재검사한다 - 제외 패턴을 잘못 고쳤거나 새 파일이
# 그 사이를 뚫고 들어온 경우를 잡기 위한 마지막 안전망이다.
#
# 이 스크립트는 git add/commit/push는 하지 않는다 - 무엇이 바뀌었는지 사람이
# git status/diff로 직접 검토하는 단계를 생략하지 않기 위함이다(CLAUDE.md
# "Git 커밋은 사용자의 명시적인 요청이 있을 때만 수행한다" 원칙과 같은 이유).
#
# 사용법:
#   scripts/sync-to-public.sh [공개 저장소 경로]
#   (기본값: 이 저장소와 같은 위치의 "<디렉터리명>-public")
set -euo pipefail
# [KOS20260923] 인자로 받은 공개 저장소 경로는 "이 스크립트를 부른 원래 위치"
# 기준 상대경로일 수 있다(예: nms-2.0.0-public 안에서 `../nms-2.0.0/scripts/
# sync-to-public.sh .`처럼 호출). 아래 cd로 DEV_DIR로 이동하기 전에 원래
# 위치를 먼저 저장해 뒀다가 그 기준으로 절대경로로 바꿔야, cd 이후에
# "."/상대경로가 엉뚱한 곳(DEV_DIR 기준)으로 풀리는 걸 막는다.
CALLER_DIR="$(pwd)"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

DEV_DIR="$(pwd)"
if [[ -n "${1:-}" ]]; then
  PUBLIC_DIR="$(cd "$CALLER_DIR" && cd "$1" && pwd)"
else
  PUBLIC_DIR="${DEV_DIR}-public"
fi

if [[ ! -d "$PUBLIC_DIR/.git" ]]; then
  echo "오류: $PUBLIC_DIR 가 git 저장소가 아닙니다." >&2
  echo "  최초 1회는 'git init'으로 공개 저장소를 직접 만든 뒤 다시 실행하세요." >&2
  exit 1
fi

echo "[sync] $DEV_DIR -> $PUBLIC_DIR"

# 공개 저장소에 절대 들어가면 안 되는 것들. 새 파일/폴더를 추가할 때 이 목록도
# 함께 검토한다(특히: 실사 데이터를 담을 수 있는 로그/리포트, 스크린샷·바이너리
# 문서, 자격증명/키 파일).
rsync -a --delete \
  --exclude='.git/' \
  --exclude='docs/ai-log/' \
  --exclude='*.docx' \
  --exclude='*.pdf' \
  --exclude='backend/.venv/' \
  --exclude='backend/__pycache__/' \
  --exclude='backend/**/__pycache__/' \
  --exclude='backend/.pytest_cache/' \
  --exclude='backend/data/' \
  --exclude='backend/app/static/' \
  --exclude='frontend/node_modules/' \
  --exclude='frontend/dist/' \
  --exclude='frontend/tsconfig.tsbuildinfo' \
  --exclude='logs/' \
  --exclude='run/' \
  --exclude='.agents/' \
  --exclude='.codex/' \
  --exclude='scripts/sync-to-public.sh' \
  "$DEV_DIR/" "$PUBLIC_DIR/"
find "$PUBLIC_DIR" -type d -empty -not -path "$PUBLIC_DIR/.git*" -delete

echo "[sync] 민감정보 재검사 중..."
# [KOS20260923] 사내 도메인/사용자명은 값 자체가 항상 실제 사내 정보이므로
# 하나라도 나오면 즉시 실패시킨다. 반면 172.16.x는 이 프로젝트 테스트
# 픽스처에서 "그럴듯한 사설 IP 예시"로 광범위하게 정상 사용되므로(예:
# 172.16.1.220 같은 합성 장비 IP) 하드 실패로 막으면 매 릴리스마다 오탐으로
# 막힌다 - 대신 목록만 보여주고 사람이 "특정 장비를 가리키는 실사례인지"를
# 직접 판단하게 한다(2026-09-23: 이 방식 도입 직전에, 바로 이 재검사가 소스
# 주석/테스트에 남아있던 진짜 실사례 IP·MAC 4곳을 실제로 잡아낸 적이 있다).
if grep -rn -E 'nstco\.co\.kr|nst\.com\b|\boskwon\b' \
    --exclude-dir=.git "$PUBLIC_DIR"; then
  echo "" >&2
  echo "!!! 위 문자열(사내 도메인/사용자명)이 공개 저장소 후보에 남아 있습니다. 커밋하기 전에 반드시 수정하세요 !!!" >&2
  exit 1
fi
echo "[sync] 사내 도메인/사용자명 검사 통과."

echo ""
echo "[sync] 참고: 아래는 172.16.x 사설 IP가 등장하는 위치입니다(대부분 합성 테스트"
echo "  픽스처로 정상이지만, 특정 장비를 가리키는 실사례 주석/데이터가 섞여 있지는"
echo "  않은지 훑어보세요 - 예: 뒤에 장비명이나 \"실 사례\"/\"조사 중\" 같은 문구가"
echo "  붙어 있으면 실제 조사 기록일 가능성이 높습니다)."
grep -rn -E '172\.16\.' --exclude-dir=.git "$PUBLIC_DIR" || echo "  (없음)"

echo ""
echo "[sync] 완료. 다음 단계 (직접 검토 후 진행):"
echo "  cd $PUBLIC_DIR"
echo "  git status                 # 무엇이 바뀌었는지 확인"
echo "  git diff                   # 상세 확인"
echo "  git add -A && git commit -m \"release: vX.Y.Z\""
echo "  git tag vX.Y.Z && git push origin rel --tags"
