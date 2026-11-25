# Design

## Context

本变更建立在 Change 01–08 已合并的实现上，设计只引用与第三阶段直接相关的现状：

- `web/src/views/DualPlatformRunPanel.vue` 目前只读取准备详情里的 `frozenSelection`，渲染问题、轮数、角色数与平台映射，并显示 `runPendingTitle`（“准备已确认，运行功能待接入”）。除准备读取外没有任何运行接口，也没有轮次、时钟或行动流。
- `web/src/environment/state.ts` 的 `EnvironmentFrozenSelection` 已定义 `selectionId`、`preparationId`、`projectId`、`buildId`、`snapshotId`、`question`、`settingsRevision`、`settings`、`roles`、`platforms`、`initialTopics`、`activation`；`app/services/environment.py` 的 `load_frozen_selection()` 返回 `environment_frozen_selections.payload`，确认时由 `_validated_publish_payload()` 生成并写入。第三阶段的输入不需要再从 build 或准备表聚合。
- 角色模拟设定字段已固定为 `motive`、`stance`、`activeHours`、`postingFrequency`、`responseDelayMinutes`、`influenceWeight`、`platformKeys`、`topics`、`orderIndex`、`firstSpeaker`、`firstTopic`（`app/engines/environment_contracts.py`）。轮次激活与发言分配只能使用这些冻结字段。
- `app/services/worker.py` 已有可复用的领取语句（`CLAIM_SQL`，同时接手 `queued` 与租约过期的 `running`）、租约验证（锁定任务行后用 `clock_timestamp()`）、`_heartbeat_scope()`、`_commit_graph_step()` 式步骤事务、`run_events.append_event()` 与 `LeaseLost`。`app/worker.py` 已按 `claim.kind` 分派 `evidence.graph.build`、`agent.environment.prepare`、`document.ingest` 与测试任务。
- `app/routers/jobs.py` 已提供项目内任务 SSE 与 `format=json` 补发，游标语义是“已完整处理的最后一个连续 `seq`”，前端已有 `web/src/workbench/job-stream.ts`、`job-log.ts`、`job-panel-state.ts`。运行事件的传输不需要新协议。
- `app/engines/simulation.py` 已有 Change 06 的探针实现：`PLATFORM_CONFIG` 固定 `platform_a→twitter`、`platform_b→reddit`，`run_platform()` 在临时目录创建 SQLite、执行 `create_post`/`create_comment`，再由 `_default_readback()` 用 `sqlite3` 从 `post`/`comment`/`user` 表读回记录并 `_validate_record()` 校验。该探针继续保留，但它的“一次性动作 + 临时目录 + 立即清理”不适合多轮运行。
- `app/requirements-engines.txt` 与 `Dockerfile.engine` 把 `camel-oasis`/`camel-ai`/`zep-cloud`/`pydantic 2.12.0` 固定在隔离引擎镜像；`app/requirements.txt` 与 `app/requirements-worker-graph.txt` 不含引擎包。这一约束决定第三阶段的引擎进程不能直接放进默认 worker 镜像。
- 迁移只前向执行，`runs.kind` 与 `jobs` 的状态约束都在既有迁移里扩展过；新增 run 类型必须继续以前向迁移扩展，并保持旧数据可启动。
- `scripts/verify.py` 已有 `KNOWN_CHANGES` 注册表、`environment_evidence`/`graph_evidence` 的写法、隔离 Compose 项目与 `ALLOW_DIRTY` 规则；`docs/acceptance/` 只写人工记录。

## Goals / Non-Goals

**Goals:**

- 冻结选择是唯一输入：开始运行、轮次推进、角色激活、话题与轮数都只读已确认的 `frozenSelection`，并在开始时冻结副本。
- 动作是引擎事实：每条业务动作都能追到某个平台的引擎记录；动作调用返回值、计划动作、前端累加都不构成计数。
- 可停止、可接手、可回看：停止在轮次边界生效，终态后计数冻结；worker 中断先读回补记再继续；同一选择可重跑并保留旧运行。
- 引擎隔离：两个平台各自的进程、目录与数据库；离线替身与真实本地 OASIS 是同一契约的两个实现，证据模式显式标注。
- 默认零真实外呼：默认启动、默认验收与 CI 只使用替身引擎；真实 OASIS 运行只由开发者显式执行。
- 页面只展示真实数据：时钟、行动流、统计与日志都来自运行接口与事件流，刷新、重连与接手后与持久记录一致。

**Non-Goals:**

- 不实现报告生成（第四阶段）、深度互动（第五阶段）、时序记忆写回（Change 10）。
- 不接入真实外网 Twitter/Reddit，不保存平台账号凭据，不做跨项目复用运行结果。
- 不让 `app` 进程执行引擎，也不在浏览器里跑模型或推演。
- 不引入第二套事件流、第二套队列或定时器驱动的“进度”。
- 不改变 Change 06 探针的动作、判定与理由码，不把运行结果写进连通性报告。
- 不为了真实 OASIS 降级 `app`/`worker-graph` 依赖，也不默认构建引擎镜像。

## Decisions

### 1. 开始运行只读冻结选择，并复制一份“本次运行实际使用”的载荷

**决定**：新增 `POST /api/projects/{projectId}/simulations`，请求体只接受 `{"selectionId": "<uuid>"}`。服务端顺序为：

