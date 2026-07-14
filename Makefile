SHELL := /bin/sh

ATLAS_OWNER_DATABASE_URL ?= postgresql://atlas_owner:atlas_owner@127.0.0.1:5432/atlas
ATLAS_TEST_DATABASE_URL ?= postgresql://atlas_app:atlas_app@127.0.0.1:5432/atlas
ATLAS_TEST_TEMPORAL_ADDRESS ?= 127.0.0.1:7233

.PHONY: infra-up infra-down migrate contracts backend-check frontend-check verify

infra-up:
	docker compose up -d --wait

infra-down:
	docker compose down

migrate:
	cd backend && ATLAS_DATABASE_URL='$(ATLAS_OWNER_DATABASE_URL)' uv run alembic upgrade head

contracts:
	cd backend && uv run python scripts/export_contracts.py
	cd backend && uv run python scripts/export_openapi.py
	cd frontend/atlas-ai-testops-prototype && npm run generate:api

backend-check:
	cd backend && uv run ruff check .
	cd backend && uv run mypy src tests
	cd backend && ATLAS_TEST_DATABASE_URL='$(ATLAS_TEST_DATABASE_URL)' ATLAS_TEST_TEMPORAL_ADDRESS='$(ATLAS_TEST_TEMPORAL_ADDRESS)' uv run pytest
	cd backend && uv run python scripts/export_contracts.py --check
	cd backend && uv run python scripts/export_openapi.py --check
	cd backend && uv build

frontend-check:
	cd frontend/atlas-ai-testops-prototype && npm run check:api
	cd frontend/atlas-ai-testops-prototype && npm run lint
	cd frontend/atlas-ai-testops-prototype && npm run build

verify: backend-check frontend-check
