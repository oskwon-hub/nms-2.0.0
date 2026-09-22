"""Settings 화면 데이터: 18.1절 Credential Profile 관리 + 읽기 전용 시스템 정보.
16장 표에는 없지만 Settings 메뉴 구현을 위해 합리적으로 추가한다. Credential
Profile은 절대 평문(SNMP community, SSH 사용자명/암호)을 응답에 포함하지 않는다."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.credentials import create_named_credential_profile, set_cli_credential
from app.db import db_session_dependency
from app.models import CredentialProfile, SchemaVersion
from app.schemas import BulkDeleteIn, BulkDeleteOut, CredentialProfileCreateIn, CredentialProfileOut, CredentialProfileSshUpdateIn, SystemInfoOut

router = APIRouter(tags=["settings"])


@router.get("/credential-profiles", response_model=list[CredentialProfileOut])
def list_credential_profiles(session: Session = Depends(db_session_dependency)):
    stmt = select(CredentialProfile).order_by(CredentialProfile.created_at.desc())
    return session.scalars(stmt).all()


@router.post("/credential-profiles", response_model=CredentialProfileOut)
def create_credential_profile(payload: CredentialProfileCreateIn, session: Session = Depends(db_session_dependency)):
    try:
        profile = create_named_credential_profile(session, payload.snmp_community, payload.name)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=f"이미 존재하는 이름입니다: {payload.name}") from exc
    session.refresh(profile)
    return profile


@router.patch("/credential-profiles/{profile_id}/ssh", response_model=CredentialProfileOut)
def update_credential_profile_ssh(
    profile_id: int,
    payload: CredentialProfileSshUpdateIn,
    session: Session = Depends(db_session_dependency),
):
    profile = session.get(CredentialProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Credential Profile을 찾을 수 없습니다: {profile_id}")
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="SSH 사용자명을 입력해 주세요.")
    protocol = payload.protocol.upper()
    if protocol not in {"SSH", "TELNET"}:
        raise HTTPException(status_code=400, detail="CLI 프로토콜은 SSH 또는 TELNET이어야 합니다.")
    set_cli_credential(profile, username, payload.password, payload.port, protocol)
    session.commit()
    session.refresh(profile)
    return profile


@router.post("/credential-profiles/bulk-delete", response_model=BulkDeleteOut)
def bulk_delete_credential_profiles(payload: BulkDeleteIn, session: Session = Depends(db_session_dependency)):
    profiles = session.scalars(select(CredentialProfile).where(CredentialProfile.id.in_(payload.ids))).all()
    deleted = [p.id for p in profiles]
    for profile in profiles:
        session.delete(profile)
    session.commit()
    return BulkDeleteOut(deleted=deleted)


@router.post("/credential-profiles/delete-all", response_model=BulkDeleteOut)
def delete_all_credential_profiles(session: Session = Depends(db_session_dependency)):
    profiles = session.scalars(select(CredentialProfile)).all()
    deleted = [p.id for p in profiles]
    for profile in profiles:
        session.delete(profile)
    session.commit()
    return BulkDeleteOut(deleted=deleted)


@router.delete("/credential-profiles/{profile_id}", status_code=204)
def delete_credential_profile(profile_id: int, session: Session = Depends(db_session_dependency)):
    profile = session.get(CredentialProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Credential Profile을 찾을 수 없습니다: {profile_id}")
    session.delete(profile)
    session.commit()


@router.get("/system-info", response_model=SystemInfoOut)
def get_system_info(session: Session = Depends(db_session_dependency)):
    version_row = session.get(SchemaVersion, 1)
    return SystemInfoOut(
        schema_version=version_row.version if version_row else 0,
        db_path=config.describe_database(),
        max_seed_hosts=config.MAX_SEED_HOSTS,
        max_discovery_depth=config.MAX_DISCOVERY_DEPTH,
        classification_threshold=config.CLASSIFICATION_THRESHOLD,
        classification_review_floor=config.CLASSIFICATION_REVIEW_FLOOR,
        core_score_threshold=config.CORE_SCORE_THRESHOLD,
        floor_score_threshold=config.FLOOR_SCORE_THRESHOLD,
        stale_after_seconds=config.STALE_AFTER_SECONDS,
        offline_after_seconds=config.OFFLINE_AFTER_SECONDS,
        auto_discovery_enabled=config.AUTO_DISCOVERY_ENABLED,
        auto_discovery_interval_seconds=config.AUTO_DISCOVERY_INTERVAL_SECONDS,
    )
