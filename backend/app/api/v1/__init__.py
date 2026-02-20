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
from app.api.v1.reviews import router as reviews_router
from app.api.v1.playlists import router as playlists_router
from app.api.v1.categories import router as categories_router
from app.api.v1.sponsor_portal import router as sponsor_portal_router
from app.api.v1.campaigns import router as campaigns_router
from app.api.v1.billing import router as billing_router
from app.api.v1.ai_emails import router as ai_emails_router
from app.api.v1.alerts import router as alerts_router
from app.api.v1.live_shows import router as live_shows_router
from app.api.v1.live_shows_ws import router as live_shows_ws_router
from app.api.v1.song_requests import router as song_requests_router
from app.api.v1.archives import router as archives_router
from app.api.v1.weather_readouts import router as weather_readouts_router
from app.api.v1.mixer import router as mixer_router
from app.api.v1.listeners import router as listeners_router

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
router.include_router(reviews_router)
router.include_router(playlists_router)
router.include_router(categories_router)
router.include_router(sponsor_portal_router)
router.include_router(campaigns_router)
router.include_router(billing_router)
router.include_router(ai_emails_router)
router.include_router(alerts_router)
router.include_router(live_shows_router)
router.include_router(live_shows_ws_router)
router.include_router(song_requests_router)
router.include_router(archives_router)
router.include_router(weather_readouts_router)
router.include_router(mixer_router)
router.include_router(listeners_router)
