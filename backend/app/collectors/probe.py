"""5장 표: ONVIF/RTSP/mDNS/SSDP/WMI/WinRM 등 Application 계층 보조 Collector.

전체 프로토콜 스택(WS-Discovery, mDNS zeroconf, WMI/DCOM 등)을 완전히 구현하는
대신, Discovery 단계에서 실제로 신호를 얻을 수 있는 최소 실용 프로브만 구현한다.
mDNS/SSDP/WMI의 완전한 구현은 Phase 10(설계서 20장) 범위로 남겨둔다.
"""
from __future__ import annotations

import asyncio

_ONVIF_PROBE_BODY = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
    "<soap:Body>"
    '<GetSystemDateAndTime xmlns="http://www.onvif.org/ver10/device/wsdl"/>'
    "</soap:Body></soap:Envelope>"
)


async def tcp_port_open(ip: str, port: int, timeout: float = 1.0) -> bool:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


async def _http_get(ip: str, port: int, path: str = "/", timeout: float = 1.5) -> str | None:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        request = f"GET {path} HTTP/1.1\r\nHost: {ip}\r\nConnection: close\r\n\r\n"
        writer.write(request.encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.read(4096), timeout=timeout)
        return data.decode(errors="ignore")
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _http_post(ip: str, port: int, path: str, body: str, timeout: float = 1.5) -> str | None:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        payload = body.encode()
        request = (
            f"POST {path} HTTP/1.1\r\nHost: {ip}\r\nContent-Type: application/soap+xml\r\n"
            f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
        ).encode() + payload
        writer.write(request)
        await writer.drain()
        data = await asyncio.wait_for(reader.read(8192), timeout=timeout)
        return data.decode(errors="ignore")
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def probe_rtsp(ip: str, timeout: float = 1.0) -> bool:
    """RTSP(554/TCP) Open 여부만 확인한다 (5장 표: Camera 보조 식별)."""
    return await tcp_port_open(ip, 554, timeout=timeout)


async def probe_onvif(ip: str, ports: tuple[int, ...] = (80, 8080), timeout: float = 1.5) -> bool:
    """ONVIF Device Service 최소 프로브. SOAP 응답에 onvif 네임스페이스가
    포함되면 '강한 시그니처'로 간주한다 (8.1절)."""
    for port in ports:
        response = await _http_post(ip, port, "/onvif/device_service", _ONVIF_PROBE_BODY, timeout=timeout)
        if response and ("onvif.org" in response.lower() or "GetSystemDateAndTimeResponse".lower() in response.lower()):
            return True
    return False


async def probe_http_title_hint(ip: str, ports: tuple[int, ...] = (80, 8080), timeout: float = 1.5) -> bool:
    """HTTP 응답 본문에서 카메라/NVR 등 장비 유형을 암시하는 문자열을 찾는다."""
    hints = ("camera", "ipcam", "nvr", "dvr", "webcam")
    for port in ports:
        response = await _http_get(ip, port, timeout=timeout)
        if response:
            lower = response.lower()
            if any(hint in lower for hint in hints):
                return True
    return False


async def probe_smb_or_rdp(ip: str, timeout: float = 1.0) -> bool:
    """8장 표 WINDOWS_PC 근거: SMB(445) 또는 RDP(3389) 포트 Open 여부만 확인한다.
    실제 프로토콜 핸드셰이크까지는 하지 않는 최소 TCP 연결 확인이다."""
    for port in (445, 3389):
        if await tcp_port_open(ip, port, timeout=timeout):
            return True
    return False


_LINUX_DISTRO_HINTS = ("ubuntu", "debian", "centos", "fedora", "rhel", "suse", "raspbian", "alpine")


async def probe_ssh_banner(ip: str, timeout: float = 1.5) -> str | None:
    """SSH(22/TCP) 접속 시 서버가 인증 이전에 먼저 보내는 식별 문자열(RFC 4253)을
    읽는다. 인증을 시도하지 않으므로 자격 증명이 없어도 배너만으로 OS/배포판 힌트를
    얻을 수 있다 (예: 'OpenSSH_for_Windows', 'Ubuntu', 'Debian')."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, 22), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        data = await asyncio.wait_for(reader.readline(), timeout=timeout)
        banner = data.decode(errors="ignore").strip()
        return banner or None
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def classify_ssh_banner(banner: str | None) -> dict:
    """SSH 배너 문자열에서 8장 채점에 쓰이는 신호를 추출한다."""
    if not banner:
        return {"has_ssh_banner": False, "windows_hint": False, "linux_distro_hint": False, "macos_hint": False}
    lower = banner.lower()
    return {
        "has_ssh_banner": True,
        "windows_hint": "windows" in lower,
        "linux_distro_hint": any(hint in lower for hint in _LINUX_DISTRO_HINTS),
        "macos_hint": "darwin" in lower or "mac os" in lower or "macos" in lower,
    }
