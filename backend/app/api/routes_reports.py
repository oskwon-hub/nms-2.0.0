"""Reports 화면 데이터: 18.1절 Audit 요구사항("모든 SET/CLI 변경은 사용자·시간·
전후값·결과 기록")을 조회 가능하게 하는 엔드포인트. 16장 표에는 없지만 Audit Log를
실제로 "볼 수 있어야" 완결되므로 합리적으로 추가한다. 재고/Discovery 이력 요약은
기존 /api/devices, /api/discovery로 프론트에서 집계하므로 별도 엔드포인트가
필요 없다."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import db_session_dependency
from app.models import DeviceControlLog
from app.schemas import BulkDeleteIn, BulkDeleteOut, ControlLogOut

router = APIRouter(tags=["reports"])


@router.get("/control-logs", response_model=list[ControlLogOut])
def list_control_logs(
    device_id: Optional[int] = None,
    result: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(db_session_dependency),
):
    stmt = select(DeviceControlLog).order_by(DeviceControlLog.created_at.desc())
    if device_id is not None:
        stmt = stmt.where(DeviceControlLog.device_id == device_id)
    if result:
        stmt = stmt.where(DeviceControlLog.result == result.upper())
    stmt = stmt.limit(limit)
    return session.scalars(stmt).all()


@router.post("/control-logs/bulk-delete", response_model=BulkDeleteOut)
def bulk_delete_control_logs(payload: BulkDeleteIn, session: Session = Depends(db_session_dependency)):
    """18.1절 Audit Log는 감사 목적 근거지만, 실사용 화면 정리 요청에 따라 운영자가
    직접 삭제할 수 있게 한다(별도 접근 통제 없이 배포하는 이번 범위의 한계는
    ai-log에 기록)."""
    logs = session.scalars(select(DeviceControlLog).where(DeviceControlLog.id.in_(payload.ids))).all()
    deleted = [log.id for log in logs]
    for log in logs:
        session.delete(log)
    session.commit()
    return BulkDeleteOut(deleted=deleted)


@router.post("/control-logs/delete-all", response_model=BulkDeleteOut)
def delete_all_control_logs(session: Session = Depends(db_session_dependency)):
    logs = session.scalars(select(DeviceControlLog)).all()
    deleted = [log.id for log in logs]
    for log in logs:
        session.delete(log)
    session.commit()
    return BulkDeleteOut(deleted=deleted)
