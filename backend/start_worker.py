"""Start the Celery worker for media processing tasks."""
import sys

from app.config import settings

if not settings.redis_enabled:
    print("ERROR: REDIS_URL not set. Cannot start Celery worker without Redis.")
    sys.exit(1)

from app.workers.celery_app import celery_app

if __name__ == "__main__":
    celery_app.worker_main([
        "worker",
        "--loglevel=info",
        "--queues=media",
        "--concurrency=2",
        "--max-tasks-per-child=50",
    ])
