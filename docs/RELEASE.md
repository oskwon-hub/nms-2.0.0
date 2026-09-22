# 릴리스 절차 (Release Process)

이 저장소 하나 안에서 브랜치 세 개로 개발부터 공개까지 관리합니다.

```
dev  ── 평소 개발. 기능/버그 커밋이 여기 쌓인다.
rel  ── dev를 merge해서 만드는 릴리스 후보. 사내에서 검토하는 자리.
main ── rel을 검토 후 merge. GitHub 등 공개 플랫폼에는 이 브랜치만 push한다.
```

세 브랜치가 하나의 히스토리를 공유하므로 평범한 `git merge`/`git push`만으로
충분합니다. 이게 가능한 이유는 **애초에 어떤 브랜치에도 민감정보를 커밋하지
않기 때문**입니다 — `docs/ai-log/`(내부 작업 로그, 실제 장비/IP가 남을 수 있음)와
설계서(`*.docx`, `*.pdf`)는 `.gitignore`에 등록되어 있어 git이 아예 추적하지
않습니다. 로컬 디스크에는 그대로 남아 있고, 필요하면 팀 내부적으로 파일로
공유하면 됩니다.

> **새 파일 종류를 커밋하기 전에 항상 확인하세요**: 실제 장비 정보나 스크린샷이
> 담길 수 있는 파일(로그, 리포트, 문서, 캡처 이미지 등)은 커밋하기 전에
> `.gitignore`에 먼저 추가하세요. 한번 커밋되면 나중에 지워도 히스토리에는
> 남습니다 - `scripts/check-before-push.sh`가 push 직전에 이런 실수를 한 번 더
> 잡아줍니다.

## 개발 → 릴리스 후보 (`dev` → `rel`)

```bash
git checkout rel
git merge dev
```

## 릴리스 후보 → 공개 (`rel` → `main`)

```bash
git checkout main
git merge rel
```

## 검증 및 공개 push

```bash
cd backend && .venv/bin/python -m pytest -q
cd ../frontend && npx tsc --noEmit -p tsconfig.json && npx vite build
scripts/check-before-push.sh main   # 민감정보 재검사
git tag vX.Y.Z
git push origin dev rel main --tags
```

GitHub 등 실제 공개 플랫폼이 준비되면 `main`만 그쪽에도 push합니다.

```bash
git remote add public <GitHub 등 저장소 URL>
git push public main --tags
```

## 새로운 종류의 파일을 다룰 때

아래에 해당하는 파일/폴더가 새로 생기면 커밋하기 전에 `.gitignore`부터
검토하세요.

- 실제 장비/사내망 데이터를 담을 수 있는 새로운 로그/리포트 디렉터리
- 스크린샷이나 바이너리 문서(docx/pdf/pptx 등)
- 자격증명이나 키 파일(`*.key`, `*.pem` 등)

`scripts/check-before-push.sh`의 검사 패턴도 사내 환경이 바뀌면(예: 다른 사설
IP 대역을 쓰게 되면) 함께 갱신하세요.
