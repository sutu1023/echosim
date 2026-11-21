# Tasks

## 1. 迁移、数据模型与配置

- [x] 1.1 新增 `app/migrations/0002_jobs_runs_events.sql`：创建 `runs`（含 `fail_at_step`）、`jobs`（含 `lease_token`）、`run_steps`、`run_events` 四张表、`jobs_active_operation_idx` 部分唯一索引与 `jobs_claimable_idx`/`jobs_lease_idx`，全部为纯新增对象；在已有数据卷上连续执行两次 `make migrate`，确认第一次输出 `已应用迁移: 0002_jobs_runs_events`、第二次输出 `无待应用迁移`，并用 `docker compose exec -T db psql -U echosim -d echosim -c '\d jobs'` 核对索引与约束存在
- [x] 1.2 在 `tests/test_migrate.py` 增加断言：`discover_migrations(app/migrations)` 返回 0001 与 0002 且顺序正确，`split_sql_statements` 能从 0002 中拆出包含四张表与部分唯一索引的语句；运行 `.venv/bin/python -m unittest tests.test_migrate` 通过
- [x] 1.3 在 `app/config.py` 增加 `job_lease_seconds`、`job_heartbeat_seconds`、`worker_poll_interval_seconds`、`test_task_step_delay_ms` 四个带安全默认值的配置，并同步写入 `.env.example`、`docker-compose.yml`（`app` 与 `worker` 的服务环境）和 `README.md` 配置表；用 `python -c "from app.config import settings; print(settings.job_lease_seconds, settings.job_heartbeat_seconds)"` 确认默认值可加载，并逐项核对三处文件与 README 表格一致

## 2. 任务服务与 HTTP 契约

- [x] 2.1 新增 `app/services/jobs.py`：实现开始（用 `pg_advisory_xact_lock` 串行化同一 `(project, operation_key)`，锁内查活动任务→命中返回 200，否则插入 `runs`（含 `fail_at_step`）+ `jobs` 并返回 202；整个事务回滚保证冲突路径不留下孤立 run）、列表（倒序 limit 20）、详情（`awaitingTakeover` 以数据库时间计算并返回 `leaseExpiredSeconds`）、取消（行锁 + 终态幂等 + `canceled` 事件的 `seq = MAX(seq)+1`）、副作用计数；在 `tests/test_jobs_service.py` 用可注入的假 session 覆盖“终态不可逆”“取消已终态返回现状”“取消序号取 MAX(seq)+1”“活动任务命中不插入”四条纯逻辑，运行 `.venv/bin/python -m unittest tests.test_jobs_service` 通过
- [x] 2.2 在 `app/errors.py` 增加 `JobNotFound`（404 `{"detail":"任务不存在"}`）与 `InvalidCursor`（400），在 `app/main.py` 增加对应异常处理器；新增 `tests/test_jobs_routes.py`，用 `TestClient` + `dependency_overrides` 断言匿名开始/查询/取消返回 401 且不访问数据库、他人项目返回 404 `{"detail":"项目不存在"}`、自己项目的非法 `jobId` 返回 404 `{"detail":"任务不存在"}`、非法 `operation`/`stage`/`failAtStep=0` 返回 422；运行 `.venv/bin/python -m unittest tests.test_jobs_routes` 通过
- [x] 2.3 新增 `app/routers/jobs.py`：注册开始（202/200）、列表、详情（含 `awaitingTakeover`）、取消、副作用五个 JSON 接口，统一“`require_user` → `resolve_owned_project` → 项目内解析 jobId”的顺序，路由层不自行拼装归属过滤；用 `curl` 对真实环境手工核对开始返回 202 与 `jobId`/`runId`、重复开始返回 200 且标识相同、他人项目返回 404，并把响应码记录到任务 8 的验收清单

## 3. worker 进程与编排

