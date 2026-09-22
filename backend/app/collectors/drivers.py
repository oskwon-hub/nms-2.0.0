"""14장: Vendor Driver 및 SSH Fallback.

부록 B 의사코드의 NetworkDeviceDriver 인터페이스를 그대로 구현한다.
GenericSNMPDriver를 기본 구현으로 두고(14장 bullet), Discovery Core에는 Vendor
CLI 문자열을 직접 넣지 않는다 - CLI 파싱은 SshCliDriver 하위 클래스에 격리한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import paramiko

from app.collectors.base import (
    ArpEntryInfo,
    CollectorError,
    FdbEntryInfo,
    InterfaceInfo,
    LldpNeighborInfo,
    PoeInfo,
    RouteInfo,
    SystemInfo,
)
from app.collectors.snmp_collector import SnmpCollector


class NetworkDeviceDriver(ABC):
    """부록 B 의사코드의 Driver 인터페이스."""

    @abstractmethod
    async def identify(self) -> bool: ...

    @abstractmethod
    async def get_system_info(self) -> SystemInfo: ...

    @abstractmethod
    async def get_interfaces(self) -> list[InterfaceInfo]: ...

    @abstractmethod
    async def get_lldp_neighbors(self) -> list[LldpNeighborInfo]: ...

    @abstractmethod
    async def get_mac_table(self) -> list[FdbEntryInfo]: ...

    @abstractmethod
    async def get_arp_table(self) -> list[ArpEntryInfo]: ...

    @abstractmethod
    async def get_routes(self) -> list[RouteInfo]: ...

    @abstractmethod
    async def get_vlans(self) -> list[int]: ...

    @abstractmethod
    async def get_poe_status(self) -> list[PoeInfo]: ...

    @abstractmethod
    async def enable_port(self, if_index: int) -> None: ...

    @abstractmethod
    async def disable_port(self, if_index: int) -> None: ...

    @abstractmethod
    async def enable_poe(self, peth_port_index: int) -> None: ...

    @abstractmethod
    async def disable_poe(self, peth_port_index: int) -> None: ...


class GenericSNMPDriver(NetworkDeviceDriver):
    """14장 bullet: '표준 MIB'만으로 동작하는 기본 구현. 대부분의 Vendor에서 동작한다."""

    def __init__(self, host: str, community: str = "public", **snmp_kwargs):
        self.snmp = SnmpCollector(host, community=community, **snmp_kwargs)

    async def identify(self) -> bool:
        return await self.snmp.is_reachable()

    async def get_system_info(self) -> SystemInfo:
        return await self.snmp.get_system_info()

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return await self.snmp.get_interfaces()

    async def get_lldp_neighbors(self) -> list[LldpNeighborInfo]:
        return await self.snmp.get_lldp_neighbors()

    async def get_mac_table(self) -> list[FdbEntryInfo]:
        return await self.snmp.get_fdb()

    async def get_arp_table(self) -> list[ArpEntryInfo]:
        return await self.snmp.get_arp_table()

    async def get_routes(self) -> list[RouteInfo]:
        return await self.snmp.get_routes()

    async def get_vlans(self) -> list[int]:
        return await self.snmp.get_vlans()

    async def get_poe_status(self) -> list[PoeInfo]:
        return await self.snmp.get_poe_status()

    async def enable_port(self, if_index: int) -> None:
        await self.snmp.set_if_admin_status(if_index, enable=True)

    async def disable_port(self, if_index: int) -> None:
        await self.snmp.set_if_admin_status(if_index, enable=False)

    async def enable_poe(self, peth_port_index: int) -> None:
        await self.snmp.set_poe_admin_enable(peth_port_index, enable=True)

    async def disable_poe(self, peth_port_index: int) -> None:
        await self.snmp.set_poe_admin_enable(peth_port_index, enable=False)


@dataclass
class SshCredential:
    username: str
    password: Optional[str] = None
    pkey_path: Optional[str] = None
    port: int = 22
    timeout: float = 5.0


class SshCliClient:
    """paramiko 기반 최소 SSH CLI 실행기. Vendor Driver가 재사용한다."""

    def __init__(self, host: str, credential: SshCredential):
        self.host = host
        self.credential = credential

    def run_commands(self, commands: list[str]) -> dict[str, str]:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                self.host,
                port=self.credential.port,
                username=self.credential.username,
                password=self.credential.password,
                key_filename=self.credential.pkey_path,
                timeout=self.credential.timeout,
                look_for_keys=self.credential.pkey_path is not None,
                allow_agent=False,
            )
            outputs: dict[str, str] = {}
            shell = client.invoke_shell()
            shell.settimeout(self.credential.timeout)
            _drain(shell)
            for cmd in commands:
                shell.send(cmd + "\n")
                outputs[cmd] = _drain(shell)
            return outputs
        except (paramiko.SSHException, OSError) as exc:
            raise CollectorError(f"SSH 접속/실행 실패 {self.host}: {exc}") from exc
        finally:
            client.close()


def _drain(shell, idle_reads: int = 3, chunk_timeout: float = 0.7) -> str:
    import time

    buf = b""
    idle = 0
    while idle < idle_reads:
        if shell.recv_ready():
            buf += shell.recv(65536)
            idle = 0
        else:
            time.sleep(chunk_timeout)
            idle += 1
    return buf.decode(errors="ignore")


class CiscoIosSshDriver(NetworkDeviceDriver):
    """14장 bullet 예시 명령(show lldp neighbors detail, show mac address-table,
    show arp, show ip route, show interfaces status, show power inline)을 사용하는
    Cisco IOS 계열 SSH CLI Driver. 표준 MIB로 얻기 어려운 Vendor 고유 데이터의
    Fallback이며, 화면 파싱 정규식은 IOS 12.x/15.x 공통 출력 형식을 기준으로 한다.
    """

    def __init__(self, host: str, credential: SshCredential):
        self.host = host
        self.client = SshCliClient(host, credential)

    async def identify(self) -> bool:
        import asyncio

        try:
            output = await asyncio.to_thread(self.client.run_commands, ["show version | include Cisco"])
            return "Cisco" in output.get("show version | include Cisco", "")
        except CollectorError:
            return False

    async def get_system_info(self) -> SystemInfo:
        import asyncio

        output = await asyncio.to_thread(self.client.run_commands, ["show version"])
        text = output.get("show version", "")
        return SystemInfo(sys_descr=text.splitlines()[0] if text else "", sys_object_id="", sys_name="")

    async def get_interfaces(self) -> list[InterfaceInfo]:
        import asyncio
        import re

        output = await asyncio.to_thread(self.client.run_commands, ["show interfaces status"])
        text = output.get("show interfaces status", "")
        interfaces = []
        pattern = re.compile(r"^(?P<name>\S+)\s+.*?\s+(?P<status>connected|notconnect|disabled)\s+", re.MULTILINE)
        for idx, match in enumerate(pattern.finditer(text), start=1):
            status = match.group("status")
            interfaces.append(
                InterfaceInfo(
                    if_index=idx,
                    name=match.group("name"),
                    admin_status="DOWN" if status == "disabled" else "UP",
                    oper_status="UP" if status == "connected" else "DOWN",
                )
            )
        return interfaces

    async def get_lldp_neighbors(self) -> list[LldpNeighborInfo]:
        import asyncio
        import re

        output = await asyncio.to_thread(self.client.run_commands, ["show lldp neighbors detail"])
        text = output.get("show lldp neighbors detail", "")
        neighbors = []
        for block in text.split("------------------------------------------------")[1:]:
            chassis = re.search(r"Chassis id:\s*(\S+)", block)
            port = re.search(r"Port id:\s*(\S+)", block)
            sysname = re.search(r"System Name:\s*(\S+)", block)
            mgmt_ip = re.search(r"Management Addresses:\s*[\r\n]+\s*IP:\s*(\S+)", block)
            if not chassis:
                continue
            neighbors.append(
                LldpNeighborInfo(
                    local_if_index=None,
                    remote_chassis_id=chassis.group(1),
                    remote_port_id=port.group(1) if port else None,
                    remote_sys_name=sysname.group(1) if sysname else None,
                    remote_mgmt_ip=mgmt_ip.group(1) if mgmt_ip else None,
                    protocol="LLDP",
                )
            )
        return neighbors

    async def get_mac_table(self) -> list[FdbEntryInfo]:
        import asyncio
        import re

        output = await asyncio.to_thread(self.client.run_commands, ["show mac address-table"])
        text = output.get("show mac address-table", "")
        entries = []
        pattern = re.compile(r"^\s*(?P<vlan>\d+)\s+(?P<mac>[0-9a-fA-F.]{14})\s+\S+\s+(?P<port>\S+)", re.MULTILINE)
        for match in pattern.finditer(text):
            mac = _normalize_cisco_mac(match.group("mac"))
            entries.append(FdbEntryInfo(if_index=None, vlan=int(match.group("vlan")), mac=mac, entry_type="DYNAMIC"))
        return entries

    async def get_arp_table(self) -> list[ArpEntryInfo]:
        import asyncio
        import re

        output = await asyncio.to_thread(self.client.run_commands, ["show arp"])
        text = output.get("show arp", "")
        entries = []
        pattern = re.compile(r"Internet\s+(?P<ip>\d+\.\d+\.\d+\.\d+)\s+\S+\s+(?P<mac>[0-9a-fA-F.]{14})")
        for match in pattern.finditer(text):
            entries.append(ArpEntryInfo(if_index=None, ip=match.group("ip"), mac=_normalize_cisco_mac(match.group("mac"))))
        return entries

    async def get_routes(self) -> list[RouteInfo]:
        import asyncio
        import re

        output = await asyncio.to_thread(self.client.run_commands, ["show ip route"])
        text = output.get("show ip route", "")
        entries = []
        pattern = re.compile(
            r"(?P<net>\d+\.\d+\.\d+\.\d+)/(?P<prefix>\d+)\s+.*?via\s+(?P<nh>\d+\.\d+\.\d+\.\d+)"
        )
        for match in pattern.finditer(text):
            entries.append(
                RouteInfo(
                    destination=match.group("net"),
                    prefix_len=int(match.group("prefix")),
                    next_hop=match.group("nh"),
                    if_index=None,
                    route_type="STATIC",
                )
            )
        return entries

    async def get_vlans(self) -> list[int]:
        import asyncio
        import re

        output = await asyncio.to_thread(self.client.run_commands, ["show vlan brief"])
        text = output.get("show vlan brief", "")
        return [int(m.group(1)) for m in re.finditer(r"^(\d+)\s+\S+", text, re.MULTILINE)]

    async def get_poe_status(self) -> list[PoeInfo]:
        import asyncio
        import re

        output = await asyncio.to_thread(self.client.run_commands, ["show power inline"])
        text = output.get("show power inline", "")
        entries = []
        pattern = re.compile(r"^(?P<intf>\S+)\s+(?P<admin>auto|off|static)\s+(?P<oper>\S+)", re.MULTILINE)
        for idx, match in enumerate(pattern.finditer(text), start=1):
            entries.append(
                PoeInfo(
                    if_index=idx,
                    enabled=match.group("admin") != "off",
                    status="DELIVERING" if match.group("oper") == "on" else "SEARCHING",
                )
            )
        return entries

    async def enable_port(self, if_index: int) -> None:
        raise NotImplementedError("Cisco SSH Driver의 config-mode 제어는 GenericSNMPDriver 사용을 권장한다.")

    async def disable_port(self, if_index: int) -> None:
        raise NotImplementedError("Cisco SSH Driver의 config-mode 제어는 GenericSNMPDriver 사용을 권장한다.")

    async def enable_poe(self, peth_port_index: int) -> None:
        raise NotImplementedError("Cisco SSH Driver의 config-mode 제어는 GenericSNMPDriver 사용을 권장한다.")

    async def disable_poe(self, peth_port_index: int) -> None:
        raise NotImplementedError("Cisco SSH Driver의 config-mode 제어는 GenericSNMPDriver 사용을 권장한다.")


def _normalize_cisco_mac(cisco_mac: str) -> str:
    """Cisco의 xxxx.xxxx.xxxx 표기를 xx:xx:xx:xx:xx:xx로 변환한다."""
    hex_digits = cisco_mac.replace(".", "")
    return ":".join(hex_digits[i : i + 2] for i in range(0, 12, 2)).lower()
