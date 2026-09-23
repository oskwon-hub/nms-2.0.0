"""SNMP Collector (5장 표: SNMP v2c/v3 - 핵심 수집).

sysDescr/sysObjectID, IF-MIB, LLDP-MIB, BRIDGE-MIB/Q-BRIDGE-MIB(FDB), IP-MIB(ARP/Route/Forwarding),
POWER-ETHERNET-MIB(PoE)를 수집하고, IF-MIB/POWER-ETHERNET-MIB SET으로 Port/PoE
제어를 수행한다. pysnmp 6.x는 asyncio 전용 hlapi만 제공하므로 본 Collector 전체를
async로 구현한다.
"""
from __future__ import annotations

import asyncio

import pysnmp.hlapi.asyncio as ha

from app.collectors.base import (
    ArpEntryInfo,
    CollectorError,
    FdbEntryInfo,
    InterfaceInfo,
    LldpNeighborInfo,
    PoeInfo,
    RouteInfo,
    StpInfo,
    SystemInfo,
)

OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
OID_SYS_LOCATION = "1.3.6.1.2.1.1.6.0"

OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
OID_IF_PHYS_ADDR = "1.3.6.1.2.1.2.2.1.6"
OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
OID_IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
OID_IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"
OID_IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"
# [KOS20260923] "안전한 구성 제어" 요청 - IF-MIB ifXTable::ifAlias(포트 설명).
# ifIndex로 바로 색인되어 dot1qPvid와 달리 BRIDGE-MIB 역매핑이 필요 없다.
OID_IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"

OID_IP_FORWARDING = "1.3.6.1.2.1.4.1.0"
OID_IP_NET_TO_MEDIA_PHYS = "1.3.6.1.2.1.4.22.1.2"
# [KOS20260921] ipAddrTable(RFC 1213). ipRouteTable은 deprecated라 SVI에 정적/직접
# 연결 경로만 있는 L3 스위치에서 비어 있는 경우가 많다. 이 장비가 IP를 할당받은
# 인터페이스(=SVI) 개수를 세는 것이 훨씬 안정적인 L3 근거다.
OID_IP_ADDR_IFINDEX = "1.3.6.1.2.1.4.20.1.2"
OID_IP_ROUTE_DEST = "1.3.6.1.2.1.4.21.1.1"
OID_IP_ROUTE_IFINDEX = "1.3.6.1.2.1.4.21.1.2"
OID_IP_ROUTE_METRIC1 = "1.3.6.1.2.1.4.21.1.3"
OID_IP_ROUTE_NEXTHOP = "1.3.6.1.2.1.4.21.1.7"
OID_IP_ROUTE_MASK = "1.3.6.1.2.1.4.21.1.11"
OID_IP_ROUTE_TYPE = "1.3.6.1.2.1.4.21.1.8"

OID_DOT1D_BASE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"
OID_DOT1D_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"
OID_DOT1D_FDB_STATUS = "1.3.6.1.2.1.17.4.3.1.3"
OID_DOT1Q_TP_FDB_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"
OID_DOT1Q_VLAN_CURRENT_FDB_ID = "1.3.6.1.2.1.17.7.1.4.2.1.3"

# [KOS20260922] BRIDGE-MIB dot1dStp 그룹(RFC 1493). dot1dStpRootPort==0이면 이
# 장비 자신이 Root Bridge라는 표준 규약이다. dot1dStpPortState의 인덱스는
# dot1dBasePort(=dot1dStpPort)라 dot1dBasePortIfIndex로 ifIndex 변환이 필요하다
# (get_fdb()/get_lldp_neighbors()의 dot1dBasePortIfIndex 사용과 동일한 이유).
OID_DOT1D_STP_ROOT_PORT = "1.3.6.1.2.1.17.2.7.0"  # 스칼라라 인스턴스 접미사 .0 필요 (OID_SYS_DESCR와 동일한 규칙)
OID_DOT1D_STP_PORT_STATE = "1.3.6.1.2.1.17.2.15.1.3"
# [KOS20260922] dot1dStpPortDesignatedBridge - BridgeId(OCTET STRING SIZE(8) =
# Priority 2바이트 + MAC 6바이트)로, 이 포트가 속한 세그먼트의 Designated
# Bridge를 가리킨다. LLDP-MIB을 지원하지 않는 장비가 많아 LLDP만으로 놓치는
# 실제 이웃을, STP(훨씬 보편적으로 지원됨)를 통해 알아내는 데 쓴다.
OID_DOT1D_STP_PORT_DESIGNATED_BRIDGE = "1.3.6.1.2.1.17.2.15.1.8"

