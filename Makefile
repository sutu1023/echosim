SHELL := /bin/sh
.PHONY: setup check audit configure up down smoke
setup:
	uv sync --project backend --frozen
	npm ci --prefix frontend
check:
	uv run --project backend --frozen ruff check backend scripts
	uv run --project backend --frozen ruff format --check backend scripts
	uv run --project backend --frozen pytest -q backend/tests
	npm --prefix frontend run test
	npm --prefix frontend run build
	python3 scripts/check_repository.py
	openspec validate --all --strict

audit:
	uv run --project backend --frozen pip-audit --skip-editable
	npm audit --prefix frontend --audit-level=high
configure:
	python3 scripts/configure.py

up:
	docker compose up --build -d --wait

down:
	docker compose down
smoke:
	python3 scripts/private_integration.py
	python3 scripts/smoke.py
