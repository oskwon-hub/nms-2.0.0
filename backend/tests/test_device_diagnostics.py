"""장비별 네트워크 진단 명령과 라우트 동작 검증."""
from __future__ import annotations

import subprocess

import pytest
from fastapi import HTTPException

from app.api.routes_devices import diagnose_device, get_l2_path_evidence
from app.diagnostics import _clean_cli_output, get_source_ip, get_source_mac, parse_ping_metrics, parse_route_hops, run_diagnostic
from app.identity import DeviceObservation, resolve_or_create_device
from app.models import DeviceInterface, MacFdb


def _create_device(session, ip="192.0.2.10"):
    device, _ = resolve_or_create_device(session, DeviceObservation(management_ip=ip))
    session.flush()
    return device


def test_ping_runs_against_inventory_ip_and_returns_output(db_session, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "4 packets transmitted, 4 received", "")

    monkeypatch.setattr("app.diagnostics.subprocess.run", fake_run)
    device = _create_device(db_session)
    result = diagnose_device(device.id, "ping", session=db_session)

    assert result.model_dump() == {
        "target": "192.0.2.10",
        "source_ip": None,
        "command": "ping",
        "protocol": "ICMP",
        "success": True,
        "output": "4 packets transmitted, 4 received",
        "hops": [],
    }
    assert calls[0][0] == ["ping", "-n", "-c", "4", "-W", "2", "192.0.2.10"]
    assert 0 < calls[0][1]["timeout"] <= 12


def test_tracepath_fallback_returns_failure_output(db_session, monkeypatch):
    monkeypatch.setattr("app.diagnostics.shutil.which", lambda command: None if command == "traceroute" else "/usr/bin/tracepath")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, " 1: no reply", "network unreachable")

    monkeypatch.setattr("app.diagnostics.subprocess.run", fake_run)
    device = _create_device(db_session)
    result = diagnose_device(device.id, "traceroute", "UDP", db_session)

    assert result.command == "tracepath"
    assert result.protocol == "UDP"
    assert result.success is False
    assert "network unreachable" in result.output
    assert calls[0] == ["tracepath", "-n", "-m", "20", "192.0.2.10"]


def test_timeout_and_missing_route_command(monkeypatch):
    def timeout_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"partial hop")

    monkeypatch.setattr("app.diagnostics.subprocess.run", timeout_run)
    command, success, output = run_diagnostic("192.0.2.10", "ping")
    assert command == "ping" and success is False
    assert "partial hop" in output and "제한 시간" in output

    monkeypatch.setattr("app.diagnostics.shutil.which", lambda _: None)
    command, success, output = run_diagnostic("192.0.2.10", "traceroute")
    assert command == "traceroute" and success is False
    assert "찾을 수 없습니다" in output
    command, success, output = run_diagnostic("192.0.2.10", "traceroute", "TCP")
    assert command == "traceroute" and success is False
    assert "TCP" in output


@pytest.mark.parametrize(
    "output,expected",
    [
        ("4 packets transmitted, 4 received, 0% packet loss\nrtt min/avg/max/mdev = 1.1/2.2/3.3/0.1 ms", (True, 0.0, 1.1, 2.2, 3.3)),
        ("Success rate is 80 percent (4/5), round-trip min/avg/max = 1/2/4 ms", (True, 20.0, 1.0, 2.0, 4.0)),
        ("4 packets transmitted, 0 received, 100% packet loss", (False, 100.0, None, None, None)),
    ],
)
def test_parse_ping_metrics(output, expected):
    assert parse_ping_metrics(output) == expected


def test_cli_default_profile_order_and_protocol_follow_nsh_nhm_prefix(db_session):
    from app.credentials import resolve_ssh_credentials, set_cli_credential
    from app.models import CredentialProfile

    nsh_profile = CredentialProfile(name="ssh-default-nsh")
    nhm_profile = CredentialProfile(name="ssh-default-nhm")
    db_session.add_all([nsh_profile, nhm_profile])
    db_session.flush()
    set_cli_credential(nsh_profile, "admin", "nsh-password", 23, "TELNET")
    set_cli_credential(nhm_profile, "admin", "nhm-password", 22, "SSH")
    nsh = _create_device(db_session, "192.0.2.31")
    nsh.hostname = "NSH-2128"
    nhm = _create_device(db_session, "192.0.2.32")
    nhm.hostname = "NHM-2408PI-BT"

    assert [credential[1] for credential in resolve_ssh_credentials(db_session, nsh)] == ["nsh-password", "nhm-password"]
    assert [credential[1] for credential in resolve_ssh_credentials(db_session, nhm)] == ["nhm-password", "nsh-password"]
    assert [(credential[2], credential[3]) for credential in resolve_ssh_credentials(db_session, nsh)] == [(23, "TELNET"), (22, "SSH")]


