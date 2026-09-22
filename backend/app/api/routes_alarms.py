"""18.1/12.1절: Alarms 화면 데이터. 16장 표에는 없던 엔드포인트지만, 18.1절
Notification Trigger 요구사항과 Alarms 메뉴 구현을 위해 합리적으로 추가한다."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alarms import Alarm, list_alarms
from app.db import db_session_dependency
from app.models import DismissedAlarm, utcnow
from app.schemas import AlarmDismissIn, AlarmOut, DismissOut

router = APIRouter(tags=["alarms"])


def _filter_dismissed(session: Session, alarms: list[Alarm]) -> list[Alarm]:
    if not alarms:
        return alarms
    dismissed = session.scalars(
        select(DismissedAlarm).where(DismissedAlarm.alarm_id.in_({a.id for a in alarms}))
    ).all()
    dismissed_keys = {(d.alarm_id, d.occurred_at) for d in dismissed}
    return [a for a in alarms if (a.id, a.occurred_at) not in dismissed_keys]


@router.get("/alarms", response_model=list[AlarmOut])
def get_alarms(
    severity: Optional[str] = None,
    category: Optional[str] = None,
    include_suppressed: bool = True,
    include_dismissed: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(db_session_dependency),
):
    alarms = list_alarms(session)
    if severity:
        alarms = [a for a in alarms if a.severity == severity.upper()]
    if category:
        alarms = [a for a in alarms if a.category == category.upper()]
    if not include_suppressed:
        alarms = [a for a in alarms if not a.suppressed]
    if not include_dismissed:
        alarms = _filter_dismissed(session, alarms)
    return alarms[:limit]


@router.post("/alarms/dismiss", response_model=DismissOut)
def dismiss_alarms(payload: AlarmDismissIn, session: Session = Depends(db_session_dependency)):
    """[KOS20260921] Alarms는 파생 데이터라 삭제할 원본 행이 없다(models.py의
    DismissedAlarm 설명 참고) - "삭제"는 실질적으로 "이 발생(occurred_at 기준)을
    다시 보지 않기"다. 같은 조건이 다시 발생(occurred_at 갱신)하면 다시 표시된다.
    """
    now = utcnow()
    count = 0
    for entry in payload.alarms:
        existing = session.get(DismissedAlarm, (entry.id, entry.occurred_at))
        if existing is None:
            session.add(DismissedAlarm(alarm_id=entry.id, occurred_at=entry.occurred_at, dismissed_at=now))
            count += 1
    session.commit()
    return DismissOut(dismissed_count=count)


@router.post("/alarms/dismiss-all", response_model=DismissOut)
def dismiss_all_alarms(
    severity: Optional[str] = None,
    category: Optional[str] = None,
    include_suppressed: bool = True,
    session: Session = Depends(db_session_dependency),
):
    """현재 필터 조건(severity/category/include_suppressed)에 맞는 알람을 전부 dismiss한다."""
    alarms = list_alarms(session)
    if severity:
        alarms = [a for a in alarms if a.severity == severity.upper()]
    if category:
        alarms = [a for a in alarms if a.category == category.upper()]
    if not include_suppressed:
        alarms = [a for a in alarms if not a.suppressed]
    alarms = _filter_dismissed(session, alarms)

    now = utcnow()
    for alarm in alarms:
        session.add(DismissedAlarm(alarm_id=alarm.id, occurred_at=alarm.occurred_at, dismissed_at=now))
    session.commit()
    return DismissOut(dismissed_count=len(alarms))