- [x] 3.1 新增 `app/services/worker.py`：实现原子领取（`queued` 或租约已过期的 `running`，`FOR UPDATE SKIP LOCKED`，生成新 `lease_token`，无归还扫描进程）、心跳续期（`WHERE id AND lease_token AND status='running' AND lease_expires_at > now()`，0 行即失去租约）、单步事务（行锁 + 令牌/状态/数据库时间守卫 + `run_steps` `ON CONFLICT DO NOTHING` + `run_events` 追加 + 限定 `id` 与令牌的 `next_step` 推进）、受控失败与取消边界，所有写操作均不得脱离 `id` 与 `lease_token`；为“游标与序号推进”“取消/失败不追加 `completed`”“令牌不匹配时零写入”写 `tests/test_worker_steps.py` 单元测试，运行 `.venv/bin/python -m unittest tests.test_worker_steps` 通过
- [x] 3.2 新增 `app/worker.py` 主循环（领取→执行→空闲轮询→SIGTERM 收尾），在 `docker-compose.yml` 增加无端口的 `worker` 服务（`command: ["python", "-m", "app.worker"]`、用 worker 自己的“心跳文件 15 秒内更新”探针覆盖（禁用）镜像继承的 `127.0.0.1:8080/livez` 探针、`depends_on` 的 `db` 与 `app` 均为 healthy、`restart: unless-stopped`、复用同一镜像与环境变量）；用仓库入口 `make up` 后 `docker compose ps` 显示 app、db、worker 三个容器运行且 app 仍为 healthy，`docker compose logs worker` 出现带 `jobId`/`attempt` 的领取日志
- [x] 3.3 手工验证中断恢复：以 `TEST_TASK_STEP_DELAY_MS=800 make up` 启动，开始一个任务并用 `docker compose stop worker` 在第 2–3 步后中断，记录 `jobId`、`runId`、`stepsApplied`、`attempts`；确认持久状态仍为 `running` 且详情在 `lease_expires_at` 后 2 秒内返回 `awaitingTakeover: true`，`docker compose start worker` 后任务走到终态，`/effects` 返回 `total=10`、`distinctSteps=10`、`steps=[1..10]`，把实际数值写入任务 8 的待签收清单
- [x] 3.4 验证 worker 隔离：仅 `docker compose stop worker` 时请求 `GET /health` 与 `GET /livez`，确认分别返回 200 与 200 且 `app` 容器 ID/PID/启动时间/重启次数不变；再 `docker compose start worker` 确认任务可继续，结果记入任务 8 清单
- [x] 3.5 新增验收专用 `app/lease_probe.py`（不出现在 app/worker 正常调用路径）：`--mode two-jobs` 建两个 probe 任务并分别领取，A 提交 1 步后逐列比对 B 的 `status`/`next_step`/`lease_token`/`lease_expires_at`/`attempts` 未变；`--mode counts` 返回项目的 `jobs`/`runs` 行数；`--mode takeover --job-id` 回放“A 提交到 3 → 强制过期 → B 提交到 5 → A 用旧令牌续租与写 6 被拒 → B 完成 6–10”；`--mode cancel-race` 用两个连接让最后一步与取消竞争 5 次并断言只有一个终态；用 `docker compose exec -T app python -m app.lease_probe --mode two-jobs --project-id <id>` 实测并记录 JSON
- [x] 3.6 修正租约判断：`heartbeat` 与 `commit_step` 先 `FOR UPDATE` 锁行，再用独立语句读 `clock_timestamp()` 核对令牌/状态/到期时间，失效即回滚；`app/lease_probe.py --mode lease-wait` 在真实 PostgreSQL 中复现“到期前开始、锁等待跨过到期点”，断言 `heartbeat=false`、`commit_step=lost`、租约未延长、步骤与事件数不变；运行 `.venv/bin/python -m unittest tests.test_worker_steps` 通过

## 4. 事件持久化与 SSE

- [x] 4.1 新增 `app/services/run_events.py`：事件追加（与步骤同事务，`seq` 由 `next_step` 推导）、游标解析（无游标视为 0；`Last-Event-ID` 优先于 `after`；合法范围 `0..MAX(seq)`，`MAX(seq)+1` 与非法值抛 `InvalidCursor`）、`seq > cursor` 补发查询、终态判定、`simulatedAt = simulated_epoch + (seq-1)×1min` 计算；在 `tests/test_run_events.py` 覆盖“`<runId>:4` 解析成功”“`after=4` 合法且不重发 4”“`after=MAX+1` 非法”“跨 run/负数/非数字游标非法”“事件字段六项齐全”，运行 `.venv/bin/python -m unittest tests.test_run_events` 通过
- [x] 4.2 在 `app/routers/jobs.py` 增加 `GET …/jobs/{jobId}/events`：`format=sse`（默认）返回 `text/event-stream`，帧为 `retry: 1000`、`id: <runId>:<seq>`、`event: run-event`、单行 JSON `data`，每 15 秒 `: keep-alive`，终态后发 `event: stream-end` 并关闭；`format=json` 返回同一组装逻辑的补发结果；鉴权、归属与游标校验全部在构造响应前完成，失败返回 JSON 400/401/404/503；用 `tests/test_jobs_routes.py` 断言匿名与 B 的订阅返回 JSON 401/404 且 `content-type` 不是 `text/event-stream`，非法游标返回 400 JSON
- [x] 4.3 用 `curl -N` 与 `curl '…/events?format=json&after=4'` 手工验证同一任务的两种读取方式给出相同序号集合，确认 `Last-Event-ID` 优先于 `after`、`after` 等于最大值时继续等待、`after` 超出最大值返回 400、心跳不产生 `id`、终态任务用 `after=LAST` 立即收到 `stream-end`，并把实测帧样例写入任务 8 清单

