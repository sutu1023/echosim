SHELL := /bin/bash
COMPOSE = docker compose --env-file infra/images.env --env-file .env
.PHONY: init up down dev status logs test check docs-check secrets security licenses prepare live-smoke
init:
	python3 scripts/init.py
prepare:
	python3 scripts/run.py prepare
up:
	$(COMPOSE) up --build -d --wait
dev:
	$(COMPOSE) -f compose.yaml -f compose.dev.yaml up --build -d --wait
down:
	$(COMPOSE) down
status:
	python3 scripts/run.py status
logs:
	$(COMPOSE) exec -T api sh -c 'tail -n 100 /app/logs/$$(date -u +%F).jsonl'
test:
	python3 scripts/run.py test
docs-check:
	python3 scripts/docs_check.py
secrets:
	python3 scripts/security.py secrets
security:
	python3 scripts/security.py vulnerabilities
licenses:
	python3 scripts/licenses.py
check:
	python3 scripts/run.py check
live-smoke:
	@printf '%s\n' 'M0 未实现真实模型调用；本项未执行，M3 才启用。'
	@exit 2
