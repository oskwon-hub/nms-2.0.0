"""ARP Collector (5장 표: 초기 발견, IP-MAC 연결 / 6.1절 Seed Discovery).

NMS 서버 자신의 OS ARP/Neighbor 캐시를 읽어 Seed 후보를 만든다. 관리형 장비의
ARP 테이블(SNMP IP-MIB ipNetToMediaTable)은 snmp_collector에서 별도로 수집한다.
"""
from __future__ import annotations

import re
import subprocess

from app.collectors.base import ArpEntryInfo

_IP_NEIGH_PATTERN = re.compile(
    r"^(?P<ip>[0-9a-fA-F:.]+)\s+dev\s+(?P<dev>\S+)\s+lladdr\s+(?P<mac>[0-9a-fA-F:]{17})\s+(?P<state>\w+)",
    re.MULTILINE,
)


def local_neighbor_table() -> list[ArpEntryInfo]:
    """`ip neighbor show` 출력을 파싱한다 (Linux)."""
    try:
        proc = subprocess.run(["ip", "neighbor", "show"], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []

    entries: list[ArpEntryInfo] = []
    for match in _IP_NEIGH_PATTERN.finditer(proc.stdout):
        state = match.group("state").upper()
        if state in ("FAILED", "INCOMPLETE"):
            continue
        entries.append(
            ArpEntryInfo(
                if_index=None,
                ip=match.group("ip"),
                mac=match.group("mac").lower(),
                state="REACHABLE" if state in ("REACHABLE", "PERMANENT", "STALE") else state,
            )
        )
    return entries


def local_active_subnets() -> list[str]:
    """`ip -o addr show` 출력을 기반으로 서버의 활성 IPv4 서브넷(CIDR)을 반환한다 (6.1절)."""
    try:
        proc = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []

    subnets: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        for i, token in enumerate(parts):
            if token == "inet" and i + 1 < len(parts):
                cidr = parts[i + 1]
                if not cidr.startswith("127."):
                    subnets.append(cidr)
    return subnets
