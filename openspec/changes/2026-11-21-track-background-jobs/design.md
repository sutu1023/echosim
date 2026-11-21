# Design

## Context

Change 03 合并提交为 `f4bdc5e`。当前后端只有 `health`、`auth`、`projects`、`history` 四组路由，所有写操作都在请求内完成；`app/db.py` 提供 SQLAlchemy async session 与 2 秒连接/命令超时，`app/migrate.py` 在容器启动时前向应用 `app/migrations/*.sql`。前端工作台已有五阶段注册表、三种布局、`layout`/`lang` 查询参数与 `stageLocation()`，但阶段内容恒为空态（`stageHasData = false`）。

本变更引入第一个长任务执行路径，必须在不破坏既有 401/404/503 契约、不改变 `/health` 与 `/livez` 语义、不引入新前端运行时依赖的前提下，把“队列、租约、幂等、事件流、断线恢复”这套机制一次做对。需求见 `specs/background-job-queue/spec.md`、`specs/run-event-stream/spec.md`、`specs/project-workbench/spec.md`，以及 `project-isolation`、`health-readiness`、`reproducible-verification` 的 delta。

## Goals / Non-Goals

**Goals:**

- 用一张 `jobs` 表同时承担“队列”与“控制状态”，用数据库约束而非进程内存保证唯一性与幂等。
- 让“做到第几步”只有一个权威来源（`run_steps`），使任意 worker 接手都能得出确定结论。
- 让事件流在断线、刷新、worker 崩溃后都能按 `seq` 完整还原，且补发与实时共用一套前端 reducer。
- 让验收结论可以只看数据库/接口就判定“有无重复副作用”，把页面观感留给人工走查。

**Non-Goals:**

- 不做任务优先级、定时调度、多队列、重试策略配置、分布式追踪或指标系统。
- 不实现材料入库、真实推演、报告生成与案例回放；测试任务不写任何阶段业务数据。
- 不引入 Redis/Celery/RabbitMQ、Alembic、Pinia、i18n 库或浏览器端 E2E 框架。
- 不新增 worker 的 HTTP 端口或健康端点；worker 的可观察性通过任务状态、日志与 `docker compose` 表达。

## Decisions

### 1. 数据模型：job 管队列，run 管事件

新增 `0002_jobs_runs_events.sql`（只前向，纯新增表，可在已有数据卷上安全启动）：

```sql
CREATE TABLE runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_by uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind text NOT NULL,                  -- 本变更只有 'test.simulation'
    stage text NOT NULL,                 -- 五个阶段 slug 之一
    total_steps int NOT NULL,
    simulated_epoch timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT runs_kind_check CHECK (kind IN ('test.simulation')),
    CONSTRAINT runs_stage_check CHECK (stage IN (
        'evidence-graph','roles-environment','dual-platform-run','report','interaction')),
    CONSTRAINT runs_total_steps_check CHECK (total_steps BETWEEN 1 AND 100)
);

CREATE TABLE jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    operation_key text NOT NULL,         -- 'test-simulation:v1'
    status text NOT NULL DEFAULT 'queued',
    next_step int NOT NULL DEFAULT 0,    -- 已连续提交的步骤数
    attempts int NOT NULL DEFAULT 0,
    lease_owner text,
    lease_expires_at timestamptz,
    cancel_requested_at timestamptz,
    terminal_reason text,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT jobs_status_check CHECK (status IN (
        'queued','running','succeeded','failed','canceled'))
);

CREATE UNIQUE INDEX jobs_active_operation_idx
    ON jobs (project_id, operation_key)
    WHERE status IN ('queued','running');
CREATE INDEX jobs_claim_idx ON jobs (created_at) WHERE status = 'queued';
CREATE INDEX jobs_lease_idx ON jobs (lease_expires_at) WHERE status = 'running';

CREATE TABLE run_steps (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_index int NOT NULL,
    step_key text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    applied_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, step_index),
    CONSTRAINT run_steps_key_unique UNIQUE (run_id, step_key)
);

CREATE TABLE run_events (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq int NOT NULL,
    type text NOT NULL,
    stage text NOT NULL,
    simulated_at timestamptz NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, seq),
    CONSTRAINT run_events_type_check CHECK (type IN (
        'progress','failed','canceled','completed')),
    CONSTRAINT run_events_seq_check CHECK (seq >= 1)
);
```

