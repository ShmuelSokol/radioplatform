import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import may_operate_station, require_manager, require_staff
from app.db.session import get_db
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.models.weather_readout import WeatherReadout
from app.schemas.weather_readout import (
    WeatherReadoutCreate,
    WeatherReadoutListResponse,
    WeatherReadoutResponse,
    WeatherReadoutUpdate,
)
from app.services.weather_readout_service import (
    DEFAULT_TEMPLATE,
    render_template,
)
from app.services.weather_service import WeatherNotConfiguredError, get_current_weather

router = APIRouter(prefix="/weather-readouts", tags=["weather-readouts"])


async def _weather_for(station: Station) -> dict:
    """The station's weather, or a clear 503 when it cannot be had (no key, API down):
    the page said only "Server error" before."""
    try:
        return await get_current_weather(
            lat=station.latitude or 40.0968,
            lon=station.longitude or -74.2179,
            timezone_name=station.timezone or "America/New_York",
        )
    except WeatherNotConfiguredError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(503, f"The weather service did not answer: {e}")


@router.get("/", response_model=WeatherReadoutListResponse)
async def list_readouts(
    station_id: uuid.UUID | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    status: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_staff),
):
    stmt = select(WeatherReadout)
    count_stmt = select(func.count(WeatherReadout.id))

    if station_id:
        stmt = stmt.where(WeatherReadout.station_id == station_id)
        count_stmt = count_stmt.where(WeatherReadout.station_id == station_id)
    if date_from:
        stmt = stmt.where(WeatherReadout.readout_date >= date_from)
        count_stmt = count_stmt.where(WeatherReadout.readout_date >= date_from)
    if date_to:
        stmt = stmt.where(WeatherReadout.readout_date <= date_to)
        count_stmt = count_stmt.where(WeatherReadout.readout_date <= date_to)
    if status:
        stmt = stmt.where(WeatherReadout.status == status)
        count_stmt = count_stmt.where(WeatherReadout.status == status)

    total = (await db.execute(count_stmt)).scalar() or 0
    result = await db.execute(
        stmt.order_by(WeatherReadout.readout_date.desc()).offset(skip).limit(limit)
    )
    readouts = result.scalars().all()

    return WeatherReadoutListResponse(
        readouts=[WeatherReadoutResponse.model_validate(r) for r in readouts],
        total=total,
    )


@router.post("/", response_model=WeatherReadoutResponse)
async def create_readout(
    body: WeatherReadoutCreate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_manager),
):
    station = await db.get(Station, uuid.UUID(str(body.station_id)))
    if not station:
        raise HTTPException(404, "Station not found")
    if not may_operate_station(_user, station):
        raise HTTPException(403, "Your login is not set up to operate this station")

    target_date = body.readout_date or date.today()

    # Check for existing
    existing = await db.execute(
        select(WeatherReadout).where(
            WeatherReadout.station_id == station.id,
            WeatherReadout.readout_date == target_date,
        ).limit(1)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Readout already exists for this station and date")

    # Fetch weather
    weather = await _weather_for(station)

    # Render
    config = (station.automation_config or {}).get("weather_readout", {})
    template = body.template_override or config.get("template", DEFAULT_TEMPLATE)
    city_name = config.get("city_name", "Lakewood")
    brand_name = config.get("brand_name", "Kohl Baramah")
    script_text = render_template(template, weather, city_name, brand_name, target_date)

    readout = WeatherReadout(
        station_id=station.id,
        readout_date=target_date,
        script_text=script_text,
        weather_data=weather,
        status="pending",
        generated_by="manual",
    )
    db.add(readout)
    await db.commit()
    await db.refresh(readout)

    return WeatherReadoutResponse.model_validate(readout)


@router.get("/template-preview")
async def template_preview(
    station_id: uuid.UUID = Query(...),
    template: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_staff),
):
    station = await db.get(Station, station_id)
    if not station:
        raise HTTPException(404, "Station not found")

    weather = await _weather_for(station)

    config = (station.automation_config or {}).get("weather_readout", {})
    tpl = template or config.get("template", DEFAULT_TEMPLATE)
    city_name = config.get("city_name", "Lakewood")
    brand_name = config.get("brand_name", "Kohl Baramah")

    rendered = render_template(tpl, weather, city_name, brand_name)
    return {"rendered": rendered, "weather": weather, "template": tpl}


