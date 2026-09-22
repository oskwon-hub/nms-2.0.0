"""[KOS20260921] 저장된 근거만으로 기존 장비를 재분류한다(네트워크 재탐색 없음).

분류 로직(NSH/NHM 명명 규칙 강한 시그니처, L2/L3 게이팅, PoE 오탐 수정 등)을
수정해도 이미 DB에 저장된 장비는 다음 Discovery 재실행 전까지 예전 결과를 그대로
보여준다(예: "NSH2228CF"가 실제로는 L2_SWITCH인데 예전 규칙으로 분류된 채 UNKNOWN
목록에 남아 있던 사례). 매번 재탐색을 요구하지 않고 즉시 반영할 수 있도록, 이미
저장되어 있는 Bridge/FDB/LLDP/Route/VLAN/PoE/hostname 근거만으로
classify_device_type()을 다시 호출한다.

한계: ICMP TTL/SSH 배너/SMB-RDP/ONVIF 등 PC·카메라 식별에 쓰는 무자격 프로브
결과는 애초에 DB에 저장하지 않으므로(휘발성 근거), 이 함수로는 복구할 수 없다.
그런 장비(대부분 device_type=UNKNOWN인 PC)는 재탐색이 필요하다.
"""
from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.classification.device_type import ClassificationEvidence, classify_device_type
from app.collectors.snmp_collector import vendor_from_sys_object_id
from app.models import DeviceInterface, LldpNeighbor, MacFdb, NetworkDevice, PoePort, RouteEntry


def reclassify_device_from_stored_evidence(session: Session, device: NetworkDevice) -> bool:
    """device_type/classification_*을 저장된 근거 기준으로 재계산한다.

    반환값은 device_type이 실제로 바뀌었는지 여부(호출자가 변경 건수를 셀 때 사용).
    """
    fdb_count = session.scalar(select(func.count()).select_from(MacFdb).where(MacFdb.device_id == device.id)) or 0
    lldp_count = (
        session.scalar(select(func.count()).select_from(LldpNeighbor).where(LldpNeighbor.local_device_id == device.id))
        or 0
    )
    interfaces = session.scalars(select(DeviceInterface).where(DeviceInterface.device_id == device.id)).all()
    routes = session.scalars(select(RouteEntry).where(RouteEntry.device_id == device.id)).all()

    evidence = ClassificationEvidence(
        has_bridge_mib=device.layer2_capable,
        has_fdb=fdb_count > 0,
        has_lldp=lldp_count > 0,
        physical_port_count=len(interfaces),
        has_ip_forwarding=device.layer3_capable,
        route_table_size=len(routes),
        svi_or_l3_if_count=len({r.interface_id for r in routes if r.interface_id is not None}),
        vlan_count=device.vlan_count,
        has_wan_or_default_route=any(r.destination == "0.0.0.0" and r.prefix_len == 0 for r in routes),
        bridge_capability_weak=not device.layer2_capable,
        has_poe_mib=device.poe_capable,
        hostname=device.hostname or "",
        has_management_response=True,
    )
    result = classify_device_type(evidence)
    changed = result.device_type != device.device_type
    device.device_type = result.device_type
    device.classification_score = result.score
    device.classification_method = result.method
    device.classification_detail = json.dumps(result.detail, ensure_ascii=False)

    # [KOS20260921] 원시 SNMP 근거(NST 사설 PoE MIB 오탐 등) 기준으로 이미 저장돼
    # 있던 device.poe_capable/DeviceInterface.poe_capable/PoePort가, 명명 규칙까지
    # 반영한 최종 device_type과 어긋나 있을 수 있다(예: L2_SWITCH로 확정됐는데
    # 인터페이스는 여전히 poe_capable=True로 남아 Devices 상세의 Interfaces/PoE 탭에
    # PoE On/Off 제어가 잘못 노출됨). 최종 결과 기준으로 다시 맞춘다.
    device.poe_capable = result.device_type in ("L2_POE_SWITCH", "L3_POE_SWITCH")
    if not device.poe_capable:
        session.query(DeviceInterface).filter(DeviceInterface.device_id == device.id).update(
            {DeviceInterface.poe_capable: False}, synchronize_session=False
        )
        session.query(PoePort).filter(PoePort.device_id == device.id).delete()

    # [KOS20260921] vendor는 sys_object_id(이미 저장됨)만으로 재탐색 없이도
    # 바로 채울 수 있다("장비 정보에 모델명이 있는데 표시가 안 된다" 리포트 -
    # sysDescr는 vendor마다 형식이 달라 model까지 자동으로 파싱하진 않지만,
    # vendor는 enterprise 번호로 확인된 값만 채운다).
    if not device.vendor and device.sys_object_id:
        vendor = vendor_from_sys_object_id(device.sys_object_id)
        if vendor:
            device.vendor = vendor

    return changed


def reclassify_all_devices(session: Session) -> int:
    """모든 장비를 저장된 근거로 재분류하고, device_type이 바뀐 개수를 반환한다."""
    changed_count = 0
    for device in session.scalars(select(NetworkDevice)):
        if reclassify_device_from_stored_evidence(session, device):
            changed_count += 1
    return changed_count