## 5. 前端日志处理与测试任务面板

- [x] 5.1 新增 `web/src/workbench/job-log.ts`（以“已完整处理的连续 seq”作为 `cursor`、`(runId, seq)` 去重、乱序暂存、忽略其他 run、缺口标记、终态标记）与 `web/tests/job-log.test.mjs`，覆盖“补发 1–4 后实时又来 3、4、5 只显示一次”“已有 1–4 收到 6 时游标仍为 4，收到 5 后连续到 6”“切换任务丢弃旧 run”“终态后不再追加”；运行 `cd web && npm test` 通过，且 `stage-links` 回归仍通过
- [x] 5.2 新增 `web/src/workbench/job-stream.ts`：用同源 `EventSource` 订阅 `…/events?after=<cursor>`，把帧交给 `job-log` 的同一 reducer，`stream-end` 后 `close()`，`error` 时按 `CONNECTING`/`CLOSED` 区分“自动重连/手动重试”，重连与手动重新同步都使用连续 `cursor` 而非最大 `seq`；运行 `cd web && npm run typecheck` 通过
- [x] 5.3 新增 `web/src/views/JobPanel.vue` 并在 `web/src/i18n.ts` 增加中英文案：显示 `jobId`、`runId`、状态、`attempts`、已提交/总步数、副作用计数与事件日志，提供开始、取消、重新连接与“这是连通性自检，不产生推演结论”的说明；在 `WorkbenchView.vue` 挂载到共享上下文栏与图谱布局工具条入口（三种布局、五个阶段都可打开），开始/取消时只更新面板状态、不改动路由；运行 `cd web && npm run typecheck && npm run build` 通过
- [x] 5.5 修正面板错误状态：SSE 重连提示优先于轮询错误，读取成功后清空轮询错误并保留日志与游标；`web/tests/job-panel-state.test.mjs` 覆盖“断线重连不被 503 覆盖”“成功后无提示”“终态优先”
- [x] 5.6 首次任务列表读取失败时提供“重试读取”按钮（`data-action="retry-list"`），只发 GET `/jobs`，成功后恢复已有任务、日志与副作用计数且不改动 URL；在浏览器注入一次列表 503 后验证
- [x] 5.4 实现面板五种状态提示（SSE 中断 / worker 中断 / 任务失败 / 任务取消 / 服务不可用）与**从可测起点计算**的时限文案（断开后 3 秒内提示、网络恢复时刻起 10 秒内 `cursor == lastSeq`、租约到期后 2 秒内 `awaitingTakeover`、终态事件写入后 2 秒内出现、503 沿用 5 秒），确保连接中断不显示失败、worker 中断不显示取消、失败与取消不显示成功；在浏览器逐项触发并记录实际耗时，写入任务 8 的待签收清单（此任务不写 `docs/acceptance/`）

## 6. 自动化验收与 CHANGE 选择器

