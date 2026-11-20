# Proposal

## Why

EchoSim 目前只有一个仓库初始化和一句产品描述，没有任何可启动的代码。在开始实现回放、上传与推演等产品能力之前，需要一条任何人从干净克隆出发都能复现的最短路径：一条命令拉起前端、后端与数据库，浏览器能看到 EchoSim，并且健康检查真的验证数据库可用性。没有这条基线，后续每个功能都会在“环境能跑吗、这次结果可信吗”上反复消耗时间。

本次只交付可启动、可复现的工程骨架，不实现回放、登录、上传或任何推演业务。

## What Changes

- 新增 Vue 3 + TypeScript + Vite 前端骨架，首页展示产品名 EchoSim 与“材料驱动的双平台推演工作台”，并展示同域 `/health` 的实时状态。
- 新增 Python 3.11 + FastAPI 后端骨架，同一进程同时提供构建后的前端静态资源与同域 `GET /health`；`/health` 对 PostgreSQL 真实执行 `SELECT 1`。
- 新增 Docker Compose 编排：应用容器发布到 `127.0.0.1:8080`，数据库使用带持久化卷的 PostgreSQL；数据库不可用时 `/health` 返回 503，恢复后返回 200，且后端进程不重启。
- 新增 `Makefile` 的 `up`、`down`、`verify` 三个入口，以及 `.env.example` 配置样例；`make up` 在缺少 `.env` 时从样例生成。
- 新增可重复执行的验收脚本：连续两次 `make verify` 均通过，并把本次实际测试的 Git SHA 写入机器可读报告；工作树不干净时默认拒绝执行。
- 新增 GitHub Actions 的 `verify` 检查：在干净检出上执行 `make verify`，校验报告中的 SHA 与触发的 `GITHUB_SHA` 一致，并上传报告作为证据。
- 新增项目工程规则（根目录 `AGENTS.md`）与 Pi 验收技能（`.pi/skills/echosim-acceptance/`），并把人工验收记录放在 `docs/acceptance/`，与 CI 结果严格分离。
- 重写 `README.md`，给出从干净克隆到浏览器看到 EchoSim 的完整步骤、验收方法与排障说明。

## Capabilities

### New Capabilities

- `runtime-bootstrap`：从干净克隆用一条命令启动 `127.0.0.1:8080` 上的 EchoSim（同域首页 + 健康检查），并能干净停止与重复启动。
- `health-readiness`：`/health` 用真实数据库查询反映就绪状态，覆盖“正常 200 → 停库 503 → 恢复 200”且后端进程不变的可观察行为。
- `reproducible-verification`：`make verify` 幂等可重复、绑定被测 SHA，并与 CI、人工验收形成可追溯的一致证据链。

### Modified Capabilities

无。仓库此前没有已归档的能力规范。

## Impact

- 新增代码与配置：`app/`（FastAPI）、`web/`（Vue + Vite）、`Dockerfile`、`docker-compose.yml`、`Makefile`、`.env.example`、`scripts/verify.py`、`.github/workflows/verify.yml`。
- 新增文档与规则：`README.md`、`AGENTS.md`、`docs/acceptance/`、`.pi/skills/echosim-acceptance/SKILL.md`。
- 依赖：Python 3.11（FastAPI、Uvicorn、SQLAlchemy、asyncpg、PostgreSQL 驱动）与 Node 22 构建期依赖，均锁定版本并提交锁文件。
- 运行时：本机需 Docker Engine 与 Compose v2、GNU Make、可用的 `python3`；占用 `127.0.0.1:8080` 与一个内部 PostgreSQL 端口。
- CI：新增一个 `verify` 工作流，单次运行需要构建镜像并执行停库/恢复场景，预计数分钟。
- 非目标：不实现业务功能、鉴权、上传、推演引擎、生产部署与多环境编排；不引入 nginx 反向代理或 Kubernetes。

## Assumptions

- 验收描述中的“测试数据库”指 compose 编排中的 PostgreSQL 服务，本次不额外提供独立的测试数据库实例。
- 默认端口固定为 `127.0.0.1:8080`，允许通过 `.env` 覆盖端口，但正式验收使用默认值。
- “干净克隆”指工作树无未提交改动；`make verify` 默认拒绝脏工作树，避免报告中的 SHA 无法代表被测代码。
