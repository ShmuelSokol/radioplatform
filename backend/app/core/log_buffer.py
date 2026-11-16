"""Recent server activity kept in memory, so a bug report can carry it.

Railway keeps the stdout log, but nobody who triages a bug can read it from a
cloud session. So the server also keeps its last few thousand log lines and
requests in two ring buffers. When someone reports a bug, the report stores
the slice of both buffers from the minutes before it (see bug_reports.py).

Every request gets an id (X-Request-ID, sent by the browser too), and each log
line written while handling it carries that id. The browser keeps the ids of
its own requests, so a failed click on screen can be matched to the server's
lines for that same request.
"""

from __future__ import annotations

import contextvars
import logging
import time
from collections import deque
from datetime import datetime, timezone

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
user_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("log_user", default=None)

LOG_LINES = 3000
REQUESTS = 2000

_log_lines: deque = deque(maxlen=LOG_LINES)
_requests: deque = deque(maxlen=REQUESTS)


def _now_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc).isoformat(timespec="milliseconds")


class RingBufferHandler(logging.Handler):
    """Keeps formatted log records in memory. Never raises."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
            _log_lines.append(
                {
                    "t": record.created,
                    "at": _now_iso(record.created),
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": msg[:4000],
                    "rid": request_id_var.get(),
                }
            )
        except Exception:
            pass


_handler: RingBufferHandler | None = None


def install(level: int = logging.INFO) -> RingBufferHandler:
    """Attach the buffer to the app's loggers and uvicorn's error log. Idempotent."""
    global _handler
    if _handler is None:
        _handler = RingBufferHandler(level=level)
    for name in ("app", "uvicorn.error", "sqlalchemy.pool"):
        lg = logging.getLogger(name)
        if _handler not in lg.handlers:
            lg.addHandler(_handler)
    return _handler


def record_request(method: str, path: str, status: int, ms: float, rid: str | None, user: str | None = None) -> None:
    _requests.append(
        {
            "t": time.time(),
            "at": _now_iso(),
            "method": method,
            "path": path[:300],
            "status": status,
            "ms": round(ms, 1),
            "rid": rid,
            "user": user,
        }
    )


def snapshot(since_seconds: float = 900, max_lines: int = 1500, max_requests: int = 800) -> dict:
    """The buffers' entries from the last `since_seconds`, newest last."""
    cutoff = time.time() - since_seconds
    lines = [dict(x) for x in list(_log_lines) if x["t"] >= cutoff][-max_lines:]
    reqs = [dict(x) for x in list(_requests) if x["t"] >= cutoff][-max_requests:]
    for x in lines + reqs:
        x.pop("t", None)
    return {"taken_at": _now_iso(), "window_seconds": since_seconds, "log": lines, "requests": reqs}


def clear() -> None:
    """For tests."""
    _log_lines.clear()
    _requests.clear()
