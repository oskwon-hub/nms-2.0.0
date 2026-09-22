"""MAC OUI(제조사 접두어) 기반 보조 증거 (4장/8장: Camera/AP/Apple OUI).

IEEE 전체 OUI 테이블을 내장하는 대신, 8장 채점 규칙에서 실제로 사용하는
카테고리(CAMERA/APPLE/AP)에 한해 널리 알려진 대표 벤더 접두어만 유지한다.
운영 중 정확도가 부족하면 이 테이블을 확장하거나 외부 OUI DB 연동으로 교체한다.
"""
from __future__ import annotations

_CAMERA_OUIS = {
    "00:40:8c",  # Axis
    "ac:cc:8e",  # Axis
    "00:0f:7c",  # Axis
    "bc:b8:63",  # Hikvision
    "44:19:b6",  # Hikvision
    "c0:56:e3",  # Dahua
    "3c:ef:8c",  # Dahua
    "00:1c:aa",  # Cisco/Vivotek 계열
}

_APPLE_OUIS = {
    "3c:22:fb",
    "a4:83:e7",
    "f0:18:98",
    "d8:96:95",
    "ac:bc:32",
}

_AP_OUIS = {
    "00:18:0a",  # Ubiquiti
    "24:a4:3c",  # Ubiquiti
    "f0:9f:c2",  # Ubiquiti
    "00:0b:86",  # Aruba
    "94:b4:0f",  # Aruba
    "00:1a:1e",  # Ruckus
}


def classify_oui(mac: str | None) -> str | None:
    if not mac:
        return None
    prefix = mac.lower()[:8]
    if prefix in _CAMERA_OUIS:
        return "CAMERA"
    if prefix in _APPLE_OUIS:
        return "APPLE"
    if prefix in _AP_OUIS:
        return "AP"
    return None
