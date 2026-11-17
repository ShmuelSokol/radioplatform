import logging
import time
import uuid as uuid_mod

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.core import log_buffer

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if settings.APP_ENV == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Generate a unique request ID for every request, include in response and logs."""

    async def dispatch(self, request: Request, call_next):
        request_id = (request.headers.get("X-Request-ID") or str(uuid_mod.uuid4())[:8])[:40]
        request.state.request_id = request_id
        token = log_buffer.request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            ms = (time.perf_counter() - started) * 1000
            path = request.url.path
            if path != "/health" and request.method != "OPTIONS":
                # Kept in memory for bug reports (see core/log_buffer.py);
                # only failures and slow calls also go to the server log.
                log_buffer.record_request(request.method, path, status, ms, request_id)
                if status >= 500 or ms > 3000:
                    logger.warning("%s %s -> %s in %.0f ms [rid %s]", request.method, path, status, ms, request_id)
            log_buffer.request_id_var.reset(token)


def setup_middleware(app: FastAPI) -> None:
    # GZip compression for responses > 500 bytes
    # Level 5, not the default 9: nearly the same size for JSON at a fraction of the
    # CPU (the control room fetches a days-long queue every few seconds).
    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=5)

    # Security headers
    app.add_middleware(SecurityHeadersMiddleware)

    # Who did what (wiki/activity-log.md); inside RequestID so rows carry the request id
    from app.services.activity_log import ActivityMiddleware
    app.add_middleware(ActivityMiddleware)

    # Request ID correlation
    app.add_middleware(RequestIDMiddleware)

    # CORS — locked down for production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    )

    # Rate limiting on public endpoints
    _setup_rate_limiting(app)


def _setup_rate_limiting(app: FastAPI) -> None:
    """Configure rate limiting using slowapi."""
    try:
        from slowapi import Limiter, _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
        from slowapi.util import get_remote_address

        limiter = Limiter(
            key_func=get_remote_address,
            default_limits=["200/minute"],
            storage_uri="memory://",
        )
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        logger.info("Rate limiting enabled (200/min default)")
    except Exception as e:
        logger.warning("Rate limiting setup failed: %s", e)
