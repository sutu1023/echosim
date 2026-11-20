# AGENTS.md

本文件是 EchoSim 仓库的单一工程规则来源，面向所有贡献者与自动化代理。README 面向快速上手，本文件面向“必须始终遵守什么”。

## 语言与文档

- 代码注释、提交信息正文、文档与验收记录使用中文（zh-CN）；标识符与命令保持英文。
- OpenSpec 结构标题与 `SHALL`/`MUST` 关键字保持英文。
- 面向使用者的行为变化必须同步更新 `README.md`；工程规则变化必须同步更新本文件。

## 技术栈与版本

- 前端：Vue 3 + TypeScript + Vite，构建期 Node.js 22（见 `.nvmrc`、`web/package.json` 的 `engines`）。
- 后端：Python 3.11 + FastAPI + Uvicorn，SQLAlchemy 2.x async + asyncpg。
- 数据库：PostgreSQL 16（`postgres:16-alpine`）。
- 编排：Docker Engine + Docker Compose v2；命令入口统一走 `make`。
- 依赖：前端提交 `web/package-lock.json` 并用 `npm ci`；后端 `app/requirements*.txt` 使用精确版本。

## 目录约定

| 路径 | 用途 |
| --- | --- |
| `app/` | FastAPI 后端：配置、数据库、健康检查、静态托管 |
| `web/` | Vue 前端源码与构建配置；`web/dist` 为构建产物（不提交） |
| `tests/` | Python 单元测试（`unittest`） |
| `scripts/verify.py` | 可重复验收脚本，仅用 Python 标准库 |
| `docs/acceptance/` | 人工验收记录与模板（自动化流程禁止写入） |
| `.github/workflows/` | CI 工作流 |
| `runtime/` | 本地运行产物（报告、日志），已忽略不提交 |
| `openspec/` | OpenSpec 变更与规范 |

## 命令入口

```bash
make up       # 缺 .env 时从 .env.example 生成，然后构建并启动，等待健康
make down     # 停止并移除容器与网络，保留数据卷
make reset    # 破坏性：连同数据卷一起移除
make verify   # 执行可重复验收，写入 runtime/verify/last-run.json
```

- 不要绕开 `make up`/`make down` 手工拼装 `docker compose` 命令作为交付路径。
- 单元测试：`.venv/bin/python -m unittest discover -s tests`（Python 3.11 虚拟环境）。

## 同域、回环与健康语义

- 首页与 `/health` 必须同源（同一协议、主机、端口），前端请求一律使用相对路径，禁止硬编码 `localhost`、后端端口或绝对 URL。
- 服务只发布到回环地址 `127.0.0.1:${ECHOSIM_PORT:-8080}`，不得默认暴露到局域网或公网。
- `GET /livez` 是存活信号：不访问数据库，进程可响应即 200，容器健康检查使用它。
- `GET /health` 是就绪信号：每次请求都**真实执行 `SELECT 1`**（不得缓存、不得固定返回），数据库可用返回 200 `{"status":"ok","database":"ok"}`，不可用或超时在 5 秒内返回 503 `{"status":"degraded","database":"unavailable"}`。
- 数据库故障不得导致后端进程重启；恢复后同一进程的下一次 `/health` 必须重新返回 200。
- 健康检查不得回显 DSN、凭据、堆栈或内部主机名；日志只记录异常类型。
- 探测路径只读：除 `SELECT 1` 外不得执行任何写操作或修改状态。

## 凭据与配置

- 禁止提交任何真实凭据、密钥或令牌；`.env`、`.env.*`（`.env.example` 除外）已被忽略。
- `.env.example` 只能包含明显的开发占位值，并注明用途。
- 新增配置项必须同时更新 `.env.example`、`docker-compose.yml` 默认值与 README 配置表。

## 验收与 SHA 规则

- 验收结论必须绑定被测 Git SHA；`runtime/verify/last-run.json` 的 `tested_sha` 必须等于执行时的 `HEAD`。
- 工作树存在未忽略改动时 `make verify` 默认拒绝执行；仅在本地调试时可用 `ALLOW_DIRTY=1`，此时报告记录 `dirty: true`，**不得**作为干净克隆证据。
- CI 必须调用与本地相同的 `make verify`，并断言报告 `tested_sha` 等于触发 SHA；不得在 CI 中重复实现验收逻辑。
- 人工验收记录只写 `docs/acceptance/`，`make verify` 与 CI 不得创建、修改或删除该目录内容。
- 干净克隆、CI 产物与人工记录必须能通过同一个 SHA 对齐；报告与运行链接写入记录的“证据”字段。

## 提交与评审

- 使用 Conventional Commits：`feat:`、`fix:`、`ci:`、`docs:`、`chore:`、`test:`、`refactor:`。
- 每次提交保持单一意图；验收前先提交，避免脏工作树导致结论不可追溯。
- 修改健康检查语义、端口绑定、验收脚本或 CI 断言时，必须同时更新对应的 OpenSpec 规范与本文件相关条目。
- 不得删除或弱化既有测试来让流水线通过；失败时修复实现或明确记录阻塞项。