关系：`project 1—N jobs`、`job 1—1 runs`、`run 1—N run_events`、`run 1—N run_steps`。`jobId` 是队列与控制身份（租约、`attempts`、取消），`runId` 是执行与事件身份（`seq` 空间、副作用）。接手只换 `jobs.lease_owner`/`attempts`，不动 `run_id`，因此 `seq` 连续。终态后再次开始会新建 `jobs` 与 `runs` 两行，新 run 的 `seq` 从 1 开始。

**为什么状态只放在 `jobs`**：两处状态必然要同步，而同步点就是每个步骤事务；与其引入第二份真相，不如让 `runs` 只承载不可变身份（项目、阶段、总步数、模拟时钟起点）。备选方案是 `runs.status + jobs.status` 双写，被否决。

**为什么副作用单独建表**：验收要“可查询、可计数且不看页面即可判定”，`run_steps` 的 `PRIMARY KEY (run_id, step_index)` 让重复执行必然撞约束，计数天然幂等；把副作用塞进事件 `payload` 会让“事件数 == 步数”与“副作用数 == 步数”两个断言互相耦合。

### 2. 接口与状态转换

所有接口挂在项目下，先 `require_user`，再 `resolve_owned_project`，最后在“已解析项目”范围内解析 `jobId`。

| 方法 | 路径 | 请求要点 | 成功响应 | 主要失败 |
| --- | --- | --- | --- | --- |
| POST | `/api/projects/{projectId}/jobs` | `{"operation":"test-simulation","stage":"<五阶段之一>","failAtStep":null}` | 新建 202；已存在活动任务 200，均返回 `{jobId,runId,status,attempts,stepsApplied,totalSteps,createdAt,deduplicated}` | 401 未登录；404 项目不存在；422 非法 operation/stage/failAtStep；503 服务不可用 |
| GET | `/api/projects/{projectId}/jobs` | — | 200 `{"jobs":[…]}`，按创建时间倒序，最多 20 条 | 401 / 404 / 503 |
| GET | `/api/projects/{projectId}/jobs/{jobId}` | — | 200 单个任务详情（含 `runId`、`attempts`、`stepsApplied`、`terminalReason`、`awaitingTakeover`） | 401 / 404 项目不存在 / 404 任务不存在 / 503 |
| POST | `/api/projects/{projectId}/jobs/{jobId}/cancel` | 空体 | 200 `{job, cancelApplied}`；对终态任务 `cancelApplied=false` | 401 / 404 / 503 |
| GET | `/api/projects/{projectId}/jobs/{jobId}/events` | `Last-Event-ID` 或 `after`；`format=sse`（默认） | 200 `text/event-stream` | 400 游标无效（JSON）；401 / 404 / 503（JSON） |
| GET | `/api/projects/{projectId}/jobs/{jobId}/events?format=json&after=N` | 同游标 | 200 `{"runId","after","lastSeq","terminal","events":[…]}` | 同上 |
| GET | `/api/projects/{projectId}/jobs/{jobId}/effects` | — | 200 `{"runId","total","distinctSteps","steps":[1..N],"totalSteps","duplicated":false}` | 401 / 404 / 503 |

`awaitingTakeover` 由服务端按 `status='queued' AND attempts > 0` 或 `status='running' AND lease_expires_at < now()` 计算，避免前端重复实现判定。`format=json` 的目的只有一个：让只用标准库的 `make verify` 能在不解析 SSE 的情况下断言序号集合；页面仍只走 SSE。

状态转换（唯一状态字段 `jobs.status`）：

