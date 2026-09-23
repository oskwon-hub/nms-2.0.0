"""16장: Port/PoE Enable/Disable 제어 API (13장)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.control import interface_config_control, poe_control, port_control
from app.control.port_control import PortNotFoundError, ProtectedPortError
from app.db import db_session_dependency
from app.schemas import ControlActionIn, ControlLogOut, DescriptionSetIn, VlanSetIn

router = APIRouter(tags=["control"])


@router.post("/devices/{device_id}/interfaces/{if_id}/enable", response_model=ControlLogOut)
async def enable_port(
    device_id: int, if_id: int, payload: ControlActionIn = ControlActionIn(), session: Session = Depends(db_session_dependency)
):
    try:
        return await port_control.set_port_status(session, device_id, if_id, True, payload.performed_by, payload.force)
    except PortNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProtectedPortError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/devices/{device_id}/interfaces/{if_id}/disable", response_model=ControlLogOut)
async def disable_port(
    device_id: int, if_id: int, payload: ControlActionIn = ControlActionIn(), session: Session = Depends(db_session_dependency)
):
    try:
        return await port_control.set_port_status(session, device_id, if_id, False, payload.performed_by, payload.force)
    except PortNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProtectedPortError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/devices/{device_id}/interfaces/{if_id}/poe/enable", response_model=ControlLogOut)
async def enable_poe(
    device_id: int, if_id: int, payload: ControlActionIn = ControlActionIn(), session: Session = Depends(db_session_dependency)
):
    try:
        return await poe_control.set_poe_status(session, device_id, if_id, True, payload.performed_by, payload.force)
    except PortNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProtectedPortError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/devices/{device_id}/interfaces/{if_id}/poe/disable", response_model=ControlLogOut)
async def disable_poe(
    device_id: int, if_id: int, payload: ControlActionIn = ControlActionIn(), session: Session = Depends(db_session_dependency)
):
    try:
        return await poe_control.set_poe_status(session, device_id, if_id, False, payload.performed_by, payload.force)
    except PortNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProtectedPortError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/devices/{device_id}/interfaces/{if_id}/vlan", response_model=ControlLogOut)
async def set_vlan(device_id: int, if_id: int, payload: VlanSetIn, session: Session = Depends(db_session_dependency)):
    try:
        return await interface_config_control.set_port_vlan(
            session, device_id, if_id, payload.vlan, payload.performed_by, payload.force
        )
    except PortNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProtectedPortError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/devices/{device_id}/interfaces/{if_id}/description", response_model=ControlLogOut)
async def set_description(
    device_id: int, if_id: int, payload: DescriptionSetIn, session: Session = Depends(db_session_dependency)
):
    try:
        return await interface_config_control.set_interface_description(
            session, device_id, if_id, payload.description, payload.performed_by, payload.force
        )
    except PortNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProtectedPortError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
