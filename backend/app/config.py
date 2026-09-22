"""NMS 설정 값.

설계서 15.1절 "SQLite3 적용 원칙" 표의 권장값을 기본값으로 사용한다.
환경변수로 재정의할 수 있게 하여 현장 배포 시 유연성을 확보한다.
"""
from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NMS_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# [KOS20260921] SQLite 단일 파일 구조가 기본이지만("설계서 15.1절"), 배포 환경에
# 따라 PostgreSQL을 붙일 수 있도록 백엔드를 선택 가능하게 한다. sqlite가 기존
# 기본값이므로 이 환경변수를 지정하지 않으면 이전과 100% 동일하게 동작한다.
DB_BACKEND = os.environ.get("NMS_DB_BACKEND", "sqlite").strip().lower()
if DB_BACKEND not in ("sqlite", "postgresql"):
    raise ValueError(f"지원하지 않는 NMS_DB_BACKEND 값입니다: {DB_BACKEND!r} (sqlite|postgresql 중 선택)")

DB_PATH = Path(os.environ.get("NMS_DB_PATH", DATA_DIR / "nms.db"))

PG_HOST = os.environ.get("NMS_PG_HOST", "localhost")
PG_PORT = os.environ.get("NMS_PG_PORT", "5432")
PG_DB = os.environ.get("NMS_PG_DB", "nms2db")
PG_USER = os.environ.get("NMS_PG_USER", "nms")
PG_PASSWORD = os.environ.get("NMS_PG_PASSWORD", "nms")

if DB_BACKEND == "postgresql":
    DATABASE_URL = f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"
else:
    DATABASE_URL = f"sqlite:///{DB_PATH}"


def describe_database() -> str:
    """Settings 화면 등에 노출할 안전한(비밀번호 제외) DB 연결 정보 문자열."""
    if DB_BACKEND == "postgresql":
        return f"postgresql://{PG_USER}@{PG_HOST}:{PG_PORT}/{PG_DB}"
    return str(DB_PATH)

# 15.1절 표: Busy Timeout 5~30초 범위 권장
SQLITE_BUSY_TIMEOUT_MS = int(os.environ.get("NMS_SQLITE_BUSY_TIMEOUT_MS", "15000"))

# 6.1절: 과도한 Scan 방지 상한
MAX_SEED_HOSTS = int(os.environ.get("NMS_MAX_SEED_HOSTS", "4096"))

# 6장: BFS 재귀 탐색 최대 깊이 (무한 확장 방지 안전장치, 설계서에 depth_score로 언급된 개념 보강)
MAX_DISCOVERY_DEPTH = int(os.environ.get("NMS_MAX_DISCOVERY_DEPTH", "12"))

# 8.1절: Device Type 확정 임계치 예시값
CLASSIFICATION_THRESHOLD = int(os.environ.get("NMS_CLASSIFICATION_THRESHOLD", "60"))
CLASSIFICATION_MIN_MARGIN = int(os.environ.get("NMS_CLASSIFICATION_MIN_MARGIN", "10"))
# 8.1절 "REVIEW_REQUIRED"의 최소 근거 기준선. 이 이상이면 완전히 UNKNOWN으로
# 숨기지 않고 "가장 유력한 후보(미확정)"를 device_type에 노출해, WMI/SSH 자격증명
# 없이도 부분 근거만으로 Windows/Linux/macOS 등을 짐작할 수 있게 한다.
CLASSIFICATION_REVIEW_FLOOR = int(os.environ.get("NMS_CLASSIFICATION_REVIEW_FLOOR", "25"))

# 9장: Core Switch 판정 임계치 (부록 A.1 예시가 98점이므로 실무적으로 60점을 기본 게이트로 사용)
CORE_SCORE_THRESHOLD = int(os.environ.get("NMS_CORE_SCORE_THRESHOLD", "60"))
FLOOR_SCORE_THRESHOLD = int(os.environ.get("NMS_FLOOR_SCORE_THRESHOLD", "50"))

# 15장: 장비 stale/offline 전이 기준 (초)
STALE_AFTER_SECONDS = int(os.environ.get("NMS_STALE_AFTER_SECONDS", "600"))
OFFLINE_AFTER_SECONDS = int(os.environ.get("NMS_OFFLINE_AFTER_SECONDS", "3600"))

# 12장: 링크 stale/down 전이 기준 (초)
LINK_STALE_AFTER_SECONDS = int(os.environ.get("NMS_LINK_STALE_AFTER_SECONDS", "600"))
LINK_DOWN_AFTER_SECONDS = int(os.environ.get("NMS_LINK_DOWN_AFTER_SECONDS", "3600"))

DEFAULT_SNMP_COMMUNITY = os.environ.get("NMS_SNMP_COMMUNITY", "public")
SNMP_TIMEOUT_SECONDS = float(os.environ.get("NMS_SNMP_TIMEOUT_SECONDS", "2.0"))
SNMP_RETRIES = int(os.environ.get("NMS_SNMP_RETRIES", "1"))

