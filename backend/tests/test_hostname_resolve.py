from __future__ import annotations

import struct

import pytest

from app.collectors.hostname_resolve import (
    _build_nbns_query,
    _encode_dns_name,
    parse_dns_ptr_response,
    parse_nbstat_response,
    resolve_hostname,
)


def _build_nbstat_response(names: list[tuple[str, int, int]]) -> bytes:
    """names: [(name, name_type, flags), ...]. 헤더/RR 이름/TTL 등은 파서가 쓰지 않는 만큼만 채운다."""
    header = b"\x00" * 56
    body = bytearray()
    body.append(len(names))
    for name, name_type, flags in names:
        body += name.ljust(15)[:15].encode("ascii")
        body.append(name_type)
        body += struct.pack(">H", flags)
    return header + bytes(body)


def test_parse_nbstat_response_returns_first_unique_workstation_name():
    data = _build_nbstat_response([("DESKTOP-ABC123", 0x00, 0x0000), ("WORKGROUP", 0x00, 0x8000)])
    assert parse_nbstat_response(data) == "DESKTOP-ABC123"


def test_parse_nbstat_response_skips_group_names():
    """flags의 Group bit(0x8000)가 켜진 이름(예: 도메인/워크그룹)은 컴퓨터 이름이 아니므로 건너뛴다."""
    data = _build_nbstat_response([("WORKGROUP", 0x00, 0x8000), ("MYHOST", 0x00, 0x0000)])
    assert parse_nbstat_response(data) == "MYHOST"


def test_parse_nbstat_response_handles_too_short_payload():
    assert parse_nbstat_response(b"\x00" * 10) is None


def test_build_nbns_query_has_expected_header_and_qtype():
    query = _build_nbns_query(transaction_id=0x1234)
    transaction_id, flags, qdcount = struct.unpack(">HHH", query[:6])
    assert transaction_id == 0x1234
    assert flags == 0x0000
    assert qdcount == 1
    qtype, qclass = struct.unpack(">HH", query[-4:])
    assert qtype == 0x0021
    assert qclass == 0x0001


def _build_dns_ptr_response(qname: str, ptr_name: str) -> bytes:
    header = struct.pack(">HHHHHH", 0x0000, 0x8180, 1, 1, 0, 0)
    question = _encode_dns_name(qname) + struct.pack(">HH", 12, 1)
    answer_name = b"\xc0\x0c"  # 질문 섹션(offset 12)을 가리키는 압축 포인터
    ptr_rdata = _encode_dns_name(ptr_name)
    answer = answer_name + struct.pack(">HHIH", 12, 1, 3600, len(ptr_rdata)) + ptr_rdata
    return header + question + answer


def test_parse_dns_ptr_response_extracts_short_hostname():
    data = _build_dns_ptr_response("2.1.0.10.in-addr.arpa", "myhost.example.com.")
    assert parse_dns_ptr_response(data) == "myhost"


def test_parse_dns_ptr_response_returns_none_when_no_answers():
    header = struct.pack(">HHHHHH", 0x0000, 0x8183, 1, 0, 0, 0)  # NXDOMAIN, ANCOUNT=0
    question = _encode_dns_name("2.1.0.10.in-addr.arpa") + struct.pack(">HH", 12, 1)
    assert parse_dns_ptr_response(header + question) is None


async def test_resolve_hostname_tries_rules_in_order_and_stops_at_first_success(monkeypatch):
    calls: list[str] = []

    async def fake_dns(ip, timeout=1.0):
        calls.append("DNS")
        return None

    async def fake_netbios(ip, timeout=1.0):
        calls.append("NETBIOS")
        return "WINPC01"

    async def fake_mdns(ip, timeout=1.0):
        calls.append("MDNS")
        return "should-not-be-called"

    import app.collectors.hostname_resolve as hr

    monkeypatch.setattr(hr, "RESOLVERS", {"DNS": fake_dns, "NETBIOS": fake_netbios, "MDNS": fake_mdns})

    name, source = await resolve_hostname("10.0.0.5", ["DNS", "NETBIOS", "MDNS"])
    assert name == "WINPC01"
    assert source == "NETBIOS"
    assert calls == ["DNS", "NETBIOS"]


async def test_resolve_hostname_returns_none_when_all_rules_fail(monkeypatch):
    import app.collectors.hostname_resolve as hr

    async def fake_fail(ip, timeout=1.0):
        return None

    monkeypatch.setattr(hr, "RESOLVERS", {"DNS": fake_fail})
    name, source = await resolve_hostname("10.0.0.5", ["DNS"])
    assert name is None
    assert source is None


async def test_resolve_hostname_with_no_rules_returns_none_immediately():
    name, source = await resolve_hostname("10.0.0.5", [])
    assert (name, source) == (None, None)
