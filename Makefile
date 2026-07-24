.PHONY: start stop logs migrate seed provision-tenant set-password test test-backend test-frontend build

start:
	docker compose up --build

stop:
	docker compose down

logs:
	docker compose logs -f

migrate:
	docker compose run --rm migrate

seed:
	docker compose run --rm backend python -m app.seed

provision-tenant:
	docker compose run --rm migrate python -m app.cli provision-tenant $(ARGS)

set-password:
	docker compose run --rm migrate python -m app.cli set-password $(ARGS)

test: test-backend test-frontend

test-backend:
	cd backend && pytest

test-frontend:
	cd frontend && npm test

build:
	cd frontend && npm run build

