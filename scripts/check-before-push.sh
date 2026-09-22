#!/usr/bin/env bash
# [KOS20260923] "처음부터 공개하기 위한 구조로" 요청 - dev/rel/main이 이제 한
# 저장소를 공유하는 평범한 브랜치라, 예전처럼 별도 저장소로 파일을 복사하며
# 재검사할 필요는 없어졌다. 대신 "실수로 민감한 파일을 git add -f 해버렸는지"
# 커밋된 실제 내용(git 트리) 기준으로 확인하는 마지막 안전망만 남긴다 -
# .gitignore는 실수를 막는 첫 번째 방어선일 뿐, 강제로 추가하면 뚫릴 수 있다.
#
# 사용법:
#   scripts/check-before-push.sh [브랜치/커밋]   (기본값: HEAD)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

REF="${1:-HEAD}"

echo "[check] '$REF' 커밋 내용 기준으로 민감정보 재검사 중..."

# 사내 도메인/사용자명은 값 자체가 항상 실제 사내 정보이므로 하나라도
# 나오면 즉시 실패시킨다.
if git grep -n -E 'nstco\.co\.kr|nst\.com\b|\boskwon\b' "$REF" -- . 2>/dev/null; then
  echo "" >&2
  echo "!!! 위 문자열(사내 도메인/사용자명)이 '$REF'에 커밋되어 있습니다 !!!" >&2
  exit 1
fi
echo "[check] 사내 도메인/사용자명 검사 통과."

# docs/ai-log, 설계서 docx/pdf가 .gitignore를 뚫고 커밋된 적이 있는지 확인.
if git ls-tree -r --name-only "$REF" | grep -iE '^docs/ai-log/|\.docx$|\.pdf$'; then
  echo "" >&2
  echo "!!! ai-log 또는 설계 문서가 '$REF'에 커밋되어 있습니다 !!!" >&2
  exit 1
fi
echo "[check] ai-log/설계 문서 추적 여부 검사 통과."

# 172.16.x 사설 IP는 이 프로젝트 테스트 픽스처에 정상적으로 광범위하게
# 쓰이므로(예: 172.16.1.220 같은 합성 장비 IP) 하드 실패로 막으면 오탐이 잦다.
# 목록만 보여주고 "실 사례"/"조사 중" 같은 문구가 붙은 실제 조사 기록이 섞여
# 있지 않은지는 사람이 직접 훑어본다.
echo ""
echo "[check] 참고: '$REF'에서 172.16.x 사설 IP가 등장하는 위치입니다(대부분 합성"
echo "  테스트 픽스처로 정상이지만, 특정 장비를 가리키는 실사례 주석/데이터가"
echo "  섞여 있지는 않은지 훑어보세요)."
git grep -n -E '172\.16\.' "$REF" -- . 2>/dev/null || echo "  (없음)"
