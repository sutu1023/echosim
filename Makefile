SHELL := /bin/bash
COMPOSE := docker compose

PYTHON ?= python3

.PHONY: up down reset verify migrate user smoke-live help

help:
	@echo "make up      生成 .env（如缺失）并构建、启动全部服务"
	@echo "make down    停止并移除容器与网络（保留数据卷）"
	@echo "make reset   停止并移除容器、网络与数据卷（破坏性）"
	@echo "make verify  执行可重复验收并写入 runtime/verify/last-run.json"
	@echo "make verify CHANGE=<id>  追加该变更的专属验收场景"
	@echo "make smoke-live 由开发者显式执行真实模型/图谱/双平台联通验收"
	@echo "make migrate 在运行中的 app 容器内应用数据库迁移"
	@echo "make user EMAIL=a@example.com  从标准输入读取密码并创建/更新账户"

# 缺少 .env 时从样例生成，保证干净克隆可直接 make up。
.env:
	@cp .env.example .env
	@echo "已从 .env.example 生成 .env"

up: .env
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