- [x] 6.1 扩展 `scripts/verify.py`：新增 `--change` 参数与 `CHANGE` 环境变量，未注册值以退出码 2 结束并在报告中写入“未注册的变更”失败场景；报告中新增 `change` 与 `job_evidence`（`job_id`、`run_id`、`attempts_before`、`attempts_after`、`first_segment_seqs`、`second_segment_seqs`、`resumed_from`、`effects_total`、`effects_steps`、`steps_at_stop`、`old_token_rejected`、`job_rows_added`、`run_rows_added`）；运行 `python3 -m py_compile scripts/verify.py` 与 `CHANGE=not-a-change python3 scripts/verify.py`，确认退出码 2 且报告含失败场景
- [x] 6.2 在默认 `make verify` 中加入任务底座基础场景：开始返回 202 与两个标识、状态可查询、确定性任务到 `succeeded` 且事件序号 1–10、匿名 401、B 访问 A 的任务与不存在项目同为 404、停库期间带 Cookie 返回 503 并保留 Cookie、只停 worker 时 `/health` 与 `/livez` 仍为 200 且 `app` 容器身份不变；确认 Change 01–03 全部既有场景仍在报告中按原顺序通过
- [x] 6.3 在 `CHANGE=2026-11-21-track-background-jobs` 中加入变更专属场景（脚本在 `compose up` 前设置 `TEST_TASK_STEP_DELAY_MS=800`）：`lease_probe` 两任务隔离；停 worker 后并发两个 POST 并断言一个 202/一个 200、`jobs`/`runs` 各 +1；真实 SSE **读完 `seq=4` 帧（id + data + 空行）**后断开，记录第一段 `[1,2,3,4]`、以 `after=4` 重连记录第二段 `[5..10]` 并断言拼接为 1–10、`format=json` 交叉核对；`Last-Event-ID` 优先于 `after`；`after=lastSeq+1` 为 400 JSON；终态 `after=10` 在 2 秒内收到 `stream-end`；worker 中断接手（`running` + `awaitingTakeover`、`attempts` 递增、`/effects` 为 10）；`lease_probe --mode takeover` 旧令牌被拒；运行中取消（3 步后 `seq=4` 的 `canceled`、再取消幂等）；`failAtStep=3` 持久化并在 worker 重启后仍失败于 3；终态后新任务新标识；每个场景同时核对详情、终态事件与副作用计数；场景自清理（取消遗留任务、恢复 worker 与数据库）
- [x] 6.4 在修复后的干净提交 `a49a292` 上带 `EXPECTED_SHA=$(git rev-parse HEAD)` 连续两次执行 `make verify` 与两次 `make verify CHANGE=2026-11-21-track-background-jobs`，四次退出码均为 0、`tested_sha=a49a292ec946fa171e724caf7cee2adcec3217d9`、`dirty=false`；报告为 `runtime/verify/run-20261121T062122Z.json`、`run-20261121T062225Z.json`、`run-20261121T062434Z.json`、`run-20261121T062631Z.json`（变更专属各 37 场景），两段序号 1–4 / 5–10、接手 `attempts` 1→2、旧令牌 `heartbeat=false`/`step=lost`、锁等待回归 `heartbeat=false`/`commit_step=lost` 且 `expiry_extended=false`、步骤与事件均为 0

## 7. 文档与工程规则

- [x] 7.1 更新 `README.md`：新增接口表、状态转换表、事件字段与 SSE 契约（`id`、游标、`stream-end`、心跳不计数）、`CHANGE` 用法、worker 与 `make up/down` 的关系、任务相关人工走查步骤与可观察时限；逐条复制文档中的命令与 `curl` 示例实际执行，确认输出与文档一致
- [x] 7.2 更新 `AGENTS.md`：新增“后台任务与事件流”规则——PostgreSQL 是唯一队列事实来源、`app` 不执行长任务、事件先落库再推送、`seq` 与 `(runId, seq)` 唯一、SSE 先鉴权后发送字节、worker 故障不改变 `/health`/`/livez`、任务接口必须复用 `resolve_owned_project`、`make verify CHANGE=<id>` 的断言边界；逐条与本次 delta 规范对照，确认没有弱化既有要求

## 8. 集成验收与人工签收准备

- [x] 8.1 提交后确认 `git status --porcelain` 为空，按任务 6.4 在同一 SHA 生成四份报告，核对 `tested_sha`、`dirty`、`job_evidence` 与两次运行一致；待签收清单含四份报告路径、真实 `jobId`/`runId`（`154772e4…`/`1f0e35c1…`、`31c65920…`/`c1317da1…`）、两段 SSE 实际序号、接手前后 `attempts`、旧令牌与锁等待回归结果
- [x] 8.2 浏览器复验由 Codex 在桌面 Chrome 完成并经项目作者签收：断线从第 4 条重连、恢复后 1738 ms 内补齐 5–10（`jobId=c4e04aa8…`/`runId=cd15fe59…`）；worker 停止时已提交 4 步、684 ms 观察到等待接手、恢复后 2578 ms 继续（`jobId=cd1561eb…`/`runId=dce15334…`）；首次列表 503 后"重试读取"恢复原任务与 3 条事件（`jobId=4efb97b2…`）；受控失败、运行中取消、双击与排队取消、订阅隔离均通过。正式记录 `docs/acceptance/2026-11-21-a49a292.md` 与脱敏截图已提交，`make verify` 与 CI 不触碰该目录。