# Q-BRIDGE-MIB (RFC 4363): VLAN 목록과 Port별 PVID.
OID_DOT1Q_VLAN_STATIC_NAME = "1.3.6.1.2.1.17.7.1.4.3.1.1"
OID_DOT1Q_PVID = "1.3.6.1.2.1.17.7.1.4.5.1.1"

OID_LLDP_LOC_CHASSIS_ID = "1.0.8802.1.1.2.1.3.2.0"
OID_LLDP_REM_CHASSIS_ID = "1.0.8802.1.1.2.1.4.1.1.5"
OID_LLDP_REM_PORT_ID = "1.0.8802.1.1.2.1.4.1.1.7"
OID_LLDP_REM_SYS_NAME = "1.0.8802.1.1.2.1.4.1.1.9"
OID_LLDP_REM_SYS_CAP_ENABLED = "1.0.8802.1.1.2.1.4.1.1.12"
OID_LLDP_LOC_PORT_NUM = "1.0.8802.1.1.2.1.3.7.1.2"
# lldpRemManAddrTable 인덱스는 timemark.localport.remindex.addrsubtype.addrlen.addr...
# 형태로 관리주소 자체가 OID 안에 인코딩되어 있으므로, 아무 컬럼이나 walk해서
# 인덱스에서 주소를 직접 파싱한다 (컬럼 값 자체는 사용하지 않는다).
OID_LLDP_REM_MAN_ADDR_IF_ID = "1.0.8802.1.1.2.1.4.2.1.4"

OID_PETH_ADMIN_ENABLE = "1.3.6.1.2.1.105.1.1.1.3"
OID_PETH_DETECTION_STATUS = "1.3.6.1.2.1.105.1.1.1.6"
OID_PETH_POWER_CLASS = "1.3.6.1.2.1.105.1.1.1.10"

# [KOS20260920] NST(Network Solution Technologies) 장비는 표준 POWER-ETHERNET-MIB 대신
# enterprise 38333 아래의 전용 PoE 테이블을 제공한다.
OID_NST_POE_ADMIN = "1.3.6.1.4.1.38333.100.4.1.1.1"
OID_NST_POE_PORT_PRESENT = "1.3.6.1.4.1.38333.100.5.1.1.1"
OID_NST_POE_POWER = "1.3.6.1.4.1.38333.100.6.1.1.1"

_ADMIN_STATUS_MAP = {"1": "UP", "2": "DOWN", "3": "TESTING"}
_OPER_STATUS_MAP = {"1": "UP", "2": "DOWN", "3": "TESTING", "4": "UNKNOWN", "5": "UNKNOWN", "6": "DOWN", "7": "DOWN"}
_PETH_STATUS_MAP = {"1": "DISABLED", "2": "SEARCHING", "3": "DELIVERING", "4": "FAULT", "5": "TEST", "6": "FAULT"}
_PETH_CLASS_MAP = {"1": "CLASS_0", "2": "CLASS_1", "3": "CLASS_2", "4": "CLASS_3", "5": "CLASS_4"}
_STP_PORT_STATE_MAP = {
    "1": "DISABLED",
    "2": "BLOCKING",
    "3": "LISTENING",
    "4": "LEARNING",
    "5": "FORWARDING",
    "6": "BROKEN",
}


