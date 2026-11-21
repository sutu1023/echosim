# Tasks

## 1. 迁移、数据模型与配置

- [ ] 1.1 新增 `app/migrations/0002_jobs_runs_events.sql`：创建 `runs`、`jobs`、`run_steps`、`run_events` 四张表、`jobs_active_operation_idx` 部分唯一索引与 `jobs_claim_idx`/`jobs_lease_idx`，全部为纯新增对象；在已有数据卷上连续执行两次 `make migrate`，确认第一次输出 `已应用迁移: 0002_jobs_runs_events`、第二次输出 `无待应用迁移`，并用 `docker compose exec -T db psql -U echosim -d echosim -c '\d jobs'` 核对索引与约束存在
- [ ] 1.2 在 `tests/test_migrate.py` 增加断言：`discover_migrations(app/migrations)` 返回 0001 与 0002 且顺序正确，`split_sql_statements` 能从 0002 中拆出包含四张表与部分唯一索引的语句；运行 `.venv/bin/python -m unittest tests.test_migrate` 通过
- [ ] 1.3 在 `app/config.py` 增加 `job_lease_seconds`、`job_heartbeat_seconds`、`worker_poll_interval_seconds`、`test_task_step_delay_ms` 四个带安全默认值的配置，并同步写入 `.env.example`、`docker-compose.yml`（`app` 与 `worker` 的服务环境）和 `README.md` 配置表；用 `python -c "from app.config import settings; print(settings.job_lease_seconds, settings.job_heartbeat_seconds)"` 确认默认值可加载，并逐项核对三处文件与 README 表格一致

## 2. 任务服务与 HTTP 契约

- [ ] 2.1 新增 `app/services/jobs.py`：实现开始（同一事务插入 `runs` + `jobs`，用 `INSERT … ON CONFLICT (project_id, operation_key) WHERE status IN ('queued','running') DO NOTHING` 做重复开始保护，冲突时重查活动任务并返回 `deduplicated=true`）、列表（倒序 limit 20）、详情、取消（行锁 + 终态幂等 + 追加 `canceled` 终态事件）、副作用计数（总数、去重步骤号、`duplicated` 标记）；在 `tests/test_jobs_service.py` 用可注入的假 session 覆盖“终态不可逆”“取消已终态返回现状”“活动任务唯一”三条纯逻辑，运行 `.venv/bin/python -m unittest tests.test_jobs_service` 通过
- [ ] 2.2 在 `app/errors.py` 增加 `JobNotFound`（404 `{"detail":"任务不存在"}`）与 `InvalidCursor`（400），在 `app/main.py` 增加对应异常处理器；新增 `tests/test_jobs_routes.py`，用 `TestClient` + `dependency_overrides` 断言匿名开始/查询/取消返回 401 且不访问数据库、他人项目返回 404 `{"detail":"项目不存在"}`、自己项目的非法 `jobId` 返回 404 `{"detail":"任务不存在"}`、非法 `operation`/`stage`/`failAtStep=0` 返回 422；运行 `.venv/bin/python -m unittest tests.test_jobs_routes` 通过
- [ ] 2.3 新增 `app/routers/jobs.py`：注册开始（202/200）、列表、详情（含 `awaitingTakeover`）、取消、副作用五个 JSON 接口，统一“`require_user` → `resolve_owned_project` → 项目内解析 jobId”的顺序，路由层不自行拼装归属过滤；用 `curl` 对真实环境手工核对开始返回 202 与 `jobId`/`runId`、重复开始返回 200 且标识相同、他人项目返回 404，并把响应码记录到任务 8 的验收清单

## 3. worker 进程与编排

