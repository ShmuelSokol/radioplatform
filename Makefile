.PHONY: up down migrate seed test-be test-fe lint build logs

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

migrate:
	docker compose exec backend alembic upgrade head

seed:
	docker compose exec backend python -m scripts.seed

test-be:
	docker compose exec backend pytest tests/ -v --tb=short

test-fe:
	docker compose exec frontend npm run lint

lint:
	docker compose exec backend ruff check app/ tests/
	docker compose exec frontend npm run lint

shell-be:
	docker compose exec backend bash

shell-fe:
	docker compose exec frontend sh

restart-be:
	docker compose restart backend celery-worker celery-beat

ps:
	docker compose ps
