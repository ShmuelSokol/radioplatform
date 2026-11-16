"""The app's log lines reach the server log, INFO included.

Nothing configured logging, so INFO lines (track changes, timed starts, retries)
were dropped and the scheduler looked silent in the Railway log while it ran.
"""
import logging

import app.main  # noqa: F401  (configures logging on import)


def test_app_info_lines_are_written():
    app_logger = logging.getLogger("app")
    assert app_logger.handlers
    assert logging.getLogger("app.api.v1.queue").isEnabledFor(logging.INFO)