1. `resolve_owned_project()` 完成身份与项目归属；
2. 按 `selectionId` 与 `projectId` 读取 `environment_frozen_selections`（复合条件，含他人选择）；
3. 校验载荷完整性：`question` 非空、轮数在 1–1000、角色非空且含 `roleId`/`displayName`/`simulation` 合法、`platforms` 恰好是 `platform_a/twitter` 与 `platform_b/reddit`、`initialTopics` 至少含一个问题话题、`activation.order` 覆盖全部角色、首发角色是顺序前缀且带首发话题；
4. 在同一事务内插入 `simulations`（含 `selection_payload` 副本）、`runs`（`kind=simulation.run`、`stage=dual-platform-run`、`total_steps=4`）、`jobs`（`operation_key=dual-platform-run:v1:<selectionId>`）与两条 `simulation_platforms`。

运行记录保存 `buildId`、`snapshotId`、`preparationId`、`question`、`rounds`、`minutesPerRound`、`durationHours`、`selection_payload`（本次实际读取的 JSON）与 `engine_mode`。运行开始后，准备、图谱、平台映射或轮数的后续变化都不影响该运行。

**理由**：Change 08 已经把“用户确认过什么”固化成不可变 JSON。运行若再读当前 build 或准备表，就会出现“确认后配置又变了”的问题；复制载荷还让旧运行在角色或准备被后续变更覆盖后仍可解释。

**备选方案**：

- 只传 `preparationId`，运行时再读准备：否决，准备可能被重跑或被新 build 影响。
- 允许请求体覆盖轮数或平台：否决，会绕开确认冻结，破坏“Change 09 只读冻结选择”的约定。
- 让前端传角色与话题：否决，服务端无法信任，且无法解释旧运行。

### 2. runId 语义：活动运行复用、终态后新运行、旧运行永久可读

**决定**：

| 场景 | 行为 | runId |
| --- | --- | --- |
| 首次开始 | 202 | 新建 |
| 运行中重复点击开始 | 200，`deduplicated=true` | 与首次相同 |
| 运行中并发开始 | 一个 202、一个 200 | 同一 runId |
| 终态（`succeeded`/`failed`/`canceled`）后再开始 | 202 | 新 runId |
| 查看旧运行 | 200 | 地址中的 `run` |
| 未指定运行读取 | 200 | 活动运行优先，否则最近运行 |

去重靠 `jobs_active_operation_idx`（`project_id, operation_key` 在 `queued|running` 上唯一），并捕获唯一冲突后重读该活动 job，返回 200。终态后同一操作键允许再次插入，因此重跑自然得到新 run。列表按 `created_at DESC`，详情永远按 `runId` 读取，不复用活动运行的缓存。

**理由**：重复点击不能制造第二套轮次或动作；“停止后重跑”是用户最自然的复现方式，但也必须能对照旧运行。把去重放在数据库唯一索引上，而不是“先查再插”，才能抗并发。

**备选方案**：

- 每次开始都新建运行：否决，双击会产生两条并行运行，浪费引擎预算且让页面抖动。
- 同一选择只允许一个运行、终态后重建覆盖旧记录：否决，旧运行的证据会被销毁。
- 用前端按钮禁用去重：否决，刷新或多标签页即可绕过。

### 3. 两个平台的动作集合按原生平台量词表达，动作内容来自冻结角色

**决定**：动作集合固定为：

| 平台别名 | 原生平台 | 允许动作 | 界面词 |
| --- | --- | --- | --- |
| `platform_a` | `twitter` | `create_post` | 发帖 |
| `platform_a` | `twitter` | `repost` | 转发（目标：同平台原帖） |
| `platform_a` | `twitter` | `like_post` | 点赞（目标：同平台帖子） |
| `platform_b` | `reddit` | `create_post` | 发主题 |
| `platform_b` | `reddit` | `create_comment` | 评论（目标：同平台主题帖或评论） |
| `platform_b` | `reddit` | `like_post` | 投票赞成 |
| `platform_b` | `reddit` | `dislike_post` | 投票反对 |

“投票”不是新动作类型：OASIS `reddit` 平台的赞成/反对就是 `like_post`/`dislike_post`，界面显示为“投票赞成/投票反对”，报告与数据仍保留原生动作名，避免把界面词当协议。`create_post`/`create_comment` 不再被当作两个平台唯一允许的动作；Change 06 的探针语义不受影响。

轮次激活规则（确定性，替身与真实引擎共用输入）：

- 模拟起点为运行开始时刻的推演时钟 `T0`，第 `r` 轮（1 起）的模拟时刻为 `T0 + (r-1) * minutesPerRound`；模拟小时为 `(startHour + (r-1)*minutesPerRound // 60) % 24`，其中 `startHour` 取**首个首发角色最早活跃小时**，避免整段运行落在深夜而每轮都无候选角色；运行创建时把它落成 `(当天, startHour:00)` 写入 `runs.simulated_epoch` 与 `simulations.simulated_start`，供调度、动作时刻、事件与详情时钟共用，真实创建时间只留在 `created_at`；
- 某平台本轮候选角色 = `simulation.platformKeys` 含该平台、`activeHours` 含模拟小时的角色，按 `activation.orderIndex` 升序；
- 第 1 轮 MUST 包含全部首发角色，并用其 `firstTopic` 作为首条内容；首发角色即使不在 `activeHours` 也加入；
- 每轮每平台的动作数上限为 `SIMULATION_ACTIONS_PER_ROUND`（默认 6），单角色按 `postingFrequency`、`influenceWeight` 与 `responseDelayMinutes` 决定动作数与目标，内容由本次 `question`、角色 `motive`/`stance`/`topics` 与目标原帖组成；
- 动作内容 MUST 由服务端校验：非空、长度上限、含本次运行标记（供读回核对），且不得包含材料正文或来源地址。

