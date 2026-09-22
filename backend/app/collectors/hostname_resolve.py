"""서버명(hostname) 식별: SNMP sysName 외의 보조 방법.

SNMP sysName은 Switch/Router처럼 관리자가 명시적으로 설정해 둔 장비에서는
신뢰도 높은 근거이고(예: NSH/NHM 명명 규칙 기반 분류가 여기 의존하므로 그대로
유지해야 함), 일반 PC/서버는 SNMP Agent 자체가 없어 sysName을 얻을 수 없는
경우가 대부분이다. 이 모듈은 SNMP가 없어도 시도할 수 있는 보조 식별 방법을
제공하며, Discovery 화면에서 사용자가 선택한 규칙만 순서대로 시도해 첫 성공한
결과를 사용한다 (discovery/engine.py에서 SNMP sysName이 없을 때만 호출).
"""
from __future__ import annotations

import asyncio
import socket
import struct

HOSTNAME_RULES = ("DNS", "NETBIOS", "MDNS")


async def resolve_via_dns(ip: str, timeout: float = 1.0) -> str | None:
    """Reverse DNS(PTR) 조회. socket.gethostbyaddr는 블로킹 호출이라 executor로 뺀다."""
    loop = asyncio.get_running_loop()
    try:
        name, _aliases, _addrs = await asyncio.wait_for(
            loop.run_in_executor(None, socket.gethostbyaddr, ip), timeout
        )
    except (socket.herror, socket.gaierror, OSError, asyncio.TimeoutError):
        return None
    short_name = name.split(".")[0].strip()
    return short_name or None