| 起点 | 触发 | 终点 | 事务内同时发生 |
| --- | --- | --- | --- |
| （无） | 开始请求，且 `(project, operation)` 无活动任务 | `queued` | 插入 `runs` + `jobs`；不写事件、不写副作用 |
| （无） | 开始请求，但已有活动任务 | 不变 | 返回既有 `jobId`/`runId`，`deduplicated=true`，200 |
| `queued` | worker 领取（租约生效） | `running` | `attempts += 1`，`started_at` 首次写入 |
| `queued` | 取消 | `canceled` | 追加 `canceled` 事件 `seq=1`；`finished_at` |
| `running` | 第 1–9 步提交 | `running` | `run_steps` + `progress` 事件 `seq=step` + `next_step=step` |
| `running` | 第 10 步提交 | `succeeded` | `run_steps(10)` + `completed` 事件 `seq=10` + `finished_at`（同一事务） |
| `running` | 受控失败于第 N 步 | `failed` | 不写第 N 步副作用；追加 `failed` 事件 `seq=N`；`finished_at` |
| `running` | 取消 | `canceled` | 追加 `canceled` 事件 `seq=next_step+1`；`finished_at` |
| `running` | 租约过期（任意 worker 的归还扫描） | `queued` | `lease_owner=NULL`，`attempts` 保留 |
| `succeeded`/`failed`/`canceled` | 任何请求 | 不变 | 取消返回 200 与现状；开始请求允许新建下一个任务 |

**取消与完成同时发生**：两个事务都对同一 `jobs` 行 `SELECT … FOR UPDATE`，因此串行化。先提交者决定终态；后到者看到终态后只返回现状，不追加事件。这满足“以持久化结果为准”，不需要额外的时间戳比较。

### 3. 领取、续期、接手与旧 worker 阻断

worker 主循环（`app/worker.py` → `app/services/worker.py`）：

```text
loop:
  if stop_requested: break
  job = claim_next()            # 过期租约归还 + FOR UPDATE SKIP LOCKED 领取
  if job is None: sleep(WORKER_POLL_INTERVAL_SECONDS); continue
  run_job(job)                  # 逐步提交 + 心跳
```

领取 SQL（单条语句完成归还与领取，避免“归还”和“领取”之间被别的 worker 插队）：

```sql
UPDATE jobs j
   SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
 WHERE j.status = 'running' AND j.lease_expires_at < now();

WITH candidate AS (
    SELECT id FROM jobs WHERE status = 'queued'
     ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
)
UPDATE jobs j
   SET status = 'running', lease_owner = :worker_id,
       lease_expires_at = now() + make_interval(secs => :lease_seconds),
       attempts = attempts + 1, started_at = COALESCE(started_at, now()), updated_at = now()
  FROM candidate c WHERE j.id = c.id
RETURNING j.id, j.run_id, j.next_step, j.attempts;
```

单步事务（幂等的最小单元）：

```sql
BEGIN;
SELECT status, lease_owner, lease_expires_at, next_step, run_id
  FROM jobs WHERE id = :job_id FOR UPDATE;           -- 与取消/接管互斥
-- 守卫：status='running' AND lease_owner=:me AND lease_expires_at > now() AND next_step=:step-1
INSERT INTO run_steps (run_id, step_index, step_key, payload)
     VALUES (:run_id, :step, :step_key, :payload)
ON CONFLICT (run_id, step_index) DO NOTHING;         -- 重复执行在此变成 0 行
-- 仅当上面插入 1 行时才继续；插入 0 行表示该步已由前一次执行业务提交，
-- 直接跳过事件追加与 next_step 推进，避免重复事件。
INSERT INTO run_events (run_id, seq, type, stage, simulated_at, payload)
     VALUES (:run_id, :seq, :type, :stage, :simulated_at, :payload);  -- PK 兜底防重
UPDATE jobs SET next_step = :step, lease_expires_at = now() + :lease, updated_at = now();
COMMIT;
```

心跳任务每 `JOB_HEARTBEAT_SECONDS` 执行 `UPDATE jobs SET lease_expires_at=now()+lease WHERE id=:id AND lease_owner=:me AND status='running' RETURNING id`；返回 0 行即判定失去租约，worker 置 `lost` 标志并退出该任务，不再写任何数据。