**理由**：真实 OASIS 的动作集合是 `twitter`/`reddit` 的原生集合，界面词只是展示层；若为“投票”另造动作名，读回时无法与引擎记录对应。轮次激活只使用冻结字段，能保证同一冻结选择重跑得到同构的行为（替身引擎同一输入得到同一结果）。

**备选方案**：

- 每轮让所有角色都行动：否决，76 个角色会产生大量重复动作，且与活跃时段语义冲突。
- 直接用界面词作为动作类型：否决，引擎记录对不上，读回校验会失效。
- 允许模型决定动作集合：否决，无法保证每个平台都产生可读回的原生动作。

### 4. 引擎由独立 worker 驱动，每平台一个子进程，引擎数据库持久到终态

**决定**：运行由独立 `worker` 领取执行，`app` 与浏览器不调用引擎。worker 对每个平台启动一个子进程，通过行协议逐轮推进：

```
worker ──write→ <data>/<runId>/<platform>/request.json ──read→ child
child  ──append→ <data>/<runId>/<platform>/actions.jsonl
child  ──sqlite→ <data>/<runId>/<platform>/social.db  (post/comment/trace/sim_round)
child  ──stdout→ {"type":"round_done","round":3,"records":[…]}
```

- 子进程入口 `python -m app.engines.duet_runner --platform platform_a --runtime <dir> --marker <runId>`；它负责打开/创建引擎数据库、按请求执行一轮、把本轮标记写入 `sim_round` 表并输出“本轮已提交”的引擎记录摘要。
- worker 收到 `round_done` 后**不直接采信摘要**：它用独立的只读 SQLite 连接按运行标记与轮次读回动作行，核对原生动作、角色、目标与内容标记，只有独立读回成功的记录才进入业务事务。
- 子进程在整次运行内保持存活（避免每轮重建 OASIS 环境）；worker 崩溃或停止时子进程随进程组退出，接手的新 worker 用同一目录与数据库重新启动子进程，从已提交轮次之后继续。
- 引擎数据库与动作日志是“运行工作数据”，业务读取只使用 PostgreSQL；终态事务提交**之后**才清理该运行目录（失败、取消同样清理），清理失败只记录 `cleanup_code`，不改变终态、计数或已有动作。运行目录 MUST 位于跨 worker 重建仍存在的持久卷（`echosim-simulations:/app/data/simulations`），否则“引擎已写入、worker 尚未记账”的接手会读不到原生记录；镜像 MUST 预先创建该目录并归属运行用户。
- 每个平台目录用 `platform.lock` 做互斥：旧子进程未退出时，接手的新子进程等待锁，避免两个进程同时写同一 SQLite。
- 真实本地 OASIS 通过 `SIMULATION_ENGINE_COMMAND` 交给隔离引擎镜像执行（模板占位 `{runtime}`、`{request_name}`），worker 只负责租约、读回与记账；容器内默认路径 `/out` 由 `SIMULATION_ENGINE_RUNTIME` 指定。

**理由**：OASIS 的环境构造与 `reset()` 成本高，逐轮重启会显著放慢并改变行为；同时把引擎放进子进程可以保证引擎异常、SQLite 句柄与 OASIS 的全局状态不会污染 worker 的事件循环或另一个平台。worker 独立读回数据库，则把“调用返回值不算证据”落在实现上。

**备选方案**：

- 在 worker 进程内直接调用引擎：否决，OASIS 的全局状态、超时与异常会污染事件循环，且两个平台无法真正隔离。
- 每轮一个子进程：否决，真实环境重建成本高，且轮次间上下文会丢失。
- 由 `app` 进程执行：否决，违反“长任务只能由 worker 执行”。

### 5. 引擎模式：离线替身默认，真实 OASIS 显式、隔离、可验证

**决定**：`SIMULATION_ENGINE` 取值 `double`（默认）或 `oasis`：

- `double`：`app/engines/duet_double.py` 实现同一契约的确定性替身。它写真正的 SQLite 表（`post`、`comment`、`trace`、`sim_round`）与 `actions.jsonl`，读回路径与真实引擎完全相同；内容由运行标记、轮次、角色与冻结话题确定性生成，不访问网络。默认 `make up`、默认 `make verify`、CI 都使用它。
- `oasis`：`app/engines/duet_oasis.py` 复用 `camel-oasis` 的 `twitter`/`reddit` 平台，通过原生手动动作（`create_post`、`create_comment`、`like_post`、`dislike_post`、`repost`）产生记录，默认不调用外部模型（因此 `externalModelCalls=0`）；导入失败 MUST 以 `engine_unavailable` 结束，MUST NOT 回退到替身。它只能运行在安装了 `app/requirements-engines.txt` 的镜像中，或用 `SIMULATION_ENGINE_COMMAND` 交给隔离引擎镜像。

镜像与编排：

- `Dockerfile` 新增 `worker-simulation` 目标：以 `echosim-engine:dev` 为基础，复用其中已解析/安装/导入的 `camel-oasis` 组合，只额外安装 `app/requirements.txt` 并复制应用代码；MUST NOT 改变 `app`、`worker-graph` 现有目标或 `app/requirements.txt` 的版本。构建前需要先有引擎镜像（`make simulation-worker` 会在缺失时先构建引擎）。
- 为避免该镜像在导入期拉起 Zep/建图依赖，`app/services/worker.py` 中建图与模型 provider 的 import 改为函数内惰性导入，并新增 `app/simulation_worker.py` 作为只处理 `simulation.run` 的入口；`app/worker.py` 的行为与分派保持不变，现有测试必须验证默认入口仍能导入四类任务分支。
- `docker-compose.yml` 新增 `worker-simulation` 服务（`profiles: ["simulation"]`），命令为 `python -m app.simulation_worker`，`WORKER_RUN_KINDS=simulation.run`，`SIMULATION_ENGINE=oasis`，并默认保留引擎证据目录供核对；`make up` 默认不启用该 profile，只有 `make up SIMULATION=1` 时才构建并启动该服务、为 app 打开 `SIMULATION_ALLOW_OASIS`，并用 `make simulation-worker-check` 跑四阶段基线。
- 该镜像的 `camel-oasis`/`camel-ai`/`pydantic` 组合 MUST 单独完成“解析 → 安装 → 导入 → 最小运行”四阶段后才记为 `verified`；未完成前不得在 README 或报告中写成已验证。本轮实测路径是 `SIMULATION_ENGINE_COMMAND` + 既有 `echosim-engine:dev`（已含 `camel-oasis 0.2.6`），`worker-simulation` 镜像目标保留但标记为 `dependency=unverified`。

