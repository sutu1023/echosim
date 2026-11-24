SHELL := /bin/bash
ENV_FILE ?= .env
COMPOSE := docker compose --env-file $(ENV_FILE)

PYTHON ?= python3

.PHONY: up down reset verify migrate user smoke-live help compose-fingerprint

help:
	@echo "make up      生成 $(ENV_FILE)（如缺失）并构建、启动全部服务"
	@echo "make down    停止并移除容器与网络（保留数据卷）"
	@echo "make reset   停止并移除容器、网络与数据卷（破坏性）"
	@echo "make verify  执行可重复验收并写入 runtime/verify/last-run.json"
	@echo "make verify CHANGE=<id>  追加该变更的专属验收场景"
	@echo "make smoke-live 由开发者显式执行真实模型/图谱/双平台联通验收"
	@echo "make migrate 在运行中的 app 容器内应用数据库迁移"
	@echo "make user EMAIL=a@example.com  从标准输入读取密码并创建/更新账户"
	@echo "make compose-fingerprint  显示 Compose 最终解析后的业务 worker 指纹"

# 缺少 .env 时从样例生成，保证干净克隆可直接 make up。
$(ENV_FILE):
	@cp .env.example $(ENV_FILE)
	@echo "已从 .env.example 生成 $(ENV_FILE)"

# 与 Compose 使用同一份 .env/shell 优先级；只向命令环境传递派生指纹。
compose-fingerprint: $(ENV_FILE)
	@$(PYTHON) scripts/host_config.py fingerprint --env-file $(ENV_FILE)

up: $(ENV_FILE)
	@set -e; \
	export_line="$$($(PYTHON) scripts/host_config.py export --env-file $(ENV_FILE))"; \
	eval "$$export_line"; \
	$(COMPOSE) up -d --build --wait --wait-timeout 240

down:
	$(COMPOSE) down --remove-orphans

reset:
	$(COMPOSE) down --remove-orphans -v

verify:
	$(PYTHON) scripts/verify.py $(if $(CHANGE),--change "$(CHANGE)",)

smoke-live:
	$(PYTHON) scripts/smoke_live.py

migrate:
	$(COMPOSE) exec -T app python -m app.migrate

user:
	@test -n "$(EMAIL)" || { echo "用法：make user EMAIL=a@example.com"; exit 1; }
	$(COMPOSE) exec -T app python -m app.manage create-user --email "$(EMAIL)" --password-stdin