# [KOS20260921] 순차 스캔은 응답 없는 호스트가 많은 넓은 대역에서 총 소요시간이
# 호스트 수 x 개별 Timeout에 비례해 늘어난다. Worker마다 독립된 SQLite 연결을
# 쓰는 asyncio.Queue 기반 동시 처리로 완화하되(discovery/engine.py 참고),
# 과도한 동시성은 SNMP/ICMP 부하와 SQLite WAL 쓰기 경합을 늘리므로 상한을 둔다.
#
# [KOS20260921] 실사용 중 concurrency=16으로 전체 백엔드가 응답 불가 상태에
# 빠지는 것을 확인했다. _process_host()가 여러 SNMP await 구간에 걸쳐 하나의
# 쓰기 트랜잭션을 오래 열어 두는 구조라, SQLAlchemy의 session.commit()(동기/
# 블로킹 호출)이 다른 Worker의 미완료 트랜잭션 때문에 SQLite Write Lock을
# 기다리는 동안 asyncio 이벤트 루프 전체(=API 서버 전체)가 멈춘다. 여러
# Worker가 동시에 이 상태에 걸리면 각자의 busy_timeout(15초) 대기가 이벤트
# 루프 위에서 직렬로 누적돼 서버가 사실상 응답 불가 상태로 굳어질 수 있다
# (실제로 재현됨 - discovery_run concurrency=16 실행 중 /api/health까지 응답
# 없음). 근본 해결은 네트워크 I/O(수집)와 DB 쓰기(저장)를 완전히 분리해 트랜잭션
# 보유 시간을 최소화하는 리팩터링이 필요하지만, 그 전까지는 상한을 크게 낮춰
# 동시 쓰기 경합 가능성 자체를 줄인다.
DEFAULT_DISCOVERY_CONCURRENCY = int(os.environ.get("NMS_DEFAULT_DISCOVERY_CONCURRENCY", "2"))
MAX_DISCOVERY_CONCURRENCY = int(os.environ.get("NMS_MAX_DISCOVERY_CONCURRENCY", "8"))

# 자동 Discovery: 백엔드 프로세스 안의 asyncio 작업으로 주기 실행한다. 첫 실행은
# 서버 시작 직후의 API 초기화를 방해하지 않도록 짧게 지연하고, 이후 기본 10분마다
# 현재 Inventory의 관리 IP를 다시 수집한다.
AUTO_DISCOVERY_ENABLED = os.environ.get("NMS_AUTO_DISCOVERY_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on",
}
AUTO_DISCOVERY_INTERVAL_SECONDS = max(60, int(os.environ.get("NMS_AUTO_DISCOVERY_INTERVAL_SECONDS", "600")))
AUTO_DISCOVERY_INITIAL_DELAY_SECONDS = max(0, int(os.environ.get("NMS_AUTO_DISCOVERY_INITIAL_DELAY_SECONDS", "15")))

CORS_ALLOW_ORIGINS = os.environ.get("NMS_CORS_ALLOW_ORIGINS", "*").split(",")

STATIC_DIR = BASE_DIR / "app" / "static"


def _detect_local_ips() -> set[str]:
    """[KOS20260922] Discovery를 실행하는 이 서버 자신은 대상 장비를 SNMP로 조회할
    때마다(운영 환경에서 "특정 IP 하나가 유독 많은 장비와 연결된 것처럼 보인다"는
    형태로 실제로 발견된 사례) 그 장비의 ARP 테이블에 자연히 등록된다 - SNMP 응답을 주고받으려면 먼저 ARP로
    이 서버의 MAC을 알아내야 하기 때문이다. 이건 실제 물리적 연결이 아니라 스캔
    트래픽의 부작용이므로, Topology 링크 추정 단계에서 이 서버 자신의 IP는
    제외해야 한다. 네트워크가 아직 안 붙었거나 여러 NIC 환경이어도 최대한
    안전하게 동작하도록 두 가지 방법을 모두 시도해 합친다.
    """
    ips: set[str] = set()
    try:
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2)
        ips.update(ip for ip in result.stdout.split() if ip)
    except Exception:
        pass
    try:
        # UDP는 "connect"해도 실제로 패킷을 보내지 않고 라우팅 테이블만 조회하므로,
        # 8.8.8.8이 실제로 응답하지 않아도(또는 인터넷이 없어도) 안전하게 동작한다 -
        # hostname -I를 못 쓰는 환경(예: 컨테이너)을 위한 폴백.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except Exception:
        pass
    return ips


# [KOS20260921] 자동 감지 외에, 다른 모니터링 서버 등 "스캔 트래픽 때문에 모든
# 장비의 ARP 테이블에 나타나는" 별도 호스트가 있다면 운영자가 직접 추가할 수 있게
# 콤마로 구분한 IP 목록도 받는다.
TOPOLOGY_EXCLUDE_IPS: set[str] = _detect_local_ips() | {
    ip.strip() for ip in os.environ.get("NMS_TOPOLOGY_EXCLUDE_IPS", "").split(",") if ip.strip()
}
