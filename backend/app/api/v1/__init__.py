from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.stations import router as stations_router
from app.api.v1.assets import router as assets_router
from app.api.v1.streams import router as streams_router
from app.api.v1.controls import router as controls_router
from app.api.v1.users import router as users_router
from app.api.v1.queue import router as queue_router
from app.api.v1.rules import router as rules_router
from app.api.v1.schedules import router as new_schedules_router
from app.api.v1.now_playing import router as now_playing_router
from app.api.v1.websocket import router as websocket_router
from app.api.v1.scheduler import router as scheduler_router
from app.api.v1.holidays import router as holidays_router
from app.api.v1.sponsors import router as sponsors_router
from app.api.v1.channels import router as channels_router
from app.api.v1.icecast import router as icecast_router
from app.api.v1.analytics import router as analytics_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(stations_router)
router.include_router(assets_router)
router.include_router(streams_router)
router.include_router(controls_router)
router.include_router(users_router)
router.include_router(queue_router)
router.include_router(rules_router)
router.include_router(new_schedules_router)
router.include_router(now_playing_router)
router.include_router(websocket_router)
router.include_router(scheduler_router)
router.include_router(holidays_router)
router.include_router(sponsors_router)
router.include_router(channels_router)
router.include_router(icecast_router)
router.include_router(analytics_router)