**理由**：仓库已明确禁止为引擎候选降级 `app`/`worker` 依赖，也禁止默认触发真实外呼。把真实引擎放进独立镜像与独立 worker，既保持默认零外呼，又让真实 OASIS 走与替身完全相同的轮次、动作、读回与记账路径，而不是另写一套“演示脚本”。

**备选方案**：

- 在默认 worker 镜像里装 `camel-oasis`：否决，会与 `worker-graph` 的 Pydantic 版本冲突，违反依赖约束。
- 真实 OASIS 只写成 `scripts/` 下的独立脚本、不经过 worker：否决，产品路径只会跑替身，真实动作与产品记录无法对齐。
- 缺少依赖时静默回退替身：否决，会把离线结果冒充真实证据。

**风险与缓解**：`camel-oasis` 在 aarch64/Linux 上的构建与四阶段验证可能不止一个迭代。若四阶段未通过，第三阶段仍可交付：`double` 是默认且完整的实现，`oasis` 以 `engine_unavailable` 明确失败，报告只记录“未验证”，不记录为通过。

### 6. 轮次游标、步骤事务与“引擎已执行未记账”恢复

**决定**：`runs` 固定四步：

| 步 | step_key | 事务内容 |
| --- | --- | --- |
| 1 | `bind` | 锁定 job、校验租约、写 `run_steps(1)`、事件、`next_step=1` |
| 2 | `environment` | 创建两个平台目录与 `simulation_platforms`（`pending`），写 `run_steps(2)`、事件、`next_step=2` |
| 3 | `advance` | 逐轮逐平台：读回引擎记录 → 写 `simulation_actions` + `simulation_rounds` + `simulation_platforms.rounds_completed/action_count/by_action` + 事件；全部轮次完成时写 `run_steps(3)`、`next_step=3` |
| 4 | `publish` | 汇总终态计数、写 `run_steps(4)`、`succeeded` 终态事件、job 终态、清理引擎目录并记录清理结果 |

`advance` 内的轮次进度以 `simulation_rounds` 为唯一游标：`(simulation_id, platform_key, round_index)` 主键 + `engine_write_outcome`；只有“本轮记录 + 本轮动作 + 平台计数 + 事件”同事务提交后才算该轮完成。`run_steps` 与 `jobs.next_step` 仍是步骤层权威，轮次记录不推进步号。

`engine_write_outcome` 取 `in_flight`、`committed`、`unconfirmed`，语义对齐 Change 07 的 episode 状态：

- 调用引擎前先原子预留整次运行的动作预算（`simulations.reserved_actions`）：`UPDATE ... WHERE 已记账 + 预留 + 本轮 <= 上限` 失败即 `simulation_too_large`，该轮不调用引擎；记账或失败后释放预留。
- worker 崩溃后接手：读取两个平台的引擎 `sim_round` 与动作记录，比对数据库中已提交轮次：
  - 动作只有计划、基线之后没有任何本角色原生行 → **可证明未执行**，允许重发；
  - 原生行已存在且能按正文标记（发帖/评论）或原生目标（点赞/反对/转发）归属 → **已执行可补记**，以 `committed` 补记（幂等，按 `(simulation_id, platform_key, engine_record_id)` 唯一约束）；
  - 基线之后存在本角色原生行但无法归属，或记录/目录不可读 → **不可确认**，该平台以 `action_unconfirmed` 结束，MUST NOT 重发。
- 点赞、反对、转发 MUST 按原生目标记录标识读回（`like.post_id`、`dislike.post_id`、`post.original_post_id`、`comment.post_id`），只有发帖与评论校验正文中的运行标记——OASIS 的点赞/转发本来就没有可写的正文。
- 读回的引擎记录若已存在，只校验不重复插入；已提交轮次不重跑、不重复追加事件。

**理由**：`run_steps` 的步数上限是 100，而轮数可达 1000，轮次不能成为步骤；把轮次游标放在独立表里，才能既保持步骤事务语义，又让接手精确到“第几轮第几平台”。`in_flight/committed/unconfirmed` 三态直接复用 Change 07 已验证的“先确认再重试”纪律。

**备选方案**：

- 把每轮做成一个 step：否决，受 100 步上限限制，且会让步骤语义与业务轮次混淆。
- 接手时直接重跑未记账轮次：否决，会与已写入引擎的动作重复，且真实 OASIS 无法回滚。
- 用引擎目录里的时间戳猜轮次：否决，时间戳不是提交事实，重启时钟即可伪造。

### 7. 停止在轮次边界生效，终态一次性冻结

**决定**：`POST /api/projects/{projectId}/simulations/{runId}/stop`：