**“接手者如何知道已经做过什么”**：`run_steps` 的 `MAX(step_index)`（并由 `jobs.next_step` 在同一事务维护）就是唯一权威答案；因为副作用、事件与 `next_step` 三者同事务提交，不存在“写了副作用但没推进”或“推进了但事件没写”的中间态。接手者 `next_step=3` 就从第 4 步开始。

**“旧 worker 恢复后为何不能重复提交”**：步骤事务的第一条语句是 `SELECT … FOR UPDATE` 加三重守卫（`status='running'`、`lease_owner=:me`、`lease_expires_at > now()`）。接管发生后 `lease_owner` 已不是旧 worker，守卫失败即回滚，不写 `run_steps`、不写 `run_events`。即使守卫被绕过（例如时钟回拨），`PRIMARY KEY (run_id, step_index)` 也让副作用写入变成 0 行、`(run_id, seq)` 主键让重复事件插入失败，仍不会产生重复。

**为什么不用 `NOTIFY` 而用轮询**：SSE 阶段每 0.5 秒按 `seq > last` 查一次已提交事件，数据库是唯一事实来源，不存在“通知丢了就漏事件”的窗口，也不需要在 `app` 内维护第二条 LISTEN 连接的生命周期。代价是 ≤0.5 秒的推送延迟，对日志 UI 可接受；`NOTIFY` 留作后续优化，不改变契约。

### 4. 事件契约与 SSE

事件字段固定为 `runId`、`seq`、`type`、`stage`、`simulatedAt`、`payload`。`simulatedAt = runs.simulated_epoch + (seq-1) × 1 分钟`（UTC ISO-8601，秒精度），因此同一确定性任务重复执行的相对偏移稳定，与 worker 停机时长无关。

确定性测试任务共 10 步、10 条业务事件：`seq=1..9` 为 `progress`，`seq=10` 为 `completed`。**终态事件计入 10 条**；SSE 心跳与 `stream-end` 不计入。取消的任务在第 3 条之后取消时事件为 `1,2,3=progress` + `4=canceled`；受控失败于第 3 步时事件为 `1,2=progress` + `3=failed`（第 3 步副作用不提交）。

SSE 帧：

```text
retry: 1000

id: 5f0f…:4
event: run-event
data: {"runId":"5f0f…","seq":4,"type":"progress","stage":"dual-platform-run","simulatedAt":"2026-11-21T09:04:00Z","payload":{"step":4,"label":"…","stepsApplied":4,"totalSteps":10}}

: keep-alive

event: stream-end
data: {"runId":"5f0f…","lastSeq":10,"terminal":true}
```

游标解析（顺序固定）：`Last-Event-ID`（合法时） → `after` 查询参数 → 1。合法 `Last-Event-ID` 必须形如 `<runId>:<seq>` 且 `runId` 与目标任务一致；`after` 必须是 `0..lastSeq+1` 的整数（`0` 等价于从头补发，`lastSeq+1` 表示当前已无新事件），大于 `lastSeq+1` 视为非法游标并返回 400，避免把“未来游标”当成正常重连。非法游标在建立流之前返回 400 JSON，因此客户端不会拿到半截流。

订阅实现：FastAPI `StreamingResponse` 的 async generator。依赖（`require_user` → `resolve_owned_project` → job 作用域查询 → 游标校验）全部在响应对象构造前完成，所以 401/404/400/503 一定是 JSON 且没有事件字节。生成器内部循环：先 `SELECT … WHERE run_id=:run AND seq > :last ORDER BY seq` 补发；然后每 0.5 秒重复同一查询；每 15 秒无数据时发 `: keep-alive`；读到终态事件后发 `stream-end` 并 `return`；每次循环检查 `await request.is_disconnected()`。若中途数据库不可用，发一帧 `event: stream-error` 后关闭，客户端按“连接中断”处理并重连（重连会拿到 503 JSON）。

