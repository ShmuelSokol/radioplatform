"""The Supabase pooler (transaction mode) refuses startup parameters it does not
know: "unsupported startup parameter: jit" stopped every connection of the
11:14Z deploy on 2026-11-18, and the tests, which use SQLite, could not see it.
So no code in the app may pass server_settings (or a jit/statement_timeout
option) when it connects."""
import re
from pathlib import Path

from app.db import engine as engine_mod

APP_DIR = Path(engine_mod.__file__).resolve().parents[1]
# server_settings=..., "server_settings": ..., options="-c ..."
STARTUP_PARAMETERS = re.compile(r"""server_settings["']?\s*[:=]|options\s*=\s*["']-c""")


def test_no_connection_sends_startup_parameters_a_pooler_refuses():
    offenders = [
        f"{path.relative_to(APP_DIR)}:{n}"
        for path in APP_DIR.rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        if STARTUP_PARAMETERS.search(line) and not line.lstrip().startswith("#")
    ]
    assert offenders == []


def test_the_check_would_have_caught_the_jit_setting():
    assert STARTUP_PARAMETERS.search('connect_args={"server_settings": {"jit": "off"}}')
    assert STARTUP_PARAMETERS.search("asyncpg.connect(dsn, server_settings={'jit': 'off'})")
    assert not STARTUP_PARAMETERS.search("# No server_settings at connect")