async def _udp_query(ip: str, port: int, query: bytes, timeout: float) -> bytes | None:
    loop = asyncio.get_running_loop()
    on_response: asyncio.Future[bytes] = loop.create_future()

    class _Protocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
            if not on_response.done():
                on_response.set_result(data)

        def error_received(self, exc: Exception) -> None:
            if not on_response.done():
                on_response.set_exception(exc)

    transport = None
    try:
        transport, _protocol = await loop.create_datagram_endpoint(_Protocol, remote_addr=(ip, port))
        transport.sendto(query)
        return await asyncio.wait_for(on_response, timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        if transport is not None:
            transport.close()


def _encode_netbios_name(name16: bytes) -> bytes:
    """RFC 1002 4.1: NetBIOS 16바이트 이름을 half-ASCII(각 nibble -> 'A'+nibble)로 인코딩."""
    out = bytearray()
    for byte in name16:
        out.append(0x41 + (byte >> 4))
        out.append(0x41 + (byte & 0x0F))
    return bytes(out)


_NBNS_WILDCARD_NAME = _encode_netbios_name(b"*" + b"\x00" * 15)
_NBSTAT_QTYPE = 0x0021


def _build_nbns_query(transaction_id: int) -> bytes:
    header = struct.pack(">HHHHHH", transaction_id, 0x0000, 1, 0, 0, 0)
    question = b"\x20" + _NBNS_WILDCARD_NAME + b"\x00" + struct.pack(">HH", _NBSTAT_QTYPE, 0x0001)
    return header + question


def parse_nbstat_response(data: bytes) -> str | None:
    """RFC 1002 4.2.18 NODE STATUS RESPONSE에서 Workstation(0x00, Unique) 이름을 뽑는다."""
    try:
        if len(data) < 57:
            return None
        num_names = data[56]
        offset = 57
        for _ in range(num_names):
            if offset + 18 > len(data):
                break
            raw_name = data[offset : offset + 15]
            name_type = data[offset + 15]
            flags = struct.unpack(">H", data[offset + 16 : offset + 18])[0]
            offset += 18
            is_group = bool(flags & 0x8000)
            if name_type == 0x00 and not is_group:
                name = raw_name.decode("ascii", errors="ignore").strip()
                if name:
                    return name
    except (struct.error, IndexError):
        return None
    return None


async def resolve_via_netbios(ip: str, timeout: float = 1.0) -> str | None:
    """NBNS(UDP 137) Node Status 질의로 Windows 컴퓨터 이름을 얻는다 (nbtstat -A와 동일 원리)."""
    query = _build_nbns_query(transaction_id=0x1337)
    data = await _udp_query(ip, 137, query, timeout)
    if data is None:
        return None
    return parse_nbstat_response(data)


def _encode_dns_name(name: str) -> bytes:
    labels = [p for p in name.split(".") if p]
    return b"".join(bytes([len(p)]) + p.encode("ascii", errors="ignore") for p in labels) + b"\x00"


def _decode_dns_name(data: bytes, offset: int) -> tuple[str, int]:
    """DNS 이름 압축 포인터(RFC 1035 4.1.4)를 따라가며 이름을 복원한다.

    반환하는 offset은 '포인터를 만나기 전까지 읽은 위치 다음'이며(호출자가
    다음 필드를 계속 읽어야 하므로), 포인터를 따라간 뒤에는 갱신하지 않는다.
    """
    labels: list[str] = []
    cursor = offset
    end_offset = None
    jumps = 0
    while jumps < 20:
        if cursor >= len(data):
            break
        length = data[cursor]
        if length == 0:
            cursor += 1
            if end_offset is None:
                end_offset = cursor
            break
        if length & 0xC0 == 0xC0:
            if cursor + 1 >= len(data):
                break
            pointer = struct.unpack(">H", data[cursor : cursor + 2])[0] & 0x3FFF
            if end_offset is None:
                end_offset = cursor + 2
            cursor = pointer
            jumps += 1
            continue
        cursor += 1
        labels.append(data[cursor : cursor + length].decode("ascii", errors="ignore"))
        cursor += length
    if end_offset is None:
        end_offset = cursor
    return ".".join(labels), end_offset


async def resolve_via_mdns(ip: str, timeout: float = 1.0) -> str | None:
    """mDNS(UDP 5353) reverse PTR 질의로 macOS/일부 Linux(.local) 이름을 얻는다.

    표준 mDNS는 224.0.0.251로 Multicast 질의하는 것이 원칙이지만, macOS
    mDNSResponder/Avahi 등 다수 구현체는 대상 호스트로 직접 보낸 Unicast
    질의에도 응답하므로 별도 소켓/그룹 조인 없이 가벼운 구현을 유지한다.
    (표준을 엄격히 지키는 Responder에서는 응답이 없을 수 있음 - Best-effort.)
    """
    octets = ip.split(".")
    if len(octets) != 4 or not all(o.isdigit() for o in octets):
        return None
    qname = ".".join(reversed(octets)) + ".in-addr.arpa"
    header = struct.pack(">HHHHHH", 0x0000, 0x0000, 1, 0, 0, 0)
    question = _encode_dns_name(qname) + struct.pack(">HH", 12, 1)  # QTYPE=PTR, QCLASS=IN
    data = await _udp_query(ip, 5353, header + question, timeout)
    if data is None:
        return None
    return parse_dns_ptr_response(data)


def parse_dns_ptr_response(data: bytes) -> str | None:
    try:
        ancount = struct.unpack(">H", data[6:8])[0]
        if ancount == 0:
            return None
        _qname, offset = _decode_dns_name(data, 12)
        offset += 4  # QTYPE(2) + QCLASS(2)

        for _ in range(ancount):
            _name, offset = _decode_dns_name(data, offset)
            if offset + 10 > len(data):
                return None
            rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", data[offset : offset + 10])
            offset += 10
            if rtype == 12:  # PTR
                ptr_name, _ = _decode_dns_name(data, offset)
                short_name = ptr_name.rstrip(".").split(".")[0]
                return short_name or None
            offset += rdlength
    except (struct.error, IndexError):
        return None
    return None


RESOLVERS = {
    "DNS": resolve_via_dns,
    "NETBIOS": resolve_via_netbios,
    "MDNS": resolve_via_mdns,
}


async def resolve_hostname(ip: str, rules: list[str] | None, timeout: float = 1.0) -> tuple[str | None, str | None]:
    """선택된 규칙을 순서대로 시도해 첫 성공한 (이름, 방법)을 반환한다."""
    for rule in rules or []:
        resolver = RESOLVERS.get(rule)
        if resolver is None:
            continue
        try:
            name = await resolver(ip, timeout)
        except Exception:  # noqa: BLE001 - 개별 방법 실패는 다음 방법으로 넘어간다
            name = None
        if name:
            return name, rule
    return None, None
