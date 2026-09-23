from fastapi import APIRouter

from app.api import (
    routes_alarms,
    routes_config,
    routes_control,
    routes_devices,
    routes_discovery,
    routes_reports,
    routes_settings,
    routes_topology,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(routes_devices.router)
api_router.include_router(routes_topology.router)
api_router.include_router(routes_discovery.router)
api_router.include_router(routes_control.router)
api_router.include_router(routes_alarms.router)
api_router.include_router(routes_reports.router)
api_router.include_router(routes_settings.router)
api_router.include_router(routes_config.router)
