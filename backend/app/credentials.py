"""18.1절: SNMP/CLI Credential Profile 조회/생성 및 복호화.

장비는 SNMP community를 직접 갖지 않고 credential_profile_id로 참조한다. 동일한
community 문자열은 하나의 CredentialProfile로 재사용(중복 생성 방지)한다.
CLI 인증정보는 링크 From→To Ping 실행 시 From 장비의 SSH/Telnet 접속에 사용한다.
"""
from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import CredentialProfile, NetworkDevice
from app.security import decrypt_secret, encrypt_secret


def set_cli_credential(
    profile: CredentialProfile,
    username: str,
    password: str,
    port: int,
    protocol: str = "SSH",
) -> None:
    protocol = protocol.upper()
    if protocol not in {"SSH", "TELNET"}:
        raise ValueError(f"지원하지 않는 CLI 프로토콜: {protocol}")
    profile.ssh_username_encrypted = encrypt_secret(username)
    profile.ssh_password_encrypted = encrypt_secret(password)
    profile.ssh_port = port
    profile.cli_protocol = protocol


def set_ssh_credential(profile: CredentialProfile, username: str, password: str, port: int = 22) -> None:
    set_cli_credential(profile, username, password, port, "SSH")


SSH_DEFAULT_PROFILE_NAMES = {
    "NSH": "ssh-default-nsh",
    "NHM": "ssh-default-nhm",
}


def _resolved_ssh_profile(profile: CredentialProfile | None) -> tuple[str, str, int, str] | None:
    if profile is None or not profile.has_cli:
        return None
    return (
        decrypt_secret(profile.ssh_username_encrypted),
        decrypt_secret(profile.ssh_password_encrypted),
        profile.ssh_port,
        profile.cli_protocol,
    )


def resolve_ssh_credentials(session: Session, device: NetworkDevice) -> list[tuple[str, str, int, str]]:
    """장비별 CLI 접속 후보를 우선순위대로 반환한다.

    NHM/NSH 장비는 현장 기본 Profile을 먼저 사용하고, 간혹 암호가 서로 바뀌는
    경우를 위해 반대 Prefix Profile도 후순위 후보로 둔다. 마지막으로 Discovery의
    SNMP Profile에 함께 저장된 CLI 정보도 후보로 사용한다.
    """
    identity = " ".join(filter(None, (device.hostname, device.model))).upper()
    prefix = next((key for key in SSH_DEFAULT_PROFILE_NAMES if identity.startswith(key)), None)
    ordered_names: list[str] = []
    if prefix:
        ordered_names.append(SSH_DEFAULT_PROFILE_NAMES[prefix])
        ordered_names.extend(name for key, name in SSH_DEFAULT_PROFILE_NAMES.items() if key != prefix)

    profiles: list[CredentialProfile] = []
    for name in ordered_names:
        profile = session.scalar(select(CredentialProfile).where(CredentialProfile.name == name))
        if profile is not None:
            profiles.append(profile)
    if device.credential_profile_id:
        attached = session.get(CredentialProfile, device.credential_profile_id)
        if attached is not None and all(profile.id != attached.id for profile in profiles):
            profiles.append(attached)

    resolved: list[tuple[str, str, int, str]] = []
    for profile in profiles:
        credential = _resolved_ssh_profile(profile)
        if credential is not None and credential not in resolved:
            resolved.append(credential)
    return resolved


def resolve_ssh_credential(session: Session, device: NetworkDevice) -> tuple[str, str, int, str] | None:
    """기존 단일 Credential 호출부를 위한 첫 번째 후보 반환 함수."""
    credentials = resolve_ssh_credentials(session, device)
    return credentials[0] if credentials else None


def _profile_name_for_community(community: str) -> str:
    # community 원문을 이름에 노출하지 않도록 해시로 식별자를 만든다.
    digest = hashlib.sha256(community.encode("utf-8")).hexdigest()[:12]
    return f"snmp-auto-{digest}"


def get_or_create_snmp_credential_profile(session: Session, community: str) -> CredentialProfile:
    name = _profile_name_for_community(community)
    existing = session.scalar(select(CredentialProfile).where(CredentialProfile.name == name))
    if existing is not None:
        return existing

    profile = CredentialProfile(name=name, snmp_community_encrypted=encrypt_secret(community))
    session.add(profile)
    session.flush()
    return profile


def create_named_credential_profile(session: Session, community: str, name: str | None = None) -> CredentialProfile:
    """Settings 화면에서 운영자가 직접 만드는 Credential Profile. 이름을 지정하지
    않으면 자동 생성/재사용 규칙(get_or_create_snmp_credential_profile)을 따른다."""
    if not name:
        return get_or_create_snmp_credential_profile(session, community)

    profile = CredentialProfile(name=name, snmp_community_encrypted=encrypt_secret(community))
    session.add(profile)
    session.flush()
    return profile


def resolve_snmp_community(session: Session, device: NetworkDevice) -> str:
    """장비에 연결된 Credential을 복호화해 실제 SNMP community를 얻는다.

    우선순위: credential_profile_id(신규, 암호화) > snmp_community(레거시 평문,
    v2 이전 데이터) > 전역 기본값.
    """
    if device.credential_profile_id:
        profile = session.get(CredentialProfile, device.credential_profile_id)
        if profile is not None and profile.snmp_community_encrypted:
            return decrypt_secret(profile.snmp_community_encrypted)
    if device.snmp_community:
        return device.snmp_community
    return config.DEFAULT_SNMP_COMMUNITY