@router.get("/{readout_id}", response_model=WeatherReadoutResponse)
async def get_readout(
    readout_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_staff),
):
    readout = await db.get(WeatherReadout, readout_id)
    if not readout:
        raise HTTPException(404, "Readout not found")
    return WeatherReadoutResponse.model_validate(readout)


@router.patch("/{readout_id}", response_model=WeatherReadoutResponse)
async def update_readout(
    readout_id: uuid.UUID,
    body: WeatherReadoutUpdate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_manager),
):
    readout = await db.get(WeatherReadout, readout_id)
    if not readout:
        raise HTTPException(404, "Readout not found")
    station = await db.get(Station, readout.station_id)
    if station is not None and not may_operate_station(_user, station):
        raise HTTPException(403, "Your login is not set up to operate this station")

    if body.script_text is not None:
        readout.script_text = body.script_text
    if body.status is not None:
        readout.status = body.status
    if body.asset_id is not None:
        readout.asset_id = uuid.UUID(str(body.asset_id))
    if body.queue_time is not None:
        readout.queue_time = body.queue_time

    await db.commit()
    await db.refresh(readout)
    return WeatherReadoutResponse.model_validate(readout)


@router.post("/{readout_id}/regenerate", response_model=WeatherReadoutResponse)
async def regenerate_readout(
    readout_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_manager),
):
    readout = await db.get(WeatherReadout, readout_id)
    if not readout:
        raise HTTPException(404, "Readout not found")

    station = await db.get(Station, readout.station_id)
    if not station:
        raise HTTPException(404, "Station not found")
    if not may_operate_station(_user, station):
        raise HTTPException(403, "Your login is not set up to operate this station")

    weather = await _weather_for(station)

    config = (station.automation_config or {}).get("weather_readout", {})
    template = config.get("template", DEFAULT_TEMPLATE)
    city_name = config.get("city_name", "Lakewood")
    brand_name = config.get("brand_name", "Kohl Baramah")

    readout.script_text = render_template(template, weather, city_name, brand_name, readout.readout_date)
    readout.weather_data = weather
    readout.status = "pending"

    await db.commit()
    await db.refresh(readout)
    return WeatherReadoutResponse.model_validate(readout)


@router.post("/{readout_id}/queue")
async def queue_readout(
    readout_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_manager),
):
    readout = await db.get(WeatherReadout, readout_id)
    if not readout:
        raise HTTPException(404, "Readout not found")
    # Queuing puts it on that station's air: same rule as operating the station.
    station = await db.get(Station, readout.station_id)
    if station is not None and not may_operate_station(_user, station):
        raise HTTPException(403, "Your login is not set up to operate this station")

    if not readout.asset_id:
        raise HTTPException(400, "Readout has no recorded asset — record it first")

    if readout.status == "queued":
        raise HTTPException(400, "Readout already queued")

    # Insert into queue at position 0 (top)
    entry = QueueEntry(
        station_id=readout.station_id,
        asset_id=readout.asset_id,
        position=0,
        status="pending",
    )
    db.add(entry)
    readout.status = "queued"
    await db.commit()

    return {"ok": True, "queue_entry_id": str(entry.id)}


@router.delete("/{readout_id}")
async def delete_readout(
    readout_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_manager),
):
    readout = await db.get(WeatherReadout, readout_id)
    if not readout:
        raise HTTPException(404, "Readout not found")
    station = await db.get(Station, readout.station_id)
    if station is not None and not may_operate_station(_user, station):
        raise HTTPException(403, "Your login is not set up to operate this station")

    await db.delete(readout)
    await db.commit()
    return {"ok": True}
