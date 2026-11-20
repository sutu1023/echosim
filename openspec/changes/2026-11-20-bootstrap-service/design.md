# Design

## Context

仓库当前只有 `README.md` 与 `.gitignore`，没有任何应用代码；`openspec/` 已初始化，但 `openspec/specs/` 为空。约束来自 proposal 与三份 delta spec：Vue 3 + TypeScript + Vite、Python 3.11 + FastAPI、PostgreSQL、Docker Compose、`127.0.0.1:8080` 同域、`/health` 真实执行 `SELECT 1`、`make up/down/verify`、`.env.example`、GitHub Actions 的 `verify` 检查、工程规则与 Pi 验收技能。`.gitignore` 已预留 `node_modules/`、`.venv/`、`dist/`、`runtime/`、`test-results/`、`.env`。

目标环境是开发者的 macOS/Linux 主机（Docker Engine + Compose v2 + GNU Make + `python3`）与 `ubuntu-latest` GitHub Runner。没有已有实现需要兼容，因此设计重点是减少活动部件、让故障可观察、让证据可追溯。

## Goals / Non-Goals

**Goals:**

- 单一构建产物：一个应用镜像同时提供构建后的前端与 API，同域、无跨域配置。
- 健康检查语义清晰：真实数据库探测，失败快速返回 503，恢复自动重新就绪，进程不重启。
- 验收可重复：`make verify` 幂等、坏工作树拒绝执行、报告绑定 SHA。
- 证据链一致：干净克隆、CI、人工验收通过同一个 SHA 对齐。
- 规则与验收知识沉淀：根 `AGENTS.md` + Pi 验收技能 + `docs/acceptance/` 模板。

**Non-Goals:**

- 不实现业务领域模型、鉴权、上传、回放或推演引擎。
- 不引入 nginx、Kubernetes、Terraform、多环境（dev/staging/prod）编排。
- 不做前端开发热更新工作流（本次验收只覆盖 compose 构建产物）。
- 不建立独立测试数据库实例；compose 中的 PostgreSQL 即验收数据库。
- 不实现生产级可观测性（指标、追踪、日志聚合）。

## Decisions

### 1. 用单个 FastAPI 容器同域提供前端与 `/health`

多阶段 `Dockerfile`：`node:22-bookworm-slim` 阶段执行 `npm ci && npm run build` 产出 `web/dist`，`python:3.11-slim-bookworm` 阶段安装后端依赖并复制 `dist`。Uvicorn 监听容器内 `0.0.0.0:8080`，Compose 只发布 `127.0.0.1:${ECHOSIM_PORT:-8080}:8080`。

静态资源由 FastAPI 提供：先注册 `/health`、`/livez` 等 API 路由，再把 `/assets` 挂到 `StaticFiles`，最后用 catch-all 路由返回存在的静态文件、否则回退 `index.html`（解析后的路径必须位于 `dist` 内）。这样 `/` 与 `/health` 天然同源，前端 `fetch('/health')` 不需要 CORS。

备选：nginx 反向代理 + 独立后端容器。更接近生产，但多一个容器、多一份配置和健康检查，且本阶段唯一收益是静态文件性能；备选：Vite 开发服务器代理到后端，但 `make up` 验收针对构建产物，dev server 不是启动路径。选择单容器以压缩故障面。

### 2. 分离存活信号与就绪信号

- `GET /livez`：不访问数据库，进程可响应即 200，容器 `HEALTHCHECK` 使用它。
- `GET /health`：每次请求执行真实 `SELECT 1`，数据库可用 200、不可用 503，供 Compose 等待后的显式验收与将来的编排就绪探针使用。

原因：若容器健康检查直接用 `/health`，验收中“停库”会让应用容器进入 unhealthy，产生误导性状态，也让后续 `compose up --wait` 行为依赖数据库；用 `/livez` 保证“数据库故障 ≠ 进程死亡”，同时 `/health` 不被掩盖。`make verify` 仍然显式轮询 `/health`，`make up` 的成功不替代就绪验证。每次 `/health` 请求确定一个请求 ID（复用合法 `X-Request-ID` 或生成随机值），通过响应头回显并写入探测失败日志，响应体契约保持不变。

### 3. SQLAlchemy async + asyncpg，短超时 + 连接池自愈

后端用 SQLAlchemy 2.x async engine + asyncpg，`pool_pre_ping=True`、`pool_timeout=2`，asyncpg `connect_args={"timeout": 2, "command_timeout": 2}`；`/health` 外层再用 `asyncio.timeout(4)` 兜底，确保最迟 5 秒内返回。数据库不可用时只记录异常类型与堆栈到服务端日志，响应固定为 `{"status":"degraded","database":"unavailable"}`，绝不回显 DSN、用户名、密码或主机名。