- 未终态：在取消事务内写 `jobs.status='canceled'`、`terminal_reason='canceled'`、`simulations.phase='canceled'`、`finished_at`、一条 `canceled` 终态事件（与 job 取消同一事务，复用 `jobs.cancel_job()` 的锁与状态检查，并调用 `simulation_store.cancel_for_run()`）；返回 200 与 `{runId, jobId, phase, cancelApplied:true}`。
- 已终态：返回 200、`cancelApplied:false`，不写第二个终态事件，不改计数。
- worker 在**每一轮开始前**与**每轮引擎返回后**检查 `cancel_requested_at`：若已请求，先把本轮已写入引擎的动作读回并记账，再提交终态；不再开始下一轮。
- 终态提交后，`_book_round()` 与事件追加都必须验证“运行未终态 + 租约有效”，否则影响 0 行；迟到的子进程结果只记录到 worker 日志并被丢弃。
- 未正常完成的轮次（`action_unconfirmed`、`simulation_timeout`）保留轮次记录与已读回动作，但 `engine_write_outcome='unconfirmed'` 且不推进 `rounds_completed`，避免把失败轮次显示成已完成。
- 两个平台独立：`_advance()` 用两个并发任务推进平台，慢平台不阻塞另一平台；停止请求在每个轮次边界检查，两个平台各自完成当前轮后再写终态。
- 停止是请求而不是终态：API 只写 `jobs.cancel_requested_at`，worker 在边界读回在途动作、记账，再写 `canceled` 终态；重复请求不再产生终态事件。
- 终态由 `simulations` 行锁 + 状态检查的单事务守卫裁决：完成与取消并发时只有一个赢家，终态事件恰好一条；顺序以先提交者为准。

**理由**：“停止”不能丢弃已经发生的动作（否则计数与引擎不一致），也不能让正在跑的轮次继续很久；轮次边界是最小可解释的切点。把终态写入放在一个事务里，才能保证“终态后不增长”。

**备选方案**：

- 立即杀死引擎进程并丢弃本轮：否决，引擎已写入的动作会与计数不一致。
- 停止后允许在途轮次继续提交：否决，终态后计数会增长。
- 用前端停止按钮隐藏新动作：否决，持久记录仍在增长。

### 8. 单平台失败不阻断另一平台，运行标记为部分结果

**决定**：平台级失败原因码复用 Change 06 的形状并扩展：`platform_a_setup`、`platform_a_action`、`platform_a_record`、`platform_a_cleanup`、`platform_b_setup`、`platform_b_action`、`platform_b_record`、`platform_b_cleanup`、`action_unconfirmed`、`engine_unavailable`、`simulation_timeout`。

一个平台失败时：

- 该平台 `status='failed'`，记录原因码与已完成轮次，不再推进；
- 另一平台继续执行剩余轮次；
- 运行在 `publish` 步以 `failed` 结束，`failed_platforms` 列出失败平台，`failure_code` 记录首个失败原因；
- 已提交的动作、轮次、事件与计数全部保留，读取接口返回 `partial=true`；
- 页面为失败平台显示原因码与“已完成 x/y 轮”，为成功平台继续显示行动流与统计。

两个平台都失败时同样以 `failed` 结束并列出两个平台。`action_unconfirmed` 与 `engine_unavailable` 也按此规则处理。

**理由**：只跑通一个平台的结果仍有比较价值；直接整体失败会销毁另一平台已经写出的证据，也无法解释“哪个平台坏了”。这与 Change 06 的“平台分别判定”一致，只是产品运行把它延伸到部分结果。

**备选方案**：

- 任一平台失败即取消全部：否决，会丢弃可读证据并把原因混成“运行失败”。
- 平台失败后自动重跑该平台：否决，可能与已写入动作重复。
- 把部分结果标为 `succeeded`：否决，会误导用户认为双平台都完成了。

### 9. 计数只从持久动作派生，读取时聚合

**决定**：`simulation_platforms.action_count` 与 `by_action` 是同一事务内的冗余计数，用于快速渲染与事件载荷；运行详情与动作接口 MUST 以 `simulation_actions` 聚合（`COUNT(*) GROUP BY native_action`）为权威，并在服务层断言冗余计数与聚合值一致，不一致时以聚合值返回并记录 `count_mismatch` 日志（不含正文）。

`GET /api/projects/{id}/simulations/{runId}` 返回：

```json
{
  "runId": "...", "jobId": "...", "selectionId": "...",
  "preparationId": "...", "buildId": "...", "snapshotId": "...",
  "question": "...", "engine": "double", "phase": "running",
  "terminal": false, "failedPlatforms": [], "partial": false,
  "rounds": 3, "minutesPerRound": 60,
  "simulatedAt": "2026-11-24T02:00:00Z", "simulatedStart": "...",
  "platforms": [
    {"platformKey":"platform_a","nativePlatform":"twitter","status":"running",
     "roundsCompleted":2,"roundsTotal":3,"actionCount":5,
     "byAction":{"create_post":2,"repost":1,"like_post":2},
     "failure":null}
  ],
  "counts":{"total":8,"byAction":{"create_post":3,"create_comment":2,"like_post":3}},
  "actions":{"items":[…],"nextAfter":8},
  "canStop": true
}
```

动作接口 `GET .../actions?platform=&after=&limit=` 使用 `action_seq` 升序游标（每运行唯一、连续），返回 `{items, nextAfter, platformCounts}`；`after` 必须 `0..当前最大 action_seq`，超出返回 400。

**理由**：前端累加、事件条数与计划数都可能在断线或接手时漂移；聚合查询让“刷新、重连、接手”天然一致。冗余计数只用于事件载荷与首屏，且被断言，不会成为第二个事实来源。

**备选方案**：

