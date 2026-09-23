"""구성 관리(Configuration Management) 화면: 구성 변경 이력 조회.

DeviceControlLog(포트/PoE 제어 Audit Log, routes_reports.py)와 달리, 이 로그는
"NMS가 직접 실행한 제어" 뿐 아니라 "재탐색이 장비 쪽 값 변경을 감지"한 것과
"운영자가 Role 등을 API로 직접 바꾼 것"까지 아우른다(app/config_history.py).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.control.config_restore import (
    ConfigChangeNotFoundError,
    UnsupportedRestoreFieldError,
    restore_config_change,
)
from app.db import db_session_dependency
from app.models import ConfigChangeLog
from app.schemas import BulkDeleteIn, BulkDeleteOut, ConfigChangeLogOut, RestoreConfigChangeIn, RestoreResultOut

router = APIRouter(tags=["config"])


@router.get("/config-changes", response_model=list[ConfigChangeLogOut])
def list_config_changes(
    device_id: Optional[int] = None,
    field_name: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(db_session_dependency),
):
    stmt = select(ConfigChangeLog).order_by(ConfigChangeLog.detected_at.desc())
    if device_id is not None:
        stmt = stmt.where(ConfigChangeLog.device_id == device_id)
    if field_name:
        stmt = stmt.where(ConfigChangeLog.field_name == field_name)
    if source:
        stmt = stmt.where(ConfigChangeLog.source == source.upper())
    stmt = stmt.limit(limit)
    return session.scalars(stmt).all()


@router.post("/config-changes/{change_id}/restore", response_model=RestoreResultOut)
async def restore_config_change_endpoint(
    change_id: int, payload: RestoreConfigChangeIn, session: Session = Depends(db_session_dependency)
):
    try:
        result = await restore_config_change(session, change_id, payload.performed_by, force=payload.force)
    except ConfigChangeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except UnsupportedRestoreFieldError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RestoreResultOut(**result)


@router.post("/config-changes/bulk-delete", response_model=BulkDeleteOut)
def bulk_delete_config_changes(payload: BulkDeleteIn, session: Session = Depends(db_session_dependency)):
    logs = session.scalars(select(ConfigChangeLog).where(ConfigChangeLog.id.in_(payload.ids))).all()
    deleted = [log.id for log in logs]
    for log in logs:
        session.delete(log)
    session.commit()
    return BulkDeleteOut(deleted=deleted)


@router.post("/config-changes/delete-all", response_model=BulkDeleteOut)
def delete_all_config_changes(session: Session = Depends(db_session_dependency)):
    logs = session.scalars(select(ConfigChangeLog)).all()
    deleted = [log.id for log in logs]
    for log in logs:
        session.delete(log)
    session.commit()
    return BulkDeleteOut(deleted=deleted)
