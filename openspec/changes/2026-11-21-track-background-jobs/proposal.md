# Proposal

## Why

Change 03 交付的工作台外壳没有任何执行能力：所有请求都在 HTTP 请求内同步完成，刷新页面就丢失全部上下文。而后续的材料入库、双平台推演与报告生成都是分钟级长任务，无法放进一次请求，也不能依赖单个进程的内存状态。本变更先建立“PostgreSQL 持久队列 + 独立 worker + 先持久化再推送的事件流”这套底座，并用一个**明确标识为测试用途**的确定性任务把底座跑通：任务在 worker 被杀、客户端断线、浏览器刷新后都必须能凭数据库中的事实恢复，而不是靠内存重放。

## What Changes

- 新增 `0002` 前向迁移：`jobs`（队列与控制记录）、`runs`（执行记录，与 job 1:1，持久化 `fail_at_step` 等创建参数）、`run_events`（业务事件，主键 `(run_id, seq)`）、`run_steps`（每步提交的测试副作用，主键 `(run_id, step_index)`）。
- 新增独立 Compose `worker` 服务（同镜像、无对外端口、`python -m app.worker`），只从数据库领取任务，`app` 不再承担长任务执行。
- 新增项目内任务接口：开始（HTTP 202 + `jobId`/`runId`）、列表、状态查询、取消、事件订阅（同域 SSE）、事件历史补发（JSON）、测试副作用计数。全部复用 `resolve_owned_project` 完成项目归属校验。
- 定义任务状态机 `queued → running → succeeded | failed | canceled`，并覆盖“重复开始只产生一个有效任务与一个 run”“取消与完成同时发生时以先提交的终态为准”“租约过期后由其他 worker 凭新租约令牌原子接手，旧令牌续租与写入被数据库拒绝”。
- 定义统一事件字段 `runId`、`seq`、`type`、`stage`、`simulatedAt`、`payload`；`seq` 在同一 run 内从 1 递增；事件先落库再推送，SSE 以 `id: <runId>:<seq>` 作为重连游标，支持 `Last-Event-ID` 与 `after` 两种游标来源。
- 在工作台加入“测试任务”面板（开始 / 取消 / 状态 / 日志 / 副作用计数），明确标注为连通性自检而非推演结果；区分 SSE 连接中断、worker 中断、任务失败、任务取消四种提示与恢复方式，且不改变当前项目、阶段、布局与语言。
- `make verify` 增加 `CHANGE` 选择器：默认仍跑 Change 01–03 全部回归并新增任务底座基础场景；`make verify CHANGE=2026-11-21-track-background-jobs` 额外跑真实断线续传、worker 中断恢复、并发重复开始、受控失败与取消，并在报告记录 `jobId`、`runId` 与实际序号。
- 更新 `README.md`（接口表、状态转换表、SSE 契约、人工走查步骤）与 `AGENTS.md`（worker 边界、事件契约、验收规则）。
- **Non-goals**：材料入库、证据图谱计算、角色与环境配置、真实双平台推演、报告生成、案例回放、任务优先级/定时调度/多队列、跨进程日志聚合。测试任务不产生任何推演结论，也不改变阶段进入条件。

## Capabilities

### New Capabilities

- `background-job-queue`: 以 PostgreSQL 为持久队列的测试任务生命周期——开始、查询、取消、状态转换、租约与接手、重复开始保护、可查询可计数的测试副作用，以及 worker 与 app 的故障隔离。
- `run-event-stream`: 先持久化再推送的 run 事件模型、`seq` 语义与唯一性、SSE 帧与 `id`、重连游标解析、历史补发与实时推送的无缝衔接、终态后的流行为，以及前端按 `runId`+`seq` 去重的统一日志处理。

### Modified Capabilities

- `project-workbench`: 工作台新增“测试任务”面板与四种可区分状态提示（SSE 中断、worker 中断、任务失败、任务取消），并声明开始/取消/订阅不得丢失当前项目、阶段、布局与语言。
- `project-isolation`: 任务与事件流接口统一复用 `resolve_owned_project`，SSE 在发送任何事件字节前完成鉴权与归属校验；他人项目、不存在项目与非法 ID 的 404 等价性扩展到 jobId 与事件订阅。
- `health-readiness`: 明确 `worker` 单独停止或异常不影响 `GET /health` 与 `GET /livez` 的既有语义，也不触发 `app` 进程重启。
- `reproducible-verification`: `make verify` 支持 `CHANGE=<id>` 选择变更专属场景，新增任务底座与断线续传/中断恢复/失败/取消断言，并在报告中记录 `change`、`jobId`、`runId` 与实际事件序号；保留 Change 01–03 全部回归。

## Impact

- 数据：新增 `app/migrations/0002_jobs_runs_events.sql`（含活动任务部分唯一索引与 `(run_id, seq)`、`(run_id, step_index)` 主键）；不修改既有表，可在已有数据卷上重复启动。
- 后端：新增 `app/services/jobs.py`、`app/services/run_events.py`、`app/services/worker.py`、`app/routers/jobs.py`、`app/worker.py`；`app/main.py` 注册路由；`app/errors.py` 增加任务不存在/游标无效映射；`app/config.py` 新增租约、心跳、轮询与测试步进配置。
- 编排：`docker-compose.yml` 新增 `worker` 服务（无端口、`depends_on: db healthy`、`restart: unless-stopped`）；`Makefile` 的 `verify` 透传 `CHANGE`。
- 前端：新增 `web/src/workbench/job-log.ts`（纯去重/排序 reducer）、`web/src/workbench/job-stream.ts`（SSE 客户端）、`web/src/views/workbench/JobPanel.vue`、`web/src/i18n.ts` 文案；`WorkbenchView.vue` 挂载面板并保持布局/语言/阶段。
- 验收：`scripts/verify.py` 增加 `--change` 与任务场景、SSE 断线重连、worker 停止/恢复、并发重复开始、受控失败与取消；`web/tests/job-log.test.mjs` 新增 reducer 回归（隔离数据），保留 `stage-links` 回归。
- 配置：`.env.example`、`docker-compose.yml`、`README.md` 配置表与 `app/config.py` 同步新增 `JOB_LEASE_SECONDS`、`JOB_HEARTBEAT_SECONDS`、`WORKER_POLL_INTERVAL_SECONDS`、`TEST_TASK_STEP_DELAY_MS`。
- 文档：`README.md` 增补接口、状态转换、SSE 与人工走查；`AGENTS.md` 增补 worker/事件/验收规则；`docs/acceptance/` 仅在人工签收后写入，本规约阶段不创建记录。
- 兼容性：不改变既有 `/health`、`/livez`、`/api/auth/*`、`/api/projects*`、`/api/history` 的响应契约；`make verify` 无 `CHANGE` 时仍覆盖 Change 01–03。