class SnmpCollector:
    def __init__(self, host: str, community: str = "public", port: int = 161, timeout: float = 2.0, retries: int = 1):
        self.host = host
        self.community = community
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self._engine = ha.SnmpEngine()

    def _auth(self):
        return ha.CommunityData(self.community, mpModel=1)

    def _target(self):
        return ha.UdpTransportTarget((self.host, self.port), timeout=self.timeout, retries=self.retries)

    async def _get(self, oid: str):
        errind, errstat, erridx, varbinds = await ha.getCmd(
            self._engine, self._auth(), self._target(), ha.ContextData(), ha.ObjectType(ha.ObjectIdentity(oid))
        )
        if errind:
            raise CollectorError(f"SNMP GET 실패 {self.host} {oid}: {errind}")
        if errstat:
            raise CollectorError(f"SNMP GET 오류 {self.host} {oid}: {errstat.prettyPrint()}")
        name, value = varbinds[0]
        if value.__class__.__name__ in ("NoSuchObject", "NoSuchInstance", "EndOfMibView"):
            raise CollectorError(f"SNMP GET no-such-object {self.host} {oid}")
        return value

    async def _walk(self, base_oid: str, max_rows: int = 20000) -> list[tuple[str, object]]:
        results: list[tuple[str, object]] = []
        current_oid = base_oid
        for _ in range(max_rows):
            errind, errstat, erridx, varbinds = await ha.nextCmd(
                self._engine,
                self._auth(),
                self._target(),
                ha.ContextData(),
                ha.ObjectType(ha.ObjectIdentity(current_oid)),
            )
            if errind or errstat:
                break
            name, value = varbinds[0][0]
            oid_str = str(name)
            if not (oid_str == base_oid or oid_str.startswith(base_oid + ".")):
                break
            if value.__class__.__name__ in ("NoSuchObject", "NoSuchInstance", "EndOfMibView"):
                break
            results.append((oid_str, value))
            current_oid = oid_str
        return results

    async def _set(self, oid: str, value) -> None:
        errind, errstat, erridx, varbinds = await ha.setCmd(
            self._engine, self._auth(), self._target(), ha.ContextData(), ha.ObjectType(ha.ObjectIdentity(oid), value)
        )
        if errind:
            raise CollectorError(f"SNMP SET 실패 {self.host} {oid}: {errind}")
        if errstat:
            raise CollectorError(f"SNMP SET 오류 {self.host} {oid}: {errstat.prettyPrint()}")

    @staticmethod
    def _suffix(oid: str, base_oid: str) -> str:
        return oid[len(base_oid) + 1 :]

    async def is_reachable(self) -> bool:
        try:
            await self._get(OID_SYS_DESCR)
            return True
        except CollectorError:
            return False

    async def get_system_info(self) -> SystemInfo:
        sys_descr = str(await self._get(OID_SYS_DESCR))
        sys_object_id = str(await self._get(OID_SYS_OBJECT_ID))
        try:
            sys_name = str(await self._get(OID_SYS_NAME))
        except CollectorError:
            sys_name = ""
        try:
            sys_location = str(await self._get(OID_SYS_LOCATION))
        except CollectorError:
            sys_location = ""
        try:
            sys_uptime = int(await self._get(OID_SYS_UPTIME))
        except CollectorError:
            sys_uptime = None

        return SystemInfo(
            sys_descr=sys_descr,
            sys_object_id=sys_object_id,
            sys_name=sys_name,
            sys_location=sys_location,
            sys_uptime=sys_uptime,
            vendor=vendor_from_sys_object_id(sys_object_id),
        )

    async def is_ip_forwarding(self) -> bool:
        try:
            value = str(await self._get(OID_IP_FORWARDING))
            return value == "1"
        except CollectorError:
            return False

    async def get_interfaces(self) -> list[InterfaceInfo]:
        descr_rows = await self._walk(OID_IF_DESCR)
        interfaces: dict[int, InterfaceInfo] = {}
        for oid, value in descr_rows:
            if_index = int(self._suffix(oid, OID_IF_DESCR))
            interfaces[if_index] = InterfaceInfo(if_index=if_index, name=str(value))

        for oid, value in await self._walk(OID_IF_PHYS_ADDR):
            if_index = int(self._suffix(oid, OID_IF_PHYS_ADDR))
            if if_index in interfaces:
                mac_bytes = value.asOctets() if hasattr(value, "asOctets") else bytes(value)
                if mac_bytes and len(mac_bytes) == 6:
                    interfaces[if_index].mac = ":".join(f"{b:02x}" for b in mac_bytes)

        for oid, value in await self._walk(OID_IF_ADMIN_STATUS):
            if_index = int(self._suffix(oid, OID_IF_ADMIN_STATUS))
            if if_index in interfaces:
                interfaces[if_index].admin_status = _ADMIN_STATUS_MAP.get(str(value), "UNKNOWN")

        for oid, value in await self._walk(OID_IF_OPER_STATUS):
            if_index = int(self._suffix(oid, OID_IF_OPER_STATUS))
            if if_index in interfaces:
                interfaces[if_index].oper_status = _OPER_STATUS_MAP.get(str(value), "UNKNOWN")

        for oid, value in await self._walk(OID_IF_SPEED):
            if_index = int(self._suffix(oid, OID_IF_SPEED))
            if if_index in interfaces:
                try:
                    interfaces[if_index].speed_mbps = int(value) // 1_000_000
                except (ValueError, TypeError):
                    pass

        for oid, value in await self._walk(OID_IF_IN_OCTETS):
            if_index = int(self._suffix(oid, OID_IF_IN_OCTETS))
            if if_index in interfaces:
                try:
                    interfaces[if_index].in_octets = int(value)
                except (ValueError, TypeError):
                    pass

        for oid, value in await self._walk(OID_IF_OUT_OCTETS):
            if_index = int(self._suffix(oid, OID_IF_OUT_OCTETS))
            if if_index in interfaces:
                try:
                    interfaces[if_index].out_octets = int(value)
                except (ValueError, TypeError):
                    pass

        # [KOS20260922] STP 미지원 장비(라우터 등)는 dot1dStpPortState 자체가
        # 없으므로 walk 결과가 그냥 빈 목록이 된다 - stp_state는 None으로 남는다.
        bridge_port_ifindex = await self._get_bridge_port_ifindex_map()
        for oid, value in await self._walk(OID_DOT1D_STP_PORT_STATE):
            bridge_port = int(self._suffix(oid, OID_DOT1D_STP_PORT_STATE))
            if_index = bridge_port_ifindex.get(bridge_port)
            if if_index in interfaces:
                interfaces[if_index].stp_state = _STP_PORT_STATE_MAP.get(str(value), "UNKNOWN")

        for oid, value in await self._walk(OID_DOT1D_STP_PORT_DESIGNATED_BRIDGE):
            bridge_port = int(self._suffix(oid, OID_DOT1D_STP_PORT_DESIGNATED_BRIDGE))
            if_index = bridge_port_ifindex.get(bridge_port)
            if if_index in interfaces:
                interfaces[if_index].stp_designated_bridge_mac = _parse_stp_designated_bridge_mac(value)

        return list(interfaces.values())

    async def get_stp_info(self) -> StpInfo:
        """BRIDGE-MIB dot1dStpRootPort==0이면 이 장비가 STP Root Bridge다(RFC 1493)."""
        try:
            root_port = int(await self._get(OID_DOT1D_STP_ROOT_PORT))
        except CollectorError:
            return StpInfo(is_root=False, supported=False)
        return StpInfo(is_root=root_port == 0, supported=True)

    async def get_local_chassis_id(self) -> str | None:
        """LLDP-MIB lldpLocChassisId (7장 장비 고유 식별자 체인의 우선순위 3)."""
        try:
            value = await self._get(OID_LLDP_LOC_CHASSIS_ID)
        except CollectorError:
            return None
        raw = value.asOctets() if hasattr(value, "asOctets") else bytes(value)
        return _format_lldp_id(raw)

    async def _get_lldp_remote_management_addresses(self) -> dict[str, str]:
        """lldpRemManAddrTable에서 (timemark.localport.remindex) -> 관리 IP를 만든다.

        주소 자체가 인덱스에 인코딩되어 있으므로 컬럼 값은 사용하지 않는다.
        IPv4(addrsubtype=1)만 지원한다.
        """
        try:
            rows = await self._walk(OID_LLDP_REM_MAN_ADDR_IF_ID)
        except CollectorError:
            return {}

        mgmt_ip_by_key: dict[str, str] = {}
        for oid, _value in rows:
            suffix = self._suffix(oid, OID_LLDP_REM_MAN_ADDR_IF_ID)
            parts = suffix.split(".")
            if len(parts) < 6:
                continue
            timemark, local_port, rem_index, addr_subtype, addr_len = parts[:5]
            addr_octets = parts[5 : 5 + int(addr_len)]
            if addr_subtype != "1" or int(addr_len) != 4 or len(addr_octets) != 4:
                continue  # IPv4 이외 주소체계는 MVP 범위에서 제외
            key = f"{timemark}.{local_port}.{rem_index}"
            mgmt_ip_by_key[key] = ".".join(addr_octets)
        return mgmt_ip_by_key

    async def get_lldp_neighbors(self) -> list[LldpNeighborInfo]:
        """LLDP-MIB lldpRemTable을 수집한다.

        인덱스 형식은 lldpRemTimeMark.lldpRemLocalPortNum.lldpRemIndex.
        [KOS20260921] lldpLocPortNum은 표준상 ifIndex가 아니라 BRIDGE-MIB의
        dot1dBasePort와 같은 값인 경우가 흔하다(get_fdb()가 이미 같은 이유로
        dot1dBasePortIfIndex를 사용해 ifIndex로 변환한다). Stack/모듈형 스위치는
        ifIndex가 "Switch N - Port M"처럼 오프셋을 갖는 벤더 스킴을 쓰는 경우가
        있어, lldpLocPortNum을 ifIndex로 그대로 쓰면 local_interface_id 매칭이
        항상 실패해 Topology 링크의 두 포트가 모두 "?"로 남는 원인이 됐다.
        BRIDGE-MIB 매핑에 있으면 변환하고, 없으면(브리지 미지원 장비 등) 기존처럼
        원값을 그대로 ifIndex로 사용한다.
        """
        try:
            chassis_rows = await self._walk(OID_LLDP_REM_CHASSIS_ID)
        except CollectorError:
            return []
        if not chassis_rows:
            return []

        port_rows = dict(await self._walk(OID_LLDP_REM_PORT_ID))
        name_rows = dict(await self._walk(OID_LLDP_REM_SYS_NAME))
        cap_rows = dict(await self._walk(OID_LLDP_REM_SYS_CAP_ENABLED))
        mgmt_ip_by_key = await self._get_lldp_remote_management_addresses()
        bridge_port_ifindex = await self._get_bridge_port_ifindex_map()

        neighbors: list[LldpNeighborInfo] = []
        for oid, value in chassis_rows:
            index_suffix = self._suffix(oid, OID_LLDP_REM_CHASSIS_ID)
            parts = index_suffix.split(".")
            local_port_num = int(parts[1]) if len(parts) >= 2 else None
            local_if_index = (
                bridge_port_ifindex.get(local_port_num, local_port_num) if local_port_num is not None else None
            )

            chassis_bytes = value.asOctets() if hasattr(value, "asOctets") else bytes(value)
            remote_chassis_id = _format_lldp_id(chassis_bytes)

            port_value = port_rows.get(f"{OID_LLDP_REM_PORT_ID}.{index_suffix}")
            remote_port_id = _format_lldp_id(port_value.asOctets()) if hasattr(port_value, "asOctets") else (
                str(port_value) if port_value is not None else None
            )

            name_value = name_rows.get(f"{OID_LLDP_REM_SYS_NAME}.{index_suffix}")
            cap_value = cap_rows.get(f"{OID_LLDP_REM_SYS_CAP_ENABLED}.{index_suffix}")

            neighbors.append(
                LldpNeighborInfo(
                    local_if_index=local_if_index,
                    remote_chassis_id=remote_chassis_id,
                    remote_port_id=remote_port_id,
                    remote_sys_name=str(name_value) if name_value is not None else None,
                    remote_mgmt_ip=mgmt_ip_by_key.get(index_suffix),
                    remote_capabilities=_format_lldp_id(cap_value.asOctets()) if hasattr(cap_value, "asOctets") else None,
                    protocol="LLDP",
                )
            )
        return neighbors

    async def get_bridge_capable(self) -> bool:
        rows = await self._walk(OID_DOT1D_BASE_PORT_IFINDEX)
        return len(rows) > 0

    async def _get_bridge_port_ifindex_map(self) -> dict[int, int]:
        """dot1dBasePort -> ifIndex 매핑 (FDB/PVID 조회에 공통으로 필요)."""
        try:
            rows = await self._walk(OID_DOT1D_BASE_PORT_IFINDEX)
        except CollectorError:
            return {}
        result: dict[int, int] = {}
        for oid, value in rows:
            bridge_port = int(self._suffix(oid, OID_DOT1D_BASE_PORT_IFINDEX))
            result[bridge_port] = int(value)
        return result

    async def get_fdb(self) -> list[FdbEntryInfo]:
        port_map = await self._get_bridge_port_ifindex_map()
        try:
            fdb_port_rows = await self._walk(OID_DOT1D_FDB_PORT)
        except CollectorError:
            fdb_port_rows = []

        # [KOS20260922] NST 스위치 일부는 CLI의 MAC 표를 Q-BRIDGE-MIB으로만
        # 제공한다. 기본 BRIDGE-MIB이 비어 있을 때 VLAN별 FDB를 수집한다.
        if not fdb_port_rows:
            try:
                q_rows = await self._walk(OID_DOT1Q_TP_FDB_PORT)
                vlan_rows = await self._walk(OID_DOT1Q_VLAN_CURRENT_FDB_ID)
            except CollectorError:
                return []
            vlan_by_fdb_id: dict[int, int] = {}
            for oid, value in vlan_rows:
                suffix = self._suffix(oid, OID_DOT1Q_VLAN_CURRENT_FDB_ID).split(".")
                if len(suffix) != 2:
                    continue
                try:
                    vlan_by_fdb_id[int(value)] = int(suffix[1])
                except ValueError:
                    continue
            entries: list[FdbEntryInfo] = []
            for oid, value in q_rows:
                suffix = self._suffix(oid, OID_DOT1Q_TP_FDB_PORT).split(".")
                if len(suffix) != 7:
                    continue
                try:
                    fdb_id, *mac_octets = (int(part) for part in suffix)
                    bridge_port = int(value)
                except ValueError:
                    continue
                if not all(0 <= octet <= 255 for octet in mac_octets) or bridge_port == 0:
                    continue
                mac = ":".join(f"{octet:02x}" for octet in mac_octets)
                entries.append(FdbEntryInfo(
                    if_index=port_map.get(bridge_port), vlan=vlan_by_fdb_id.get(fdb_id),
                    mac=mac, entry_type="DYNAMIC",
                ))
            return entries

        entries: list[FdbEntryInfo] = []
        for oid, value in fdb_port_rows:
            mac_suffix = self._suffix(oid, OID_DOT1D_FDB_PORT)
            mac_octets = [int(x) for x in mac_suffix.split(".")]
            if len(mac_octets) != 6:
                continue
            mac = ":".join(f"{b:02x}" for b in mac_octets)
            bridge_port = int(value)
            if_index = port_map.get(bridge_port)
            entries.append(FdbEntryInfo(if_index=if_index, vlan=None, mac=mac, entry_type="DYNAMIC"))
        return entries

    async def get_vlans(self) -> list[int]:
        """Q-BRIDGE-MIB dot1qVlanStaticTable 기반 VLAN 목록 (9장 VLAN 수 채점 근거)."""
        try:
            rows = await self._walk(OID_DOT1Q_VLAN_STATIC_NAME)
        except CollectorError:
            return []
        vlan_ids: list[int] = []
        for oid, _value in rows:
            suffix = self._suffix(oid, OID_DOT1Q_VLAN_STATIC_NAME)
            try:
                vlan_ids.append(int(suffix))
            except ValueError:
                continue
        return vlan_ids

    async def get_port_pvids(self) -> dict[int, int]:
        """Q-BRIDGE-MIB dot1qPvid를 ifIndex 기준으로 매핑한다 (Port의 Access/Native VLAN)."""
        try:
            rows = await self._walk(OID_DOT1Q_PVID)
        except CollectorError:
            return {}
        port_map = await self._get_bridge_port_ifindex_map()
        result: dict[int, int] = {}
        for oid, value in rows:
            bridge_port = int(self._suffix(oid, OID_DOT1Q_PVID))
            if_index = port_map.get(bridge_port)
            if if_index is not None:
                result[if_index] = int(value)
        return result

    async def get_arp_table(self) -> list[ArpEntryInfo]:
        try:
            rows = await self._walk(OID_IP_NET_TO_MEDIA_PHYS)
        except CollectorError:
            return []

        entries: list[ArpEntryInfo] = []
        for oid, value in rows:
            suffix = self._suffix(oid, OID_IP_NET_TO_MEDIA_PHYS)
            parts = suffix.split(".")
            if len(parts) < 5:
                continue
            if_index = int(parts[0])
            ip = ".".join(parts[1:5])
            mac_bytes = value.asOctets() if hasattr(value, "asOctets") else bytes(value)
            if len(mac_bytes) != 6:
                continue
            mac = ":".join(f"{b:02x}" for b in mac_bytes)
            entries.append(ArpEntryInfo(if_index=if_index, ip=ip, mac=mac))
        return entries

    async def get_ip_interface_count(self) -> int:
        """ipAddrTable에 등록된 IP-인터페이스(SVI 포함) 개수.

        관리 IP 하나만 있는 순수 L2 스위치도 최소 1개는 갖는다. 값 자체보다는
        discovery/engine.py에서 (count - 1)로 '추가 SVI 존재 여부'를 판단하는
        용도로 쓰인다.
        """
        try:
            rows = await self._walk(OID_IP_ADDR_IFINDEX)
        except CollectorError:
            return 0
        return len(rows)

    async def get_routes(self) -> list[RouteInfo]:
        try:
            dest_rows = await self._walk(OID_IP_ROUTE_DEST)
        except CollectorError:
            return []

        ifindex_rows = dict(await self._walk(OID_IP_ROUTE_IFINDEX))
        nexthop_rows = dict(await self._walk(OID_IP_ROUTE_NEXTHOP))
        mask_rows = dict(await self._walk(OID_IP_ROUTE_MASK))
        type_rows = dict(await self._walk(OID_IP_ROUTE_TYPE))

        routes: list[RouteInfo] = []
        for oid, value in dest_rows:
            suffix = self._suffix(oid, OID_IP_ROUTE_DEST)
            dest = value.prettyPrint()
            mask_value = mask_rows.get(f"{OID_IP_ROUTE_MASK}.{suffix}")
            mask = mask_value.prettyPrint() if mask_value is not None else "255.255.255.255"
            prefix_len = sum(bin(int(o)).count("1") for o in mask.split("."))
            nexthop_value = nexthop_rows.get(f"{OID_IP_ROUTE_NEXTHOP}.{suffix}")
            next_hop = nexthop_value.prettyPrint() if nexthop_value is not None else None
            if_index_raw = ifindex_rows.get(f"{OID_IP_ROUTE_IFINDEX}.{suffix}")
            route_type_raw = str(type_rows.get(f"{OID_IP_ROUTE_TYPE}.{suffix}", "3"))
            routes.append(
                RouteInfo(
                    destination=dest,
                    prefix_len=prefix_len,
                    next_hop=next_hop,
                    if_index=int(if_index_raw) if if_index_raw is not None else None,
                    route_type="DIRECT" if route_type_raw == "3" else "STATIC",
                )
            )
        return routes

    async def get_poe_status(self) -> list[PoeInfo]:
        try:
            enable_rows = await self._walk(OID_PETH_ADMIN_ENABLE)
        except CollectorError:
            enable_rows = []

        if not enable_rows:
            return await self._get_nst_poe_status()

        status_rows = dict(await self._walk(OID_PETH_DETECTION_STATUS))
        class_rows = dict(await self._walk(OID_PETH_POWER_CLASS))

        results: list[PoeInfo] = []
        for oid, value in enable_rows:
            suffix = self._suffix(oid, OID_PETH_ADMIN_ENABLE)
            parts = suffix.split(".")
            # pethPsePortIndex를 ifIndex의 근사값으로 사용한다(단일 Group 장비 가정).
            # 다중 모듈/스택 장비의 정확한 매핑은 Vendor Driver 확장이 필요하다.
            port_index = int(parts[-1]) if parts else 0
            status_raw = str(status_rows.get(f"{OID_PETH_DETECTION_STATUS}.{suffix}", "1"))
            class_raw = class_rows.get(f"{OID_PETH_POWER_CLASS}.{suffix}")
            results.append(
                PoeInfo(
                    if_index=port_index,
                    enabled=str(value) == "1",
                    status=_PETH_STATUS_MAP.get(status_raw, "UNKNOWN"),
                    poe_class=_PETH_CLASS_MAP.get(str(class_raw)) if class_raw is not None else None,
                )
            )
        return results

    async def _get_nst_poe_status(self) -> list[PoeInfo]:
        """enterprise 38333 기반 NST 스위치의 PoE 포트 상태를 수집한다."""
        try:
            port_rows = await self._walk(OID_NST_POE_PORT_PRESENT)
        except CollectorError:
            port_rows = []

        admin_rows = dict(await self._walk(OID_NST_POE_ADMIN))
        power_rows = dict(await self._walk(OID_NST_POE_POWER))
        if not port_rows and power_rows:
            # [KOS20260920] 일부 NHM 펌웨어는 포트 판별 테이블을 생략하지만 전력
            # 테이블에는 마지막 2개 SFP 업링크까지 포함한다. 전력 테이블을 대신
            # 사용하되 SFP 2개를 제외해 PoE 구리 포트만 만든다.
            port_rows = list(power_rows.items())[:-2]
        if not port_rows:
            return []
        results: list[PoeInfo] = []
        for oid, _value in port_rows:
            source_oid = OID_NST_POE_PORT_PRESENT if oid.startswith(OID_NST_POE_PORT_PRESENT + ".") else OID_NST_POE_POWER
            suffix = self._suffix(oid, source_oid)
            port_index = int(suffix.split(".")[-1])
            admin_raw = admin_rows.get(f"{OID_NST_POE_ADMIN}.{suffix}")
            power_raw = power_rows.get(f"{OID_NST_POE_POWER}.{suffix}")
            enabled = str(admin_raw) == "1" if admin_raw is not None else True
            try:
                # [KOS20260920] 실장비 비교 결과 이 값은 0.1W 단위로 노출된다.
                power_mw = float(power_raw) * 100 if power_raw is not None else None
            except (TypeError, ValueError):
                power_mw = None
            status = "DISABLED" if not enabled else "DELIVERING" if power_mw and power_mw > 0 else "SEARCHING"
            results.append(
                PoeInfo(
                    if_index=port_index,
                    enabled=enabled,
                    status=status,
                    power_mw=power_mw,
                )
            )
        return results

    async def set_if_admin_status(self, if_index: int, enable: bool) -> None:
        """13.1절: SNMP IF-MIB::ifAdminStatus SET (1=up, 2=down)."""
        await self._set(f"{OID_IF_ADMIN_STATUS}.{if_index}", ha.Integer(1 if enable else 2))

    async def set_poe_admin_enable(self, peth_port_index: int, enable: bool) -> None:
        """13.2절: POWER-ETHERNET-MIB::pethPsePortAdminEnable SET (1=true, 2=false)."""
        await self._set(f"{OID_PETH_ADMIN_ENABLE}.1.{peth_port_index}", ha.Integer(1 if enable else 2))

    async def set_port_pvid(self, if_index: int, vlan: int) -> None:
        """13.x절: Q-BRIDGE-MIB::dot1qPvid SET - 포트의 Access/Native VLAN(PVID)을
        바꾼다. get_port_pvids()와 마찬가지로 이 테이블은 ifIndex가 아니라
        dot1dBasePort로 색인되므로, 먼저 dot1dBasePortIfIndex로 역매핑해야 한다."""
        bridge_port_ifindex = await self._get_bridge_port_ifindex_map()
        bridge_port = next((bp for bp, idx in bridge_port_ifindex.items() if idx == if_index), None)
        if bridge_port is None:
            raise CollectorError(f"{self.host}: ifIndex {if_index}에 대응하는 BRIDGE-MIB 포트를 찾을 수 없습니다.")
        await self._set(f"{OID_DOT1Q_PVID}.{bridge_port}", ha.Integer(vlan))

    async def get_if_alias(self, if_index: int) -> str | None:
        """13.x절: IF-MIB::ifAlias(ifXTable) 단일 조회 - SET 검증용. ifAlias
        미지원/빈 문자열이면 None으로 취급한다."""
        try:
            value = await self._get(f"{OID_IF_ALIAS}.{if_index}")
        except CollectorError:
            return None
        text = str(value)
        return text or None

    async def set_if_alias(self, if_index: int, description: str) -> None:
        """13.x절: IF-MIB::ifAlias SET - 포트 설명(Description)을 바꾼다."""
        await self._set(f"{OID_IF_ALIAS}.{if_index}", ha.OctetString(description))