`pool_pre_ping` 处理“数据库重启后池中连接已失效”的情况；恢复后的第一个请求重建连接并返回 200，无需重启应用。备选：每次请求新建连接（简单但慢，且无法验证池的自愈能力）；备选：`psycopg` 同步驱动（需要线程池，行为不如 async 直观）。选 asyncpg 同时为后续真实业务查询打底。

健康响应固定契约：

| 场景 | HTTP | JSON |
| --- | --- | --- |
| 数据库可用 | 200 | `{"status":"ok","database":"ok"}` |
| 数据库不可用/超时 | 503 | `{"status":"degraded","database":"unavailable"}` |

### 4. Compose 编排与固定入口

`docker-compose.yml` 包含 `app` 与 `db` 两个服务：

- `db`：`postgres:16-alpine`，命名卷 `echosim-pgdata`，`pg_isready` 健康检查，`POSTGRES_*` 全部来自环境变量并带非敏感默认值。
- `app`：由根 `Dockerfile` 构建，`depends_on: db: condition: service_healthy`，`/livez` 健康检查，端口仅绑定 `127.0.0.1`。
- `make up`：确保 `.env` 存在（缺失时从 `.env.example` 复制）→ `docker compose up -d --build --wait --wait-timeout 240`。
- `make down`：`docker compose down --remove-orphans`，保留数据卷，避免把“停止”变成破坏性操作；破坏性清理放到 README 记录的 `make reset`（`down -v`）。
- `make verify`：只调用 `python3 scripts/verify.py`，由脚本负责幂等编排，避免 Makefile 与 CI 逻辑分叉。

备选：`make down` 直接 `down -v`。被否决，因为会隐式删除数据，且与“可重复停止/启动”无关；备选：匿名卷。被否决，重建后难以定位与清理。

### 5. 验收脚本用宿主机 Python 标准库

`scripts/verify.py` 只依赖 Python 标准库（`subprocess`、`urllib`、`json`、`time`、`argparse`），不引入 pytest/requests，使干净克隆无需额外安装即可验收；Docker 交互全部通过 `docker compose` CLI，HTTP 通过 `urllib`。

执行顺序（单次运行）：

1. 前置检查：`docker compose version` 可用、`python3 --version` 满足、读取 `git rev-parse HEAD`、用 `git status --porcelain` 判定脏工作树（默认非零退出，`ALLOW_DIRTY=1` 才继续）。
2. `compose up -d --build --wait`，并轮询首页 200 且正文包含 `EchoSim`。
3. 请求 `/health`，断言 200 与 `status=ok`、`database=ok`，记录耗时。
4. 记录 `app` 容器身份：容器 ID、`State.Pid`、`State.StartedAt`、`RestartCount`。
5. `docker compose stop db`，轮询 `/health` 直到 503，记录停库到 503 的耗时并断言 ≤ 5 秒；期间断言 `/livez` 仍为 200。
6. `docker compose start db`，轮询 `/health` 直到 200（上限 60 秒，覆盖 Postgres 恢复）。
7. 再次断言容器身份三项不变、首页仍为 200。
8. 原子写入 `runtime/verify/last-run.json` 与带时间戳的历史副本；失败时同样写报告，并在 `finally` 中尽力把 `db` 恢复到运行状态。

报告字段：`schema_version`、`result`（`passed`/`failed`）、`tested_sha`、`dirty`、`expected_sha`、`started_at`/`finished_at`（UTC ISO 8601）、`scenarios[]`（`id`、`status`、`duration_ms`、`detail`）、`app` 身份、`compose_project`。脚本对未预期异常也写入 `verify-crash` 失败报告，保证任何失败都有可下载产物。`EXPECTED_SHA` 环境变量存在时，`tested_sha` 必须与其相等，否则失败，这是 CI 与干净克隆对齐的关键。

备选：Bash + `curl` + `jq`（依赖 `jq`，JSON 断言别扭）；备选：pytest + testcontainers（需要额外依赖与 Docker socket 挂载）。选择标准库脚本以贴合“干净克隆即可验收”。

### 6. 脏工作树默认拒绝

验收结论声称“该 SHA 可复现”，未提交改动会让 SHA 无法代表被测代码。因此默认 `git status --porcelain` 非空即退出并提示提交或使用 `ALLOW_DIRTY=1`；允许时报告记录 `dirty: true`，README 明确其不能作为干净克隆证据。`.env` 与 `runtime/` 已被忽略，不会误触发。

备选：始终允许脏树并只记录标志。被否决，默认严格更符合验收目标；显式开关保留了本地调试的灵活性。

### 7. CI 校验“同一 SHA”而不是只跑命令

