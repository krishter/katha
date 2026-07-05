.PHONY: dev test lint migrate

dev:
	docker-compose up

test:
	cd backend && .venv/bin/pytest tests/ -v

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd frontend && npm run lint

migrate:
	cd backend && .venv/bin/alembic upgrade head