# [KOS20260921] sysObjectID(1.3.6.1.4.1.<enterprise>...)의 enterprise 번호로
# vendor를 표시해 달라는 요청 - 추측이 아니라 이 코드베이스가 이미 확인해 둔
# 값만 넣는다. 38333은 OID_NST_POE_* 전용 MIB 주석("enterprise 38333 아래의
# NST 전용 PoE 테이블")에서 이미 NST로 확인된 값이다. 모르는 enterprise 번호는
# 억지로 추측하지 않고 vendor를 비워 둔다(필요 시 확인된 값만 추가).
_ENTERPRISE_VENDOR = {
    "38333": "NST",
}


def vendor_from_sys_object_id(sys_object_id: str) -> str | None:
    prefix = "1.3.6.1.4.1."
    if not sys_object_id.startswith(prefix):
        return None
    enterprise = sys_object_id[len(prefix):].split(".", 1)[0]
    return _ENTERPRISE_VENDOR.get(enterprise)


def _format_lldp_id(value) -> str | None:
    if value is None:
        return None
    try:
        raw = value if isinstance(value, (bytes, bytearray)) else bytes(value)
    except TypeError:
        return str(value)
    if all(32 <= b < 127 for b in raw) and raw:
        return raw.decode("ascii", errors="ignore")
    return ":".join(f"{b:02x}" for b in raw)


