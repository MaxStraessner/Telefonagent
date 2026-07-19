.PHONY: start stop logs migrate seed test test-backend test-frontend build

start:
	docker compose up --build

stop:
	docker compose down

logs:
	docker compose logs -f

migrate:
	docker compose run --rm backend alembic upgrade head

seed:
	docker compose run --rm backend python -m app.seed

test: test-backend test-frontend

test-backend:
	cd backend && pytest

test-frontend:
	cd frontend && npm test

build:
	cd frontend && npm run build