**补发与实时为什么不会漏/重**：两阶段共用同一个游标变量 `last`，每次只查 `seq > last` 并逐条发送后更新 `last`；补发结束与实时开始之间没有快照边界，因为“实时”第一次查询就是从同一个 `last` 继续查数据库。事件是只追加的，且 `(run_id, seq)` 唯一，所以不会出现“补发读到 5，实时又从 5 开始”。

### 5. 前端：一套 reducer，两种数据来源

新增 `web/src/workbench/job-log.ts`（纯函数，无 DOM）：

```ts
interface JobEvent { runId: string; seq: number; type: 'progress'|'failed'|'canceled'|'completed'; stage: string; simulatedAt: string; payload: Record<string, unknown> }
interface JobLog { runId: string | null; events: Map<number, JobEvent>; lastSeq: number; gap: boolean }
// applyEvents(log, incoming)：忽略其他 runId；按 (runId, seq) 去重；按 seq 排序；gap 检测
```

- SSE 实时帧与 `format=json` 补发结果都只经过 `applyEvents()`，页面渲染只读 reducer 输出，因此“实时”和“历史”不可能出现两套去重逻辑。
- 每个 run 是一个 reducer 实例；切换任务时新建实例，旧 run 的迟到帧因 `runId` 不匹配被丢弃。
- gap 判定：收到 `seq > lastSeq + 1` 时置 `gap=true` 并显示“检测到事件缺口 / 重新同步”，不做静默补齐。
- 重连使用浏览器原生 `EventSource`（同源自动带 Cookie，`retry: 1000` 生效，断线自动带 `Last-Event-ID`）；`stream-end` 后 `close()` 并标记 `ended`，不再重连；需要从历史重建时用 `format=json&after=lastSeq` 走同一 reducer。

面板 `web/src/views/JobPanel.vue` 挂在现有右侧上下文栏（并在图谱布局通过工具条入口打开），显示 `jobId`、`runId`、状态、`attempts`、已提交/总步数、副作用计数、事件日志与“开始/取消/重新连接”。它不写阶段内容状态：`stageHasData` 仍为 `false`，工作台外壳与空态规则保持不变。

四种提示的可观察时限（默认配置下）：SSE 中断提示 ≤3 秒（`EventSource` 报错即触发）；自动重连 15 秒内补齐；worker 中断在租约 6 秒到期后 ≤8 秒显示“等待接手”，worker 恢复后 ≤10 秒继续出事件；失败/取消终态事件 ≤2 秒出现在日志；503 沿用 5 秒内返回。

### 6. 配置、编排与隔离

新增配置（同步写入 `.env.example`、`docker-compose.yml`、`README.md` 配置表与 `app/config.py` 安全默认值）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `JOB_LEASE_SECONDS` | `6` | 租约时长；过期即允许接手 |
| `JOB_HEARTBEAT_SECONDS` | `2` | worker 续期间隔，必须小于租约 |
| `WORKER_POLL_INTERVAL_SECONDS` | `0.25` | 无任务时的轮询间隔 |
| `TEST_TASK_STEP_DELAY_MS` | `250` | 测试任务每步间隔；`make verify` 用它放大中断窗口 |

Compose 新增：

```yaml
worker:
  build: { context: ., dockerfile: Dockerfile }
  image: echosim-skeleton:dev
  command: ["python", "-m", "app.worker"]
  depends_on:
    db: { condition: service_healthy }
    app: { condition: service_healthy }   # 等迁移完成后才开始领取
  environment: 与 app 相同的 DATABASE_URL + 四个任务配置
  restart: unless-stopped
```

worker 无端口、无 healthcheck（`docker compose up --wait` 对无 healthcheck 的服务只要求 running）。迁移由 `app` 启动时的 `python -m app.migrate` 完成，worker 依赖 `app: service_healthy` 避免“表还没建就领取”。`app` 不感知 `worker` 存在，因此 `docker compose stop worker` 只影响任务进度，不改变 `/health`、`/livez` 与容器身份。

### 7. make verify 的 CHANGE 选择器

`Makefile`：`verify: $(PYTHON) scripts/verify.py $(if $(CHANGE),--change $(CHANGE),)`；`scripts/verify.py` 同时接受 `CHANGE` 环境变量与 `--change`。

