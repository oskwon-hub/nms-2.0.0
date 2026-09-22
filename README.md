# NMS 2.0.0

SNMP/SSH 기반 네트워크 자동 탐색(Discovery) 및 토폴로지 시각화, 장비 관리 웹 애플리케이션입니다.
FastAPI(Python) 백엔드 + React/Vite(TypeScript) 프런트엔드로 구성되며, SQLite(기본) 또는
PostgreSQL을 데이터 저장소로 사용합니다.

## 주요 기능

- **자동 탐색(Discovery)**: CIDR 대역을 지정하면 ICMP/SNMP/ARP를 이용해 장비를 찾고, 이웃 IP를
  재귀적으로 확장(BFS)해 네트워크 전체를 스캔합니다. LIGHT/STANDARD/DETAILED 등 수집 범위가
  다른 프로파일을 제공합니다.
- **장비 분류**: SNMP sysObjectID/OUI/인터페이스 특성 등을 근거로 장비 종류(스위치/라우터/AP 등)와
  역할(Core/Distribution/Floor/Access Switch, Server, Endpoint 등)을 자동 분류합니다.
- **토폴로지 시각화**: LLDP/CDP/STP Designated Bridge/FDB+ARP 상관관계 등 여러 근거를 결합해
  장비 간 링크를 추정하고, Role 단위로 묶은 "구름 보기"와 개별 장비를 모두 보여주는
  "전체 보기"를 제공합니다. 자동/원/격자/트리 배치를 지원합니다.
- **장비 진단**: 장비 상세 화면에서 Ping, TCP/UDP/ICMP Traceroute, MAC 학습 포트 기반 L2 경로
  추정을 바로 실행할 수 있습니다.
- **제어 기능**: PoE 포트 On/Off, 포트 Shutdown/No-Shutdown 등 장비 제어(SNMP SET 기반)를
  지원하며, 보호 대상 장비/포트는 별도로 관리합니다.
- **알람/리포트**: 장비 상태 변화(온라인/오프라인 등)를 알람으로 기록하고, 리포트 화면에서
  조회할 수 있습니다.

## 기술 스택

| 영역 | 구성 |
| --- | --- |
| 백엔드 | Python 3.12, FastAPI, SQLAlchemy 2.x, pysnmp, paramiko, APScheduler 계열 스케줄러 |
| 프런트엔드 | React 18, TypeScript, Vite, React Flow, d3-force |
| 데이터베이스 | SQLite(기본, 파일 기반) 또는 PostgreSQL |
| 테스트 | pytest(백엔드), tsc/vite build(프런트엔드 타입 체크 및 빌드 검증) |

## 빠른 시작

### 요구 사항

- Python 3.12+
- Node.js 18+ / npm
- (선택) PostgreSQL — 기본은 SQLite라 별도 설치 없이 바로 실행할 수 있습니다.

### 설치 및 개발 모드 실행

```bash
# 백엔드 의존성 설치
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..

# 프런트엔드 의존성 설치
cd frontend
npm install
cd ..

# 개발 모드로 백엔드(--reload) + 프런트엔드(Vite dev server) 동시 기동
scripts/start.sh
```

기본 포트는 `NMS_BACKEND_PORT=18090`, `NMS_FRONTEND_PORT=15180`이며, 환경변수로 바꿀 수
있습니다. 기동 후 브라우저에서 `http://127.0.0.1:15180`에 접속하면 됩니다. API 문서는
`http://127.0.0.1:18090/docs`(Swagger UI)에서 확인할 수 있습니다.

중지는 `scripts/stop.sh`, 현재 상태 확인은 `scripts/status.sh`를 사용합니다.

### 운영(prod) 모드

`scripts/deploy.sh`로 프런트엔드를 빌드해 백엔드 정적 파일로 통합한 뒤,
`scripts/start.sh prod`로 백엔드 한 프로세스만으로 Web UI + API를 함께 서빙할 수 있습니다.

### 테스트

```bash
# 백엔드
cd backend
.venv/bin/python -m pytest -q

# 프런트엔드 (타입 체크 + 빌드)
cd frontend
npx tsc --noEmit -p tsconfig.json
npx vite build
```

## 환경 변수

주요 환경 변수는 `backend/app/config.py`를 참고하세요. 예:

| 변수 | 설명 | 기본값 |
| --- | --- | --- |
| `NMS_DATA_DIR` | SQLite DB 파일 등 데이터 저장 경로 | `backend/data` |
| `NMS_SNMP_COMMUNITY` | 기본 SNMP v2c community (Credential Profile 미지정 시) | `public` |
| `NMS_BACKEND_HOST` / `NMS_BACKEND_PORT` | 백엔드 바인딩 주소/포트 | `0.0.0.0` / `18090` |
| `NMS_FRONTEND_PORT` | 프런트엔드(Vite dev server) 포트 | `15180` |

SNMP community, SSH 계정 등 실제 자격증명은 코드나 환경변수에 평문으로 두지 않고, 애플리케이션
내 Credential Profile 기능으로 암호화 저장(`backend/app/security.py`)해 관리합니다.

## 프로젝트 구조

```
backend/
  app/
    api/            REST API 라우터
    classification/ 장비 종류/역할 분류 로직
    collectors/     SNMP/ARP/ICMP/SSH 등 수집기
    control/        PoE/포트 제어
    discovery/       Discovery 엔진 및 프로파일
    topology/        토폴로지 링크 추정/역할 재계산
  tests/            pytest 테스트
frontend/
  src/
    api/            백엔드 API 클라이언트
    components/     React 컴포넌트 (토폴로지 그래프, 진단 패널 등)
    pages/          라우트별 페이지
    lib/            배치 알고리즘, 클러스터링 등 순수 로직
scripts/            시작/중지/배포 스크립트
docs/               설계/운영 문서 (CONTRIBUTING/SECURITY 등)
```

## 기여

기여 방법은 [CONTRIBUTING.md](CONTRIBUTING.md)를, 행동 강령은
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)를 참고하세요. 보안 취약점 제보는
[SECURITY.md](SECURITY.md)를 확인해 주세요.

## 라이선스

이 프로젝트는 [GNU General Public License v2.0](LICENSE)로 배포됩니다.

Copyright (C) 2026 NST Information & Communications Co., Ltd. (엔에스티정보통신)
