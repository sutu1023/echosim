SHELL := /bin/bash
COMPOSE := docker compose

PYTHON ?= python3

.PHONY: up down reset verify help

help:
	@echo "make up      生成 .env（如缺失）并构建、启动全部服务"
	@echo "make down    停止并移除容器与网络（保留数据卷）"
	@echo "make reset   停止并移除容器、网络与数据卷（破坏性）"
	@echo "make verify  执行可重复验收并写入 runtime/verify/last-run.json"

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
	$(PYTHON) scripts/verify.py