- 无 `CHANGE`：保留 Change 01–03 全部场景，并追加任务底座基础场景（开始 202、状态可查询、确定性任务成功且事件 1–10、匿名 401、B 404 等价、停库 503 保留 Cookie、只停 worker 时 `/health`/`/livez` 不变）。
- `CHANGE=2026-11-21-track-background-jobs`：在基础场景上追加变更专属场景（见下表）。脚本在 `compose up` 前设置 `TEST_TASK_STEP_DELAY_MS=800`，让中断与断线窗口足够宽。
- 未注册的 `CHANGE`：退出码 2，报告写入“未注册的变更”失败场景，不执行任何任务场景。

变更专属场景顺序（放在停库恢复场景之后，避免与数据库停机互相干扰）：

1. `job-duplicate-start-concurrent`：先 `docker compose stop worker` 使任务停留在 `queued`；用 `ThreadPoolExecutor` 并发两个 POST；断言两次都成功且 `jobId` 相同、响应中恰有一个 `deduplicated=false`；`GET /jobs` 活动任务数为 1；随后取消该任务清理状态。
2. `job-resume-after-disconnect`：`docker compose start worker`；用 `urllib` 打开 SSE，逐行读到 `id: <runId>:4` 后关闭连接并记录 `disconnected_at_seq=4`；等待约 1 秒后以 `after=4` 重连，读到 `stream-end`；用 `format=json` 复核序号集合 `[1..10]`、长度 10、无重复；报告记录 `jobId`、`runId`、`disconnected_at_seq`、`resumed_from`、`seqs`。
3. `job-worker-crash-takeover`：新建任务，轮询状态直到 `stepsApplied >= 2`；`docker compose stop worker`；记录 `stepsAtStop` 与 `attempts=1`；断言 8 秒内 `awaitingTakeover` 为真（状态回到 `queued`）；`docker compose start worker`；轮询到终态；断言 `attempts >= 2`、`/effects` 返回 `total=10`、`distinctSteps=10`、`steps=[1..10]`、`duplicated=false`，事件 `seq` 仍为 1–10。
4. `job-cancel-running`：轮询到 `stepsApplied >= 2` 后 POST cancel；断言 200、状态 `canceled`、`cancelApplied=true`；事件终态为 `canceled` 且无 `completed`；再次 cancel 断言 `cancelApplied=false`。
5. `job-fail-controlled`：以 `failAtStep=3` 开始；断言终态 `failed`、终态事件 `seq=3`、无 `completed`、`stepsApplied=2`。
6. `job-restart-after-terminal`：对已终态任务再次开始；断言新 `jobId` 与新 `runId`，新 run 的 `seq` 从 1 开始。
7. `worker-isolation`：只停 worker 时 `/health` 200、`/livez` 200、`app` 容器身份不变；恢复 worker 后 `container-identity-stable` 仍通过。

可重复性：每个场景自带清理（取消遗留活动任务、恢复 worker、恢复数据库）；账号与项目沿用 Change 02 的幂等准备与唯一命名。

### 8. 能力—场景—验证方式

