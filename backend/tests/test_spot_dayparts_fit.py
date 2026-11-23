"""Every daypart ticked at once fits the column (a 60-character column refused six
three-hour slots with a server error on Save, admin flows on Postgres 2026-11-23)."""
from app.models.spot_schedule import SpotSchedule
from app.schemas.spot_schedule import _clean_dayparts
from app.services.spot_schedule_service import DAYPARTS


def test_all_dayparts_fit_the_column():
    everything = _clean_dayparts(",".join(DAYPARTS))
    assert len(everything) <= SpotSchedule.__table__.c.dayparts.type.length


def test_startup_widens_the_column_on_existing_databases():
    import inspect

    import app.main as main

    src = inspect.getsource(main)
    assert "ALTER TABLE spot_schedules ALTER COLUMN dayparts TYPE VARCHAR(255)" in src
