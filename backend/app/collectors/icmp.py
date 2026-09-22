"""ICMP Collector (5장 표: Seed Discovery / Availability Sensor 기반).

subprocess로 시스템 ping 명령을 사용한다. root 권한 raw socket 접근 없이도
동작하도록 하여 배포 단순성을 유지한다(설계서의 '배포 단순화' 원칙).
"""
from __future__ import annotations

import re
import subprocess

from app.collectors.base import ProbeResult

_RTT_PATTERN = re.compile(r"time[=<]([\d.]+)\s*ms")
_LOSS_PATTERN = re.compile(r"([\d.]+)%\s+packet loss")
_TTL_PATTERN = re.compile(r"ttl=(\d+)", re.IGNORECASE)


def ping(ip: str, count: int = 1, timeout_seconds: float = 1.0) -> ProbeResult:
    try:
        proc = subprocess.run(
            ["ping", "-c", str(count), "-W", str(max(1, int(timeout_seconds))), ip],
            capture_output=True,
            text=True,
            timeout=timeout_seconds * count + 3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ProbeResult(ip=ip, alive=False, rtt_ms=None, loss_percent=100.0)

    output = proc.stdout
    loss_match = _LOSS_PATTERN.search(output)
    loss_percent = float(loss_match.group(1)) if loss_match else (0.0 if proc.returncode == 0 else 100.0)

    rtt_match = _RTT_PATTERN.search(output)
    rtt_ms = float(rtt_match.group(1)) if rtt_match else None

    ttl_match = _TTL_PATTERN.search(output)
    ttl = int(ttl_match.group(1)) if ttl_match else None

    alive = proc.returncode == 0 and loss_percent < 100.0
    return ProbeResult(ip=ip, alive=alive, rtt_ms=rtt_ms, loss_percent=loss_percent, ttl=ttl)


def classify_ttl_os_hint(ttl: int | None) -> str | None:
    """관측된 TTL로부터 최초 송신 TTL을 역추정해 대략적인 OS 계열을 힌트로 제공한다.

    잘 알려진 관행값: Windows 128, Linux/macOS(Unix 계열) 64, 일부 네트워크
    장비(Cisco IOS 등) 255. 홉 수만큼 감소하므로 관측값 이상인 가장 작은 관행값으로
    올림 추정한다. SNMP/WMI 없이도 저비용으로 얻을 수 있는 보조 근거일 뿐이며,
    반환값이 100% 확정적인 식별은 아니다.
    """
    if ttl is None:
        return None
    if ttl <= 64:
        return "UNIX"  # Linux, macOS 등
    if ttl <= 128:
        return "WINDOWS"
    return "NETWORK"  # Cisco IOS 등 초기 TTL 255 계열