| 能力 | 代表场景 | 验证方式 |
| --- | --- | --- |
| `background-job-queue` | 开始返回 202；同项目同操作只产生一个有效任务；租约过期后接手；中断恢复后副作用仍为 10；取消与失败不误报成功 | `make verify`（基础 5 项）+ `make verify CHANGE=2026-11-21-track-background-jobs`（并发重复开始、中断恢复、取消、失败、终态后重启） |
| `run-event-stream` | 事件 1–10 各一次；`seq=4` 断线后 `after=4` 补齐；`Last-Event-ID` 优先；补发与实时重叠不重复；终态后 `stream-end` 并关闭 | 断线续传由 `make verify CHANGE=…` 用真实 SSE 连接验证；去重/排序/缺口提示由 `cd web && npm test`（`web/tests/job-log.test.mjs`）验证；帧观感由人工走查 |
| `project-workbench` | 面板标识为测试任务；开始不改变项目/阶段/布局/语言；四种状态文案可区分；刷新后还原 | 布局、语言、阶段保持、文案与可观察时限由人工走查（浏览器）记录；面板不改变阶段空态由人工走查核对 |
| `project-isolation` | 匿名 SSE 401；B 订阅 A 的事件流 404 且与不存在项目一致；停库 503 保留 Cookie；归属先于游标 | `make verify`（基础：匿名 401、B 404 等价、停库 503 保留 Cookie）；SSE 场景的错误响应为 JSON 由 `make verify CHANGE=…` 断言 |
| `health-readiness` | 只停 worker 时 `/health` 200、`/livez` 200、容器身份不变 | `make verify`（`worker-isolation` 场景） |
| `reproducible-verification` | 无 `CHANGE` 保留 01–03 回归；非法 `CHANGE` 退出码 2；报告记录 `jobId`/`runId`/序号 | `make verify` 与 `make verify CHANGE=…` 连续两次执行；报告字段人工核对 |

### 9. 为什么要一个“测试任务”

材料入库与真实推演尚未实现，但队列、租约、事件与断线恢复必须有真实执行体才能验收。测试任务提供确定性（固定 10 步、固定事件数、固定模拟时钟）与可控性（`failAtStep`、可调步进），让“重复执行不产生重复副作用”变成可数事实；它不写任何阶段业务数据，也不改变阶段进入条件，因此不会把假数据带进产品页面。

## Risks / Trade-offs

- [每 0.5 秒轮询带来额外数据库读] → 查询是 `(run_id, seq)` 主键范围扫描，单行索引读；同一任务同时只保留一个订阅（页面切换时关闭旧连接）；后续可加 `LISTEN/NOTIFY` 而不改契约。
- [worker 无 healthcheck，崩溃循环不易发现] → 任务状态 `awaitingTakeover` 与 worker 日志按 `jobId`/`attempt` 记录；`make verify` 的中断恢复场景覆盖“停止→接手”路径。
- [部分唯一索引 + `ON CONFLICT` 推断失败] → 实现时优先用 `INSERT … ON CONFLICT (project_id, operation_key) WHERE status IN ('queued','running') DO NOTHING`；若推断不被接受，退化为捕获唯一冲突（SQLSTATE 23505）后重查活动任务，对外契约不变。
- [取消在步骤中间到达] → 取消事务与步骤事务争同一行锁，最坏等待一个步骤事务（毫秒级）；取消语义是“下一步边界生效”，与规格一致。
- [测试步进太慢导致验收变长] → 默认 250ms 只在人工调试时生效；`make verify` 用 800ms 仅在变更专属场景放大窗口，单个任务约 8 秒，总增量可控。
- [迁移只前向] → `0002` 纯新增表与索引，不触碰既有表；回滚通过回退代码 + 保留未使用的表实现，符合仓库既有策略；需要彻底清理时用 `make reset`。
- [SSE 在反向代理后缓冲] → 响应头带 `Cache-Control: no-cache, no-transform` 与 `X-Accel-Buffering: no`；当前仅本机回环直连，无代理。
- [旧浏览器/自动重连风暴] → `retry: 1000` 固定 1 秒退避，连接在终态后关闭，避免终态任务被无限重连。

## Migration Plan

1. 合并后执行 `make up`：`app` 启动时自动应用 `0002`（已应用则跳过），随后 `worker` 因 `app: service_healthy` 才开始领取。
2. 已有数据卷无需回填；旧任务表不存在，历史项目不受影响。
3. 回退：回退代码提交并 `make up`；`0002` 建的表保留但不被使用（只前向策略）。需要完全清理时 `make reset`（破坏性）。
4. 验收：先提交，确认工作树干净；依次执行 `make verify` 与 `make verify CHANGE=2026-11-21-track-background-jobs`，两次均须 `tested_sha == HEAD`、`dirty=false`，并把 `jobId`、`runId`、`disconnected_at_seq`、`seqs`、`stepsAtStop`、`attempts` 记入 `docs/acceptance/` 记录。
