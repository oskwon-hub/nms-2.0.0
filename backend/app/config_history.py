"""구성 변경 이력(ConfigChangeLog) 기록/조회.

호출부(Discovery 재탐색 루프, Role 자동/수동 변경 등)는 필드 값이 실제로
달라졌는지 스스로 판단할 필요 없이 이 모듈의 record_config_change()에 이전값/
새값을 그대로 넘기면 된다 - 같으면 조용히 아무 것도 하지 않는다.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import ConfigChangeLog


def record_config_change(
    session: Session,
    *,
    device_id: int,
    field_name: str,
    old_value: Optional[str],
    new_value: Optional[str],
    source: str,
    interface_id: Optional[int] = None,
    performed_by: Optional[str] = None,
) -> Optional[ConfigChangeLog]:
    """old_value != new_value일 때만 이력 행을 추가한다(커밋은 호출부가 한다).

    값 자체는 표시용 문자열로만 쓰이므로 호출부가 bool/int 등을 str()로 넘겨도
    되고, 둘 다 None이면(값이 계속 비어 있던 경우) 변경이 아니므로 기록하지 않는다.
    """
    if old_value == new_value:
        return None
    log = ConfigChangeLog(
        device_id=device_id,
        interface_id=interface_id,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        source=source,
        performed_by=performed_by,
    )
    session.add(log)
    return log
