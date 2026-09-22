# 릴리스 절차 (Release Process)

이 프로젝트는 저장소 두 개, 브랜치 세 개를 각각 다른 용도로 사용합니다.

- **`nms-2.0.0` 저장소의 `dev` 브랜치 (개발)** — 사내 GitLab(`nms_g/nms-2.0.0`, private)에
  push합니다. 일상적인 기능 개발/버그 수정과 내부 작업 로그(`docs/ai-log/`)가 모두 여기
  남습니다.
- **`nms-2.0.0-public` 저장소의 `rel` 브랜치 (릴리스 후보)** — `dev`와 **공통 조상이
  전혀 없는 별도의 git 히스토리**입니다. `scripts/sync-to-public.sh`로 `dev`의 공개
  가능한 파일만 골라 반영한 뒤, 검토용으로 사내 GitLab의 같은 프로젝트에 push합니다.
- **`nms-2.0.0-public` 저장소의 `main` 브랜치 (공개)** — `rel`을 사람이 검토한 뒤
  fast-forward merge로 반영하는 브랜치입니다. GitHub 등 실제 공개 플랫폼에는 이
  `main`만 push합니다(GitHub 기본 브랜치 이름 관례와도 맞습니다).

```
nms-2.0.0 (사내 GitLab, private)
  dev  ── 전체 개발 히스토리, ai-log 포함 (절대 공개 안 함)

nms-2.0.0-public (별도 저장소, dev와 공통 조상 없음)
  rel  ── sync-to-public.sh로 dev를 반영한 "릴리스 후보" (사내 GitLab에서 검토)
  main ── rel을 검토 후 fast-forward merge한 "공개판" → GitHub push
```

> **`dev`를 `rel`/`main`에 절대 일반 merge로 합치지 마세요.** `git merge`(squash가
> 아닌 일반 merge)는 결과 커밋에 두 브랜치의 히스토리를 모두 조상으로 남기므로,
> 이후 GitHub 같은 공개 remote에 push하면 `docs/ai-log/`가 있던 옛 커밋까지 함께
> 넘어갑니다("지금 안 보인다"와 "히스토리에 없다"는 다릅니다). `dev → rel` 반영은
> 반드시 `scripts/sync-to-public.sh`(파일을 통째로 복사 + 재검사)를 거쳐야 합니다.
> 반면 `rel → main`은 **같은 공개 저장소 안의 두 브랜치**라 공유 히스토리 문제가
> 없으므로 평범한 `git merge`(fast-forward)로 충분합니다.

## 공개 저장소에서 항상 제외하는 것

정확한 최신 목록은 `scripts/sync-to-public.sh`의 `--exclude` 인자를 참고하세요(이
문서에 따로 복사해 두면 둘이 어긋날 수 있어 스크립트를 단일 진실 공급원으로 둡니다).
요약하면:

- `docs/ai-log/` — 실사 IP/장비 정보가 담긴 내부 작업 로그
- `*.docx`, `*.pdf` — 스크린샷에 실제 데이터가 있을 수 있는 설계 문서
- 빌드 산출물/의존성(`node_modules`, `.venv`, `dist` 등), 런타임 데이터
  (`backend/data`, `logs`, `run`)

## 릴리스 절차

1. **개발 저장소에서 변경 사항 확정** — 이번 릴리스에 포함할 커밋을 모두 완료하고
   테스트를 통과시킵니다.
   ```bash
   cd backend && .venv/bin/python -m pytest -q
   cd ../frontend && npx tsc --noEmit -p tsconfig.json && npx vite build
   ```
2. **CHANGELOG 갱신** — `CHANGELOG.md`에 새 버전 섹션을 추가합니다
   ([Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식).
3. **버전 값 확인** — `frontend/package.json`의 `version` 등, 버전 번호가 들어가는
   곳이 새 릴리스 버전과 일치하는지 확인합니다.
4. **`rel`(릴리스 후보)로 동기화**
   ```bash
   cd ../nms-2.0.0-public
   git checkout rel
   ../nms-2.0.0/scripts/sync-to-public.sh .
   ```
   스크립트가 (a) 제외 목록에 따라 파일을 복사하고 (b) 사내 도메인/사용자명이
   하나라도 남아 있으면 실패합니다. 172.16.x 사설 IP는 목록만 보여주므로 사람이
   직접 훑어봅니다. **git add/commit까지는 하지 않습니다** — 무엇이 바뀌었는지
   사람이 확인하는 단계를 건너뛰지 않기 위해서입니다.
5. **변경 내용 검토 및 `rel` 커밋**
   ```bash
   git status
   git diff
   git add -A
   git commit -m "release candidate: vX.Y.Z"
   git push origin rel
   ```
   필요하면 이 시점에 사내 GitLab에서 `rel`을 팀원과 함께 리뷰합니다.
6. **검토 완료 후 `main`에 반영(fast-forward merge)**
   ```bash
   git checkout main
   git merge rel
   git tag vX.Y.Z
   git push origin main --tags
   ```
   `rel → main`은 같은 저장소 안의 두 브랜치이므로 일반 merge로 충분합니다
   (fast-forward가 안 되면 `main`을 다른 곳에서 직접 건드렸다는 뜻이니 원인을
   먼저 확인하세요 - `main`은 항상 `rel`을 merge해서만 갱신되어야 합니다).
7. **(GitHub 등 실제 공개 준비가 되면)** 공개 플랫폼 remote를 추가로 등록하고
   `main`을 그쪽에도 push합니다.
   ```bash
   git remote add public <GitHub 등 저장소 URL>
   git push public main --tags
   ```

## 제외 목록에 새 항목을 추가해야 할 때

`scripts/sync-to-public.sh`의 `--exclude` 인자에 추가하세요. 특히 아래에 해당하는
파일/폴더가 새로 생기면 반드시 검토합니다.

- 실제 장비/사내망 데이터를 담을 수 있는 새로운 로그/리포트 디렉터리
- 스크린샷이나 바이너리 문서(docx/pdf/pptx 등)
- 자격증명이나 키 파일(`*.key`, `*.pem` 등 — 개발 저장소의 `.gitignore`에도 함께
  추가해야 합니다)

민감정보 재검사에 쓰는 패턴도 사내 환경이 바뀌면 함께 갱신하세요. 사내 도메인/사용자명
(`nstco\.co\.kr|nst\.com\b|\boskwon\b`)은 하나라도 나오면 스크립트가 실패하고, 172.16.x
사설 IP는 이 프로젝트 테스트 픽스처에 정상적으로 광범위하게 쓰이므로 목록만 보여주고
사람이 "실 사례"/"조사 중" 같은 문구가 붙어 있는지 직접 훑어보게 되어 있습니다
(`scripts/sync-to-public.sh` 참고).