- [ ] 3.1 新增 `app/services/worker.py`：实现过期租约归还 + `FOR UPDATE SKIP LOCKED` 领取、心跳续期（0 行即判定失去租约）、单步事务（行锁 + 三重守卫 + `run_steps` `ON CONFLICT DO NOTHING` + `run_events` 追加 + `next_step` 推进 + 终态写入同一事务）、受控失败与取消边界；为“游标与序号推进”“取消/失败不追加 `completed`”写 `tests/test_worker_steps.py` 单元测试，运行 `.venv/bin/python -m unittest tests.test_worker_steps` 通过
- [ ] 3.2 新增 `app/worker.py` 主循环（领取→执行→空闲轮询→SIGTERM 收尾），在 `docker-compose.yml` 增加无端口的 `worker` 服务（`command: ["python", "-m", "app.worker"]`、`depends_on` 的 `db` 与 `app` 均为 healthy、`restart: unless-stopped`、复用同一镜像与环境变量）；执行 `make up` 后 `docker compose ps` 显示 app、db、worker 三个容器运行，`docker compose logs worker` 出现带 `jobId`/`attempt` 的领取日志
- [ ] 3.3 手工验证中断恢复：以 `TEST_TASK_STEP_DELAY_MS=800 make up` 启动，开始一个任务并用 `docker compose stop worker` 在第 2–3 步后中断，记录 `jobId`、`runId`、`stepsApplied`、`attempts`；确认 8 秒内详情显示 `awaitingTakeover: true`，`docker compose start worker` 后任务走到终态，`/effects` 返回 `total=10`、`distinctSteps=10`、`steps=[1..10]`，把实际数值写入任务 8 的待签收清单
- [ ] 3.4 验证 worker 隔离：仅 `docker compose stop worker` 时请求 `GET /health` 与 `GET /livez`，确认分别返回 200 与 200 且 `app` 容器 ID/PID/启动时间/重启次数不变；再 `docker compose start worker` 确认任务可继续，结果记入任务 8 清单

## 4. 事件持久化与 SSE

- [ ] 4.1 新增 `app/services/run_events.py`：事件追加（与步骤同事务，`seq` 由 `next_step` 推导）、游标解析（`Last-Event-ID` 优先于 `after`，非法值抛 `InvalidCursor`）、`seq > last` 补发查询、终态判定、`simulatedAt = simulated_epoch + (seq-1)×1min` 计算；在 `tests/test_run_events.py` 覆盖“`<runId>:4` 解析成功”“跨 run 游标非法”“负数/非数字/超界游标非法”“事件字段六项齐全”，运行 `.venv/bin/python -m unittest tests.test_run_events` 通过
- [ ] 4.2 在 `app/routers/jobs.py` 增加 `GET …/jobs/{jobId}/events`：`format=sse`（默认）返回 `text/event-stream`，帧为 `retry: 1000`、`id: <runId>:<seq>`、`event: run-event`、单行 JSON `data`，每 15 秒 `: keep-alive`，终态后发 `event: stream-end` 并关闭；`format=json` 返回同一组装逻辑的补发结果；鉴权、归属与游标校验全部在构造响应前完成，失败返回 JSON 400/401/404/503；用 `tests/test_jobs_routes.py` 断言匿名与 B 的订阅返回 JSON 401/404 且 `content-type` 不是 `text/event-stream`，非法游标返回 400 JSON
- [ ] 4.3 用 `curl -N` 与 `curl '…/events?format=json&after=4'` 手工验证同一任务的两种读取方式给出相同序号集合，确认 `Last-Event-ID` 优先于 `after`、心跳不产生 `id`、终态后连接自动结束，并把实测帧样例写入任务 8 清单

## 5. 前端日志处理与测试任务面板

- [ ] 5.1 新增 `web/src/workbench/job-log.ts`（`(runId, seq)` 去重、按 `seq` 排序、忽略其他 run、缺口检测、终态标记）与 `web/tests/job-log.test.mjs`，覆盖“补发 1–4 后实时又来 3、4、5 只显示一次”“切换任务丢弃旧 run”“缺号置 `gap`”“终态后不再追加”；运行 `cd web && npm test` 通过，且 `stage-links` 回归仍通过
- [ ] 5.2 新增 `web/src/workbench/job-stream.ts`：用同源 `EventSource` 订阅 `…/events?after=<lastSeq>`，把帧交给 `job-log` 的同一 reducer，`stream-end` 后 `close()`，`error` 时按 `CONNECTING`/`CLOSED` 区分“自动重连/手动重试”；运行 `cd web && npm run typecheck` 通过
- [ ] 5.3 新增 `web/src/views/workbench/JobPanel.vue` 并在 `web/src/i18n.ts` 增加中英文案：显示 `jobId`、`runId`、状态、`attempts`、已提交/总步数、副作用计数与事件日志，提供开始、取消、重新连接与“这是连通性自检，不产生推演结论”的说明；在 `WorkbenchView.vue` 挂载到共享上下文栏与图谱布局工具条入口，开始/取消时只更新面板状态、不改动路由；运行 `cd web && npm run typecheck && npm run build` 通过
- [ ] 5.4 实现面板五种状态提示（SSE 中断 / worker 中断 / 任务失败 / 任务取消 / 服务不可用）与可观察时限文案，确保连接中断不显示失败、worker 中断不显示取消、失败与取消不显示成功；在浏览器逐项触发并核对，把实际提示与时序写入任务 8 的待签收清单（此任务不写 `docs/acceptance/`）