def test_clean_cli_output_removes_terminal_control_sequences():
    assert _clean_cli_output("\x1b[H\x1b[Jping result\r\n\x00") == "ping result"


@pytest.mark.parametrize("protocol,flag", [("TCP", "-T"), ("UDP", "-U"), ("ICMP", "-I")])
def test_trace_protocol_selects_corresponding_command(db_session, monkeypatch, protocol, flag):
    monkeypatch.setattr("app.diagnostics.shutil.which", lambda command: "/usr/sbin/traceroute" if command == "traceroute" else None)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "1 192.0.2.10", "")

    monkeypatch.setattr("app.diagnostics.subprocess.run", fake_run)
    device = _create_device(db_session)
    result = diagnose_device(device.id, "traceroute", protocol, db_session)
    assert result.protocol == protocol and result.success is True
    assert calls[0][:2] == ["traceroute", flag]
    assert calls[0][-1] == "192.0.2.10"


def test_permission_error_tries_next_available_method(monkeypatch):
    monkeypatch.setattr("app.diagnostics.shutil.which", lambda command: f"/usr/bin/{command}")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[0])
        if command[0] == "tracepath":
            return subprocess.CompletedProcess(command, 1, "", "socket: Operation not permitted")
        return subprocess.CompletedProcess(command, 0, "1 192.0.2.10", "")

    monkeypatch.setattr("app.diagnostics.subprocess.run", fake_run)
    command, success, output = run_diagnostic("192.0.2.10", "traceroute", "UDP")
    assert (command, success, output) == ("mtr", True, "1 192.0.2.10")
    assert calls == ["tracepath", "mtr"]


def test_tcp_permission_error_falls_back_to_traceroute(monkeypatch):
    monkeypatch.setattr("app.diagnostics.shutil.which", lambda command: f"/usr/bin/{command}")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[0])
        if command[0] == "mtr":
            return subprocess.CompletedProcess(command, 1, "", "Failure to open sockets: Operation not permitted")
        return subprocess.CompletedProcess(command, 0, "1 192.0.2.10", "")

    monkeypatch.setattr("app.diagnostics.subprocess.run", fake_run)
    command, success, _ = run_diagnostic("192.0.2.10", "traceroute", "TCP")
    assert command == "traceroute" and success is True
    assert calls == ["mtr", "traceroute"]


def test_all_methods_permission_denied_returns_guidance(monkeypatch):
    monkeypatch.setattr("app.diagnostics.shutil.which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        "app.diagnostics.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "socket: Operation not permitted"),
    )
    command, success, output = run_diagnostic("192.0.2.10", "traceroute", "ICMP")
    assert command == "traceroute" and success is False
    assert "CAP_NET_RAW" in output
    assert "mtr: socket" in output and "traceroute: socket" in output


def test_route_requires_existing_device_and_valid_ip(db_session):
    with pytest.raises(HTTPException) as missing:
        diagnose_device(999, "ping", session=db_session)
    assert missing.value.status_code == 404

    device = _create_device(db_session)
    with pytest.raises(HTTPException) as unknown:
        diagnose_device(device.id, "unknown", session=db_session)
    assert unknown.value.status_code == 404

    device.management_ip = None
    with pytest.raises(HTTPException) as no_ip:
        diagnose_device(device.id, "ping", session=db_session)
    assert no_ip.value.status_code == 400

    device.management_ip = "-c 100"
    with pytest.raises(HTTPException) as invalid_ip:
        diagnose_device(device.id, "ping", session=db_session)
    assert invalid_ip.value.status_code == 400


def test_invalid_protocol_is_rejected(db_session):
    device = _create_device(db_session)
    for kind, protocol in [("traceroute", "SCTP"), ("ping", "TCP")]:
        with pytest.raises(HTTPException) as invalid:
            diagnose_device(device.id, kind, protocol, db_session)
        assert invalid.value.status_code == 400


@pytest.mark.parametrize(
    "command,output",
    [
        ("tracepath", " 1:  10.0.0.1 0.2ms\n 2:  no reply\n 3:  192.0.2.10 0.5ms reached"),
        ("mtr", "  1.|-- 10.0.0.1 0.0% 3 1.0\n  2.|-- ??? 100.0% 3 0.0\n  3.|-- 192.0.2.10 0.0% 3 1.3"),
        ("traceroute", " 1  10.0.0.1  0.2 ms\n 2  * * *\n 3  192.0.2.10  0.5 ms"),
    ],
)
def test_parse_route_hops_preserves_order_and_unanswered_hops(command, output):
    assert parse_route_hops(command, output) == [
        (1, "10.0.0.1"),
        (2, None),
        (3, "192.0.2.10"),
    ]


def test_tracepath_local_mtu_line_and_repeated_replies_are_not_extra_hops():
    output = (
        " 1?: [LOCALHOST]                      pmtu 1500\n"
        " 1:  192.0.2.10                    0.870ms reached\n"
        " 1:  192.0.2.10                    0.821ms reached\n"
        "     Resume: pmtu 1500 hops 1 back 1"
    )
    assert parse_route_hops("tracepath", output) == [(1, "192.0.2.10")]


