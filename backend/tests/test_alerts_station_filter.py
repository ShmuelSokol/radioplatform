"""F-MODES-34: the control room's Errors tab asks for one station's open alerts (plus the
ones tied to no station), so a busy station cannot push another station's errors off it."""
import uuid

import pytest

from app.models.alert import Alert, AlertSeverity, AlertType
from app.models.station import Station


@pytest.mark.asyncio
async def test_errors_tab_reads_only_its_station(client, db_session, auth_headers):
    busy = Station(id=uuid.uuid4(), name="Busy", timezone="UTC")
    quiet = Station(id=uuid.uuid4(), name="Quiet", timezone="UTC")
    db_session.add_all([busy, quiet])
    await db_session.flush()

    def alert(station, title):
        return Alert(id=uuid.uuid4(), station_id=station.id if station else None, title=title, message=title,
                     alert_type=AlertType.PLAYBACK_GAP, severity=AlertSeverity.WARNING)

    db_session.add_all([alert(busy, f"busy {i}") for i in range(120)])
    db_session.add_all([alert(quiet, "quiet one"), alert(None, "site wide")])
    await db_session.commit()

    r = await client.get("/api/v1/alerts", params={"is_resolved": False, "limit": 100, "station_id": str(quiet.id)},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    titles = sorted(a["title"] for a in r.json()["alerts"])
    assert titles == ["quiet one", "site wide"]
    everything = await client.get("/api/v1/alerts", params={"limit": 100}, headers=auth_headers)
    assert everything.json()["total"] == 122