- 只存冗余计数：否决，一旦更新遗漏就永久不一致。
- 每次读取都重算全部动作不返回分页：否决，动作多时页面首屏过重。
- 让事件条数当计数：否决，事件会因重连或终态事件而不同。

### 10. 目标原帖在同运行、同平台内解析

**决定**：`simulation_actions` 保存 `target_engine_record_id`、`target_action_id`、`target_platform_key`、`target_native_action`。落库前校验：

- 目标平台必须等于动作自身平台（`repost`/`like_post`/`create_comment` 不跨平台）；
- 目标引擎记录必须存在于本运行该平台的已读回记录中；
- `target_action_id` 在事务内解析为已提交动作；解析失败时保留 `target_engine_record_id` 并把 `target_action_id` 置空、`target_resolved=false`。

读取接口只返回已解析目标的摘要（内容、角色、轮次、模拟时刻），未解析时返回 `targetResolved=false` 与原因码 `target_unresolved`，页面显示“目标未在本运行中定位”，不伪造原帖。跨运行或跨平台的目标绝不解析，即使引擎里存在同 id 记录。

**理由**：引擎的帖子 id 只在单平台单库内唯一，跨运行复用同一固定图谱式的“旧记录共存”不适用于运行；只有按运行标记绑定，才能保证“回到原帖”不是回到旧运行的帖。

**备选方案**：

- 只存引擎 id，读取时按平台库查内容：否决，运行目录终态被清理后无法读取。
- 允许跨运行解析：否决，第二次运行会链接到第一次的原帖，证据失真。
- 目标解析失败时丢弃该动作：否决，动作确实发生了，丢弃会破坏计数一致。

### 11. 事件载荷只带标识与计数，页面以运行详情为准

**决定**：事件复用 `run_events`，`type` 取 `progress|failed|canceled|completed`，载荷：

```json
{"simulationId":"…","selectionId":"…","buildId":"…","snapshotId":"…",
 "roundIndex":3,"totalRounds":3,"platformKey":"platform_a",
 "nativePlatform":"twitter","roundsCompleted":3,"actionCount":5,
 "nativeActionCounts":{"create_post":2,"repost":1,"like_post":2},
 "reason":null}
```

事件与轮次记录、动作、计数在同一事务提交；终态事件带最终计数与原因码。事件不携带动作正文、角色画像、材料正文、引擎路径或凭据。页面收到事件后**重新读取运行详情**取正文，事件只用于唤醒、时钟推进与日志行。

前端状态模块 `web/src/simulation/state.ts`：

- `runId` + `selectionId` 守卫：不匹配的详情或事件一律丢弃（含切换旧运行后的迟到响应）；
- 计数只接受详情返回值，事件里的累计计数只用于日志与一致性断言；
- 终态后关闭事件流、禁用停止、冻结时钟；
- `stopRequested` 在收到 200 后置位，终态后清除并显示“已停止”；
- 时钟与行动流顺序由 `simulatedAt` 与 `action_seq` 决定，不依赖到达顺序。

**理由**：Change 07/08 已经证明“先详情、后事件”能覆盖刷新、重连与接手；运行只是在这套语义上增加时钟与平台维度。让事件携带正文会扩大日志与报告泄露面，也会让页面在断线时缺内容。

### 12. 数据模型与前向迁移

**决定**：新增 `app/migrations/0013_dual_platform_runs.sql`：

- `runs_kind_check` 扩展为包含 `simulation.run`；
- `environment_frozen_selections` 增加 `(project_id, selection_id)` 唯一约束，供复合外键使用；
- `simulations`：`id`、`project_id`、`selection_id`、`preparation_id`、`build_id`、`snapshot_id`、`question`、`rounds`、`minutes_per_round`、`duration_hours`、`engine_mode`（`double|oasis`）、`selection_payload jsonb`、`run_id`（唯一，`runs.id`）、`phase`（`queued|running|succeeded|failed|canceled`）、`failure_code`、`failed_platforms text[]`、`simulated_start`、`provider_calls_real`、`provider_calls_double`、时间戳；复合外键 `(project_id, selection_id)`、`(project_id, build_id)`、`(build_id, snapshot_id)`、`(preparation_id, project_id)`。
- `simulation_platforms`：主键 `(simulation_id, platform_key)`，`native_platform` 由检查约束绑定映射，`status`、`rounds_completed`、`action_count`、`by_action jsonb`、`failure_code`、`engine_rel_dir`、时间戳。
- `simulation_rounds`：主键 `(simulation_id, platform_key, round_index)`，`simulated_at`、`action_count`、`engine_write_outcome`（`in_flight|committed|unconfirmed`）、`committed_at`，复合外键指向 `simulation_platforms`。
- `simulation_actions`：`id`、`simulation_id`、`platform_key`、`round_index`、`action_seq`、`native_action`（按平台白名单检查）、`actor_role_id`、`actor_display_name`、`content`、`engine_record_id`、`engine_sequence`、`target_*` 四列、`target_resolved`、`simulated_at`、`created_at`；唯一 `(simulation_id, platform_key, engine_record_id)`、唯一 `(simulation_id, action_seq)`，复合外键指向轮次与角色。
- 索引：`simulations(project_id, created_at DESC)`、`simulation_actions(simulation_id, platform_key, action_seq)`、`simulation_rounds(simulation_id, platform_key, round_index DESC)`。

迁移 MUST 在空数据卷与已有 Change 01–08 数据卷上各启动一次，旧表数据不变，重复启动只执行一次。约束测试覆盖：非法 `kind`、非法平台映射、`round_index < 1`、重复 `engine_record_id`、非法 `native_action`、非法 `engine_mode`、跨项目选择外键。