def test_unanswered_probe_is_removed_when_same_hop_later_replies():
    assert parse_route_hops("tracepath", " 2: no reply\n 2: 10.0.0.2 1.0ms") == [(2, "10.0.0.2")]


def test_route_hops_match_registered_devices(db_session, monkeypatch):
    router = _create_device(db_session, "10.0.0.1")
    router.hostname = "core-router"
    router.device_role = "CORE_SWITCH"
    target = _create_device(db_session)
    output = " 1:  10.0.0.1 0.2ms\n 2:  no reply\n 3:  192.0.2.10 0.5ms reached"
    monkeypatch.setattr("app.api.routes_devices.run_diagnostic", lambda *_: ("tracepath", True, output))
    monkeypatch.setattr("app.api.routes_devices.get_source_ip", lambda _: "172.16.1.99")

    result = diagnose_device(target.id, "traceroute", "UDP", db_session)
    assert result.source_ip == "172.16.1.99"
    assert [(hop.hop, hop.ip, hop.device_id, hop.hostname, hop.device_role) for hop in result.hops] == [
        (1, "10.0.0.1", router.id, "core-router", "CORE_SWITCH"),
        (2, None, None, None, None),
        (3, "192.0.2.10", target.id, target.hostname, target.device_role),
    ]


def test_source_ip_uses_destination_specific_route(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "192.0.2.10 dev enp7s0 src 172.16.1.99 uid 1000\n", "")

    monkeypatch.setattr("app.diagnostics.subprocess.run", fake_run)
    assert get_source_ip("192.0.2.10") == "172.16.1.99"
    assert calls == [["ip", "-4", "route", "get", "192.0.2.10"]]


def test_source_mac_uses_selected_route_interface(monkeypatch, tmp_path):
    interface_dir = tmp_path / "enp7s0"
    interface_dir.mkdir()
    (interface_dir / "address").write_text("AA:BB:CC:DD:EE:01\n")
    monkeypatch.setattr("app.diagnostics._SYS_CLASS_NET", tmp_path)
    monkeypatch.setattr(
        "app.diagnostics.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "192.0.2.10 dev enp7s0 src 172.16.1.99", ""),
    )
    assert get_source_mac("192.0.2.10") == "aa:bb:cc:dd:ee:01"


def test_l2_path_evidence_distinguishes_different_and_same_ports(db_session, monkeypatch):
    source_mac = "aa:bb:cc:dd:ee:01"
    target_mac = "f8:b1:56:b6:52:d3"
    target = _create_device(db_session)
    target.primary_mac = target_mac
    transit = _create_device(db_session, "192.0.2.1")
    same_side = _create_device(db_session, "192.0.2.2")
    one_side = _create_device(db_session, "192.0.2.3")

    def port(device, index):
        interface = DeviceInterface(device_id=device.id, if_index=index, name=f"Port {index}")
        db_session.add(interface)
        db_session.flush()
        return interface

    transit_source = port(transit, 1)
    transit_target = port(transit, 2)
    shared = port(same_side, 11)
    source_only = port(one_side, 3)
    for device, interface, mac in [
        (transit, transit_source, source_mac),
        (transit, transit_target, target_mac),
        (same_side, shared, source_mac),
        (same_side, shared, target_mac),
        (one_side, source_only, source_mac),
    ]:
        db_session.add(MacFdb(device_id=device.id, interface_id=interface.id, mac=mac, entry_type="DYNAMIC"))
    db_session.flush()
    monkeypatch.setattr("app.api.routes_devices.get_source_ip", lambda _: "172.16.1.99")
    monkeypatch.setattr("app.api.routes_devices.get_source_mac", lambda _: source_mac)

    result = get_l2_path_evidence(target.id, db_session)
    by_id = {item.switch_id: item for item in result.switches}
    assert result.source_ip == "172.16.1.99"
    assert result.target_mac == target_mac
    assert by_id[transit.id].relation == "DIFFERENT_PORTS"
    assert by_id[transit.id].source_ports == ["Port 1"]
    assert by_id[transit.id].target_ports == ["Port 2"]
    assert by_id[same_side.id].relation == "SAME_PORT"
    assert by_id[one_side.id].relation == "ONE_SIDE"


def test_l2_path_without_target_mac_returns_no_switch_claim(db_session, monkeypatch):
    target = _create_device(db_session)
    monkeypatch.setattr("app.api.routes_devices.get_source_ip", lambda _: "172.16.1.99")
    monkeypatch.setattr("app.api.routes_devices.get_source_mac", lambda _: "aa:bb:cc:dd:ee:01")
    result = get_l2_path_evidence(target.id, db_session)
    assert result.target_mac is None
    assert result.switches == []