## 6. 自动化验收与 CHANGE 选择器

- [ ] 6.1 扩展 `scripts/verify.py`：新增 `--change` 参数与 `CHANGE` 环境变量，未注册值以退出码 2 结束并在报告中写入“未注册的变更”失败场景；报告中新增 `change` 与 `job_evidence`（`job_id`、`run_id`、`attempts`、`disconnected_at_seq`、`resumed_from`、`seqs`、`effects_total`、`steps_at_stop`）；运行 `python3 -m py_compile scripts/verify.py` 与 `CHANGE=not-a-change python3 scripts/verify.py`，确认退出码 2 且报告含失败场景
- [ ] 6.2 在默认 `make verify` 中加入任务底座基础场景：开始返回 202 与两个标识、状态可查询、确定性任务到 `succeeded` 且事件序号 1–10、匿名 401、B 访问 A 的任务与不存在项目同为 404、停库期间带 Cookie 返回 503 并保留 Cookie、只停 worker 时 `/health` 与 `/livez` 仍为 200；确认 Change 01–03 全部既有场景仍在报告中按原顺序通过
- [ ] 6.3 在 `CHANGE=2026-11-21-track-background-jobs` 中加入变更专属场景：停 worker 后用两个并发请求验证只有一个活动任务、真实 SSE 读到 `seq=4` 断开并以 `after=4` 补发到 `stream-end`、运行中停止 worker 后恢复并核对 `/effects` 为 10 且 `steps=[1..10]`、运行中取消、`failAtStep=3` 受控失败、终态后重新开始得到新 `jobId`/`runId`；脚本在 `compose up` 前设置 `TEST_TASK_STEP_DELAY_MS=800`，场景自清理（取消遗留任务、恢复 worker 与数据库）
- [ ] 6.4 连续两次执行 `make verify` 与两次 `make verify CHANGE=2026-11-21-track-background-jobs`，确认四次退出码均为 0、每次报告 `tested_sha` 等于 `HEAD`、`dirty=false`，且变更专属报告包含 `job_evidence` 与断线/中断数值；确认报告未声称验证了页面提示、布局或语言

## 7. 文档与工程规则

- [ ] 7.1 更新 `README.md`：新增接口表、状态转换表、事件字段与 SSE 契约（`id`、游标、`stream-end`、心跳不计数）、`CHANGE` 用法、worker 与 `make up/down` 的关系、任务相关人工走查步骤与可观察时限；逐条复制文档中的命令与 `curl` 示例实际执行，确认输出与文档一致
- [ ] 7.2 更新 `AGENTS.md`：新增“后台任务与事件流”规则——PostgreSQL 是唯一队列事实来源、`app` 不执行长任务、事件先落库再推送、`seq` 与 `(runId, seq)` 唯一、SSE 先鉴权后发送字节、worker 故障不改变 `/health`/`/livez`、任务接口必须复用 `resolve_owned_project`、`make verify CHANGE=<id>` 的断言边界；逐条与本次 delta 规范对照，确认没有弱化既有要求

## 8. 集成验收与人工签收准备

- [ ] 8.1 提交后确认 `git status --porcelain` 为空，连续执行两次 `make verify` 与两次 `make verify CHANGE=2026-11-21-track-background-jobs`，核对报告 `tested_sha`、`dirty`、`job_evidence` 与两次运行一致（序号集合、副作用计数相同），并把报告路径写入待签收清单
- [ ] 8.2 按 README 走查步骤在浏览器完成：开始任务后 `seq=4` 断网/断开再重连、`docker compose stop worker` 观察“等待接手”并在恢复后继续、受控失败与运行中取消的提示、切换阶段/布局/语言后任务面板仍在同一项目与阶段；保存截图与待签收清单，签收后只在 `docs/acceptance/` 写入记录（含 `tested_sha`、`jobId`、`runId`、实际序号、`stepsAtStop`、证据位置），`make verify` 与 CI 不触碰该目录