### 13. 接口契约

| 方法 | 路径 | 语义 | 状态码 |
| --- | --- | --- | --- |
| POST | `/api/projects/{p}/simulations` | 从 `selectionId` 开始 | 202 新建、200 复用、409 `selection_missing`/`selection_invalid`、404 `推演选择不存在`、422 `selection_invalid` |
| GET | `/api/projects/{p}/simulations?selection=` | 列表（按选择过滤可选） | 200 |
| GET | `/api/projects/{p}/simulations/{runId}` | 运行详情 | 200、404 `运行不存在` |
| GET | `/api/projects/{p}/simulations/{runId}/actions` | 动作分页 | 200、400 游标越界 |
| POST | `/api/projects/{p}/simulations/{runId}/stop` | 停止 | 200（含已终态） |

所有接口先 `require_user`、再 `resolve_owned_project`、最后资源归属；匿名立即 401 且不访问数据库；他人项目/不存在项目 404 `项目不存在`；数据库不可达 503 且不清除 Cookie。事件订阅复用 `GET /api/projects/{p}/jobs/{jobId}/events`，运行详情返回 `jobId`。

### 14. 配置与依赖

新增配置（`.env.example`、`docker-compose.yml`、`app/config.py`、README 同步）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SIMULATION_ENGINE` | `double` | `double|oasis`，非法值按 `double`（默认安全）并在启动日志告警 |
| `SIMULATION_DATA_DIR` | `/app/data/simulations` | 运行工作目录根 |
| `SIMULATION_ACTIONS_PER_ROUND` | `6` | 每平台每轮动作上限（1–50） |
| `SIMULATION_ROUND_TIMEOUT_SECONDS` | `60` | 单平台单轮预算 |
| `SIMULATION_ROUND_DELAY_MS` | `0` | 验收用于放大停止窗口 |
| `SIMULATION_MAX_ACTIONS` | `5000` | 单运行动作上限，超出 `simulation_too_large` |
| `SIMULATION_FAULT` | 空 | 仅验收：`platform_a:1:after_first_action` 等故障注入 |
| `DUET_CHILD_SLEEP_MS` | `0` | 仅验收：放大“已写入、未记账”窗口 |
| `SIMULATION_KEEP_RUNTIME` | `false` | 仅验收：保留运行目录以核对引擎记录 |
| `SIMULATION_ENGINE_COMMAND` | 空 | 真实本地 OASIS 的隔离执行命令（支持 `{runtime}`/`{request_name}`） |
| `SIMULATION_ENGINE_RUNTIME` | `/out` | 引擎命令看到的运行目录 |
| `SIMULATION_PLATFORM_DELAY_MS` | 空 | 仅验收：`platform_a:700` 让某平台变慢，验证独立推进 |
| `SIMULATION_CLEANUP_FAIL` | 空 | 仅验收：注入清理失败，验证终态不被改写 |
| `WORKER_RUN_KINDS` | 全部五类 | worker 可领取的 run 类型（逗号分隔） |

`WORKER_RUN_KINDS` 加入 `CLAIM_SQL` 的 `kind = ANY(...)` 过滤，未声明的 job 保持 `queued`。`worker-simulation` 镜像的依赖组合单独记录 baseline；`make up` 默认不构建它。

### 15. 验收与证据

`scripts/verify.py` 注册 `SIMULATION_CHANGE_ID = "2026-11-24-run-dual-platform-simulation"`，`runtime_env` 设置 `SIMULATION_ENGINE=double`、`SIMULATION_KEEP_RUNTIME` 与 `SIMULATION_ROUND_DELAY_MS`（替身是默认引擎，不需要额外允许开关，但报告必须记录 `engine=double`）。默认 `make verify` 增加“无冻结选择不触发运行”场景并写 `simulation_evidence = {mode:"not-triggered", calls:{real:0,double:0}, callScope:[]}`。

Change 09 场景顺序：

1. 上传原创材料 → 建图（stub）→ 准备出 6 个角色（样例值，断言取实际冻结角色数）→ 确认冻结 3 轮 → `selectionId`；
2. 开始运行 → 202 → 等待 `bind`/`environment`/`advance`/`publish`；
3. 断言两平台各 3 轮、动作数 > 0、按类型分解、引擎记录存在且与数据库记录一致；
4. 目标原帖：`create_comment` → 主题帖、`repost` → 原帖，三方计数一致；
5. 停止场景：新运行 + `SIMULATION_ROUND_DELAY_MS` 放大窗口 → 停止 → 两次读取计数不变 → 同选择重跑 202 新 `runId` → 旧运行仍可读；
6. 接手：停止 worker 容器 → 恢复 → `attempts>1` → 计数与引擎一致；
7. 单平台失败：注入 `platform_a_action`（通过替身 `SIMULATION_FAULT=platform_a_action` 或独立探针），断言 B 完成、运行 `failed`、`failedPlatforms=["platform_a"]`；
8. 边界：匿名 401、他人 404、停库 503 保留 Cookie、缺选择 409、非法选择 422；
9. 未知 `CHANGE` 退出码 2、连续两次执行通过、`tested_sha == HEAD`、`calls.real == 0`。

`simulation_evidence` 结构：

```json
{"mode":"offline-double","engine":"double","calls":{"real":0,"double":12},
 "callScope":["<runId-1>","<runId-2>"],
 "runs":[{"runId":"…","selectionId":"…","rounds":3,
          "platforms":[{"platformKey":"platform_a","nativePlatform":"twitter",
                        "roundsCompleted":3,"actionCount":3,
                        "byAction":{"create_post":1,"repost":1,"like_post":1},
                        "engineRecords":["post:1","post:2"],"failure":null}],
          "targetLinks":[{"actionId":"…","platformKey":"platform_b",
                          "nativeAction":"create_comment","targetId":"…"}],
          "stop":{"requestedAt":"…","terminalAt":"…","countsFrozen":true},
          "takeover":{"attemptsBefore":1,"attemptsAfter":2,"booked":1},
          "failedPlatforms":[]}],
 "workerRestored":{"env":"default","ok":true}}
