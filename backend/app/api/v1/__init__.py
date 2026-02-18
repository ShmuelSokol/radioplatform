from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.stations import router as stations_router
from app.api.v1.assets import router as assets_router
from app.api.v1.streams import router as streams_router
from app.api.v1.controls import router as controls_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(stations_router)
router.include_router(assets_router)
router.include_router(streams_router)
router.include_router(controls_router)