`.github/workflows/verify.yml`：`on: [push, pull_request, workflow_dispatch]`，`concurrency` 按 ref 取消旧运行，`timeout-minutes: 25`。步骤为 checkout → `EXPECTED_SHA=${{ github.sha }} make verify` → 读取 `runtime/verify/last-run.json` 断言 `tested_sha == github.sha && result == passed` → `actions/upload-artifact` 上传报告 → `if: always()` 执行 `make down`。

`actions/checkout` 对 PR 检出合并提交，`github.sha` 即该次检出的 HEAD，因此“触发 SHA”“检出 HEAD”“报告 SHA”三者可比。`workflow_dispatch` 额外提供期望 SHA 覆盖入口，用于确定性地演练失败路径；无论成功或失败都上传报告，产物缺失即判定配置错误。备选：比较 PR head SHA。被否决，因为实际构建的是合并结果，报告应描述真正被测的提交。

### 8. 工程规则与验收技能分层

- 根 `AGENTS.md`：单一工程规则来源（语言、技术栈与版本、命令、端口/同源/回环约束、健康语义、禁止提交凭据、验收与 SHA 规则、目录约定、提交与文档要求），README 链接它。
- `.pi/skills/echosim-acceptance/SKILL.md`：Pi 可调用的验收流程技能，指导 agent 依次执行 `make verify`、连续两次运行、浏览器人工检查、停库/恢复观察，并生成 `docs/acceptance/<date>-<sha7>.md`。
- `docs/acceptance/README.md` + `TEMPLATE.md`：人工记录的格式（SHA、日期、验收人、环境、三场景结果、CI 链接、证据），与 `runtime/verify/` 的 CI 产物物理分离；自动化流程不写该目录。

备选：把规则写进 README 单文件。被否决，README 面向人类快速上手，`AGENTS.md` 才是 agent 的规则入口；两者互相链接而非复制。

### 9. 版本与锁文件

Node 22（`.nvmrc`，`package.json` 的 `engines`）、Python 3.11、PostgreSQL 16-alpine。前端提交 `package-lock.json` 并用 `npm ci`；后端 `requirements.txt` 使用精确版本（`fastapi`、`uvicorn[standard]`、`sqlalchemy[asyncio]`、`asyncpg`、`pydantic-settings`）。镜像使用明确的基础标签（`python:3.11-slim-bookworm`、`node:22-bookworm-slim`、`postgres:16-alpine`），暂不做 digest 固定，以换取可读性与升级便利。

前端只有单页：标题 `EchoSim`、副标题“材料驱动的双平台推演工作台”、以及挂载时读取 `/health` 的状态徽标（成功/降级/不可达），用于肉眼确认同域链路。

## Risks / Trade-offs

- [Postgres 重启后恢复慢，`/health` 轮询超时] → 恢复轮询上限 60 秒，`db` 健康检查带 `start_period`，失败报告记录实际耗时。
- [8080 被占用导致 `make up` 失败] → Compose 明确绑定 `127.0.0.1`，README 提供端口检查与 `ECHOSIM_PORT` 覆盖，失败信息指向占用排查。
- [旧数据卷与 `.env` 中新凭据不一致，Postgres 不会重设密码] → README 提供 `make reset`（`down -v`）说明；默认配置稳定不变，验收不自动删卷以免误删数据。
- [Docker 构建需要网络拉取 npm/pip 依赖] → 锁文件 + 精确版本降低漂移；CI 每次干净构建，耗时换可信度。
- [容器 `HEALTHCHECK` 用 `/livez` 会掩盖数据库故障] → 有意分离存活/就绪；`make verify` 与 CI 显式断言 `/health` 的 200/503/200，`make up` 的等待不被当作就绪证明。
- [健康检查 5 秒上限在慢机器上抖动] → 连接与命令超时 2 秒、外层 4 秒兜底，验收阈值按规格断言。
- [脏工作树检查误伤生成文件] → `runtime/`、`.env`、`node_modules/`、`dist/` 已在 `.gitignore`，报告与本地配置不会触发。
- [PR 合并提交 SHA 与分支头 SHA 不同造成误解] → 验收技能与 README 明确“被测 SHA = 检出 HEAD”，人工记录同时保存分支名与 CI 链接。
- [单容器同时托管前端与 API 不适合未来横向拆分] → 现阶段优先可复现；静态资源与 API 的边界留在 FastAPI 路由顺序中，未来可平滑拆出 nginx。

## Migration Plan

绿地变更，无数据迁移。实施以一个提交（或少量原子提交）落地，部署方式即 `make up`。回滚为 revert 该提交并执行 `make down`；如已创建本地数据卷，按 README 使用 `make reset` 清理。发布后不涉及生产流量切换。

## Open Questions

无。未来“拆分为 nginx + 独立 API”“前端本地热更新流程”“生产环境编排”均不影响本次规格与任务拆分，留待后续变更。