```

真实本地 OASIS 证据由独立命令（沿用 `ALLOW_DIRTY` 约定）产出：`make simulate-live` → `scripts/simulate_live.py` → 登录既有栈、用原创材料完成建图/准备/冻结，然后启动一个只领取 `simulation.run` 的本机 worker，通过 `SIMULATION_ENGINE_COMMAND` 把每轮交给隔离引擎镜像（本轮使用既有 `echosim-engine:dev`），最后读回两个引擎数据库并写 `runtime/simulation-live/report.json`（`mode=live`、`engine=oasis`、`localOasisActions>0`、`externalModelCalls=0`、`dirty`、`tested_sha`）。该路径与产品运行共用同一 API、同一 worker、同一事件流与同一 runId，但证据文件独立；MUST NOT 由默认验收、CI 或 `make up` 触发。

### 16. 三个核心验收场景（操作手册第 23–24 页）

| # | 操作 | 可观察结果 | 证据来源 |
| --- | --- | --- | --- |
| ① | 用原创材料建图并准备角色，确认冻结 3 轮后开始运行；用初始冻结的角色数（验收样例为 6 个，正式运行使用冻结值） | 运行完成或进入终态；`platform_a/twitter` 与 `platform_b/reddit` 各有已提交轮次与至少一条从引擎记录读回的原生动作；页面显示时钟、两条行动流与按平台统计；页面标注引擎模式 | `simulation_evidence.runs[].platforms[]` 的 `engineRecords`/`byAction`；运行详情；`runtime/simulation-live/report.json`（真实 OASIS，作者显式执行）；`docs/acceptance/` 截图与文字记录 |
| ② | 从一条评论或转发点回原帖；逐平台核对引擎记录、服务端计数与页面计数 | 目标原帖属于同一运行、同一平台；三个来源的动作数与按类型分解一致；跨运行/跨平台目标不链接 | 动作详情与 `targetLinks`；引擎 SQLite 读回；运行详情 `counts`；浏览器走查记录 |
| ③ | 运行中停止；观察轮次与动作数稳定；用同一冻结选择重跑；再打开旧运行 | 停止在下一轮边界生效，终态后两次读取计数不变；重跑返回新 `runId`；旧运行的终态、计数与行动流仍可读 | `simulation_evidence.runs[].stop` 与 `takeover`；两次运行详情的 `runId` 对比；浏览器走查记录 |

区分离线与真实：①②③ 在默认验收中由 `engine=double` 完成，报告写 `mode=offline-double`；作者在隔离引擎镜像中显式执行同一选择时写 `mode=live`、`engine=oasis` 与 `calls.real>0`。两类报告使用不同文件名与不同证据字段，任何一方都不能替代另一方。

人工验收记录只写 `docs/acceptance/`，包含 `tested_sha`、日期、验收人、`runId`、`selectionId`、两平台动作数与目标链接、停止与重跑结果、离线和真实证据路径；`make verify` 与 CI 不创建、不修改、不删除该目录。

## Risks / Trade-offs

- **真实 OASIS 依赖组合可能无法在 aarch64 上完成四阶段验证** → 默认 `double` 仍是完整实现；`oasis` 缺失时以 `engine_unavailable` 明确失败，报告只写“未验证”，不写“通过”。
- **每平台常驻子进程在 worker 崩溃后可能残留** → worker 与子进程同进程组，`preexec`/`start_new_session` + 终态清理；接手前先读引擎 `sim_round` 判断，重复进程不影响已提交轮次，清理失败记录 `platform_*_cleanup`。
- **替身引擎可能“看起来像真的”** → 运行记录、事件、页面与报告都强制标注 `engine`，验收断言 `mode=offline-double` 与 `calls.real=0`；真实证据只来自独立 `mode=live` 报告。
- **轮次粒度的事件会让长运行的 `seq` 很大** → 事件只在轮次提交时产生，`seq` 连续且 `(run_id, seq)` 唯一；SSE 与游标语义不变，事件表按 run 索引。
- **部分失败的产品语义可能被误读为成功** → 运行 `failed` + `failedPlatforms` + `partial=true` 三处同时表达，页面不显示完成态。
- **`WORKER_RUN_KINDS` 引入新的部署约束** → 默认值覆盖全部五类；未声明的 job 保持 `queued` 而非失败，README 与 Compose 注释说明何时收紧。

## Migration Plan

1. 新迁移 `0013_dual_platform_runs.sql` 只做前向变更：扩展 `runs.kind`、加唯一约束、建四张表与索引；
2. 部署后旧数据卷直接启动，历史 run/job/准备/图谱记录不变；
3. 默认 `SIMULATION_ENGINE=double`，无需新密钥；
4. 需要真实 OASIS 时显式构建 `worker-simulation` 并启用 profile；不使用时可随时关闭，历史运行记录仍可读（动作已落库，引擎目录可清理）。

## Open Questions

- 是否需要在第三阶段提供“下载本次运行动作 CSV”？（本轮不做，留待报告阶段）
- 真实 OASIS 的 `like_post` 在 `twitter` 上是否需要目标帖必填？以引擎记录为准，读回时按实际字段校验，不预设。