def _parse_stp_designated_bridge_mac(value) -> str | None:
    """dot1dStpPortDesignatedBridge(BridgeId = Priority 2바이트 + MAC 6바이트,
    총 8바이트 raw binary)에서 MAC만 뽑아낸다.

    [KOS20260922] RFC 1493 표준은 raw binary 8바이트지만, 실사용 장비(이 벤더
    스위치 라인)는 "priority-XX.XX.XX.XX.XX.XX" 형태의 ASCII 문자열로 인코딩해
    응답한다(snmpwalk가 이 OID를 Hex-STRING이 아니라 STRING으로 표시하는 것으로
    확인 - 최초 raw-binary만 가정한 구현은 이 문자열을 그대로 8~19바이트로 오
    해석해 "2e:30:30:2e:30:30"처럼 텍스트 일부를 MAC인 척 잘못 뽑아내는 버그가
    있었다). 두 형식을 모두 시도한다.

    값이 전부 0(00:00:...:00)이면 이 포트에 아직 STP가 알아낸 Designated
    Bridge가 없다는 뜻이라 None을 반환한다.
    """
    try:
        raw = value.asOctets() if hasattr(value, "asOctets") else bytes(value)
    except TypeError:
        return None

    if len(raw) == 8:
        mac_bytes = raw[-6:]
        if all(b == 0 for b in mac_bytes):
            return None
        return ":".join(f"{b:02x}" for b in mac_bytes)

    try:
        text = raw.decode("ascii")
    except (UnicodeDecodeError, AttributeError):
        return None
    _, sep, mac_part = text.partition("-")
    if not sep:
        return None
    hex_bytes = mac_part.split(".")
    if len(hex_bytes) != 6:
        return None
    try:
        mac_ints = [int(b, 16) for b in hex_bytes]
    except ValueError:
        return None
    if all(b == 0 for b in mac_ints):
        return None
    return ":".join(f"{b:02x}" for b in mac_ints)
