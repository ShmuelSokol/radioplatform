from app.config import settings


class _NoOpCelery:
    """Stub when Redis is not configured — tasks become regular sync functions."""

    def task(self, *args, **kwargs):
        def decorator(func):
            func.delay = lambda *a, **k: None
            func.apply_async = lambda *a, **k: None
            return func
        return decorator

    def autodiscover_tasks(self, *args, **kwargs):
        pass


if settings.redis_enabled:
    from celery import Celery

    celery_app = Celery(
        "radioplatform",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
    )

    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_routes={
            "app.workers.tasks.media_tasks.*": {"queue": "media"},
        },
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        worker_max_tasks_per_child=50,
    )

    celery_app.autodiscover_tasks(["app.workers.tasks"])
else:
    celery_app = _NoOpCelery()
