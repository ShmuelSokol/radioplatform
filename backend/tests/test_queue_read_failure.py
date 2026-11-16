"""A failed queue read must not look like an empty queue: on KBR Jerusalem the
control room showed "Stopped", 0 items, while about 1,450 entries were queued."""
import pytest

import app.api.v1.queue as queue_module
from tests.test_queue_advance import _station_with_two_tracks


@pytest.mark.asyncio
async def test_failed_queue_read_is_an_error_not_an_empty_queue(client, auth_headers, db_session, monkeypatch):
    station, _, _ = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)

    async def broken(*args, **kwargs):
        raise RuntimeError("pool timeout")

    monkeypatch.setattr(queue_module, "_get_queue_impl", broken)
    res = await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)
    assert res.status_code == 503
    assert "entries" not in res.json()
