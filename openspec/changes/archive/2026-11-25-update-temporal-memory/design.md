# Design

## Context

本变更建立在 Change 09 已合并实现之上。只读核对到的现状如下：

- `app/services/simulation_worker.py` 的 `_advance_platform()` 逐平台独立推进，`_book_round()` 在锁定任务行、用数据库实际时钟验证租约后调用 `simulation_store.write_round()`，把轮次、动作、平台计数与一条 `run_events` 进度事件放在同一事务提交；同一轮可以有多条 `simulation_actions`。
- `simulation_actions` 已保存 `id`、`platform_key`、`round_index`、`action_seq`、`native_action`、`actor_role_id`、`actor_display_name`、`content`、`engine_record_id`、`target_engine_record_id`、`target_action_id`、`target_resolved` 与 `simulated_at`；`simulation_rounds` 以 `(simulation_id, platform_key, round_index)` 为唯一游标。
- `_round_plan()` 已从 `simulation_store.recent_posts()` 读取本平台已提交帖子，再调用 `app/engines/duet_contracts.py` 的 `plan_round()` 生成确定性计划；`plan_round()` 目前只接受 `known_posts`，没有记忆引用参数，也没有把“角色读到了什么”持久化。
- 运行详情的 `serialize_simulation()` 已返回 `runId`、`jobId`、`selectionId`、`buildId`、`snapshotId`、`question`、`engine`、`phase`、轮次、时钟、平台统计、动作分页、`canStop` 与 `partial`；第三阶段 `web/src/views/DualPlatformRunPanel.vue` 已有两条行动流、目标原帖展开、运行日志、旧运行切换与 `runId`/`selectionId` 守卫。
- Change 07 已验证“持久化幂等键 → `graph.episode.add` → `search_episodes` 精确匹配 → `processed` → 节点/边读回”的四段事实，以及 `write_outcome=in_flight` 时先检索确认、不换键重写、无法确认时 `write_unconfirmed`。本变更复用同一纪律，但图谱是**本次运行私有**的，不写入项目证据图谱的 `graph_snapshots`。
- 现有 `project_graph_bindings` 是“项目一份证据图谱”；`graph_snapshots`、`graph_nodes`、`graph_edges`、`graph_evidence` 是冻结材料快照的物化。运行记忆必须有独立绑定、独立 episode 命名空间与独立读模型，否则会与材料快照和旧 build 互相污染。
- 现有 `runs` 与 `jobs` 是一对一（`jobs_run_unique`），因此记忆同步不能复用模拟运行的 `run_id`；若要独立续跑，必须新增 run 类型与 `run_id → simulationId` 的同步映射。
- `scripts/verify.py` 已有 `KNOWN_CHANGES`、`simulation_evidence`、隔离 Compose 项目与 `ALLOW_DIRTY` 规则；`docs/acceptance/` 只写人工记录。默认验收与 CI 必须保持零真实模型/Zep 外呼。

## Goals / Non-Goals

**Goals:**

- 每个已确认行动都能追溯：`actionId` → 轮次/模拟时间/原事件 → `memoryId` → `episodeUuid`，并可从运行图节点/边回到同一 run、同一平台的原行动。
- “第 R 轮结束后的图”有确定、可重放的截止语义；“角色开始第 R 轮前读到什么”有更严格的同平台前序语义，二者分别定义、分别取证。
- 角色记忆读取发生在实际执行入口并被持久化，不能只用查询 API 的结果冒充角色已读取。
- 两个平台独立；一侧到达第 3 轮不会让另一侧第 2 轮角色读到未来行动；同一冻结选择重跑时两次运行图谱与记忆完全隔离。
- 重复提交、响应丢失、worker 接手与图服务暂不可达都有确定的可见状态和恢复起点；任何情况下都不重复增长记忆、节点、边或来源。
- 材料事实、角色发言与推演结果各自保留来源；运行图只读冻结材料快照，MUST NOT 改写 `graph_snapshots`/`graph_nodes`/`graph_edges`/`graph_evidence`。
- 默认启动、默认 `make verify` 与 CI 零真实外呼；真实 Zep 记忆只在显式配置和独立运行中产生证据。

**Non-Goals:**

- 不实现第四阶段报告与第五阶段深度互动。
- 不接入真实外网 Twitter/Reddit，不保存平台账号凭据。
- 不把运行记忆写回项目证据图谱，不重新发布或迁移冻结材料快照。
- 不引入第二套事件流、第二套队列或前端定时器驱动的“记忆进度”。
- 不让 `app` 进程执行 Zep 调用，不在浏览器里做记忆抽取或节点归并。
- 不把私有 25 页报告、私有材料正文、私有截图或真实动作正文写入日志、报告、夹具或 CI 产物。

## Decisions

### 1. 运行图谱按 `simulationId` 独立绑定，绝不与材料图谱或旧 build 共用

**决定**：新增 `simulation_graph_bindings`：

| 列 | 语义 |
| --- | --- |
| `simulation_id` | 主键，指向 `simulations.id` |
| `create_idempotency_key` | 唯一，首次调用 `graph.create` 前持久化 |
| `request_name` / `request_description` / `request_time_zone` | 稳定请求参数 |
| `graph_id` | Zep 返回的 `Graph.uuid_`，唯一、可空 |
| `state` | `pending` / `bound` |
| `created_at` / `bound_at` | 审计时间 |

创建参数与幂等键只由 `simulationId` 派生：`simulation-memory-graph:v1:<simulationId>`。流程与 Change 07 一致：先在独立事务插入并提交绑定记录，再调用 `graph.create`；响应丢失时用同一幂等键重试；返回空 UUID 或与已保存 `graph_id` 不一致时以 `invalid_graph_scope` 失败，不覆盖。`graph_id` MUST NOT 等于 `ZEP_ACCEPTANCE_GRAPH_ID` 或当前项目 `project_graph_bindings.graph_id`；命中时在写入 episode 前失败。

同一冻结选择重跑会创建新的 `simulations` 行与新的 `simulationId`，因此得到新的 `simulation_graph_bindings`、新的 `graph_id` 与新的 episode 命名空间。旧运行的 `graph_id`、episode、节点/边与读模型保持可读，不因新运行被覆盖或删除。

**理由**：Zep 的节点/边按图谱聚合，若两次运行共用图谱，角色记忆和“第 R 轮图”必然互相污染。运行级图谱也是“同一选择重跑各自隔离”的最直接实现。

**备选方案**：

- 复用项目证据图谱，用 `simulationId` metadata 过滤：否决，节点/边会被 Zep 合并，无法保证重跑隔离，也会污染材料快照。
- 复用 Change 07 的 `project_graph_bindings`：否决，项目图谱是证据图谱，语义与生命周期都不同。
- 每次轮次新建图谱：否决，运行内前序记忆无法累积，旧轮次也无法回链。

### 2. PostgreSQL 本地记忆账本是权威读模型；Zep 是增量投影

**决定**：行动记账事务内同时写入 `simulation_memory_entries`（每条已确认行动一条）与 `simulation_memory_episodes`（幂等计划）。本地记忆账本立即对角色可见；Zep episode 写入是投影，异步推进。读取路径：

- 角色行动前的记忆读取只查本地账本（`simulation_memory_entries` + 冻结快照材料事实），不直接对 Zep 做无约束 `search_episodes`。这样即使图谱服务慢或暂不可达，角色仍能读到前序轮次已提交的记忆，也不会因为图谱里混有另一平台未来行动而泄漏。
- 运行图节点/边读模型由 `simulation_memory_nodes`/`simulation_memory_edges`/`simulation_memory_provenance` 物化；只有已处理（`processed`）的 episode 才贡献节点/边。未处理的本地记忆中 `graphState` 为 `pending`/`processing`，页面显示条目但不得伪造节点/关系。
- 材料事实不复制进运行记忆表；运行图视图按 `snapshotId` 只读 `graph_snapshots`/`graph_nodes`/`graph_edges`/`graph_evidence`，标注 `origin=material` 与原始来源。运行图节点/边标注 `origin=role`/`origin=simulation`。

**理由**：如果把 Zep 查询作为角色记忆的唯一入口，图谱服务一慢角色就失去记忆，也无法证明记忆属于哪个运行。把本地账本作为权威，Zep 只负责“从 episode 抽取节点/边”，既能保证时序边界确定，又能保留图谱增量更新的价值。

**备选方案**：

- 角色直接 `search_episodes`：否决，无法按平台/轮次严格截断，未来行动可能被检索到。
- 只存 Zep 节点、不存本地账本：否决，图服务不可达时无法行动，也无法解释记忆来源。
- 每次读取都从 Zep 重算：否决，刷新与重连不稳定，且会产生额外真实外呼。

### 3. 每条行动一条记忆、一条 episode；映射固定为 `actionId → memoryId → episodeUuid → eventSeq`

**决定**：`simulation_memory_entries` 与 `simulation_actions` 一对一：

| 字段 | 来源 |
| --- | --- |
| `id` (`memoryId`) | `uuid5(simulationId, "memory:" + actionId)`，稳定可重算 |
| `action_id` | `simulation_actions.id`，唯一 |
| `platform_key` / `round_index` / `action_seq` | 行动记录 |
| `actor_role_id` / `actor_display_name` | 行动记录 |
| `speech_text` | 发帖/评论正文；无正文动作为空 |
| `native_action` / `target_action_id` / `target_engine_record_id` | 行动记录 |
| `simulated_at` | 该轮模拟时刻 |
| `source_event_seq` | 该轮提交事件在 `run_events` 中的 `seq` |
| `created_at` | 记账事务时间 |

`simulation_memory_episodes` 以 `(simulation_id, memory_id)` 为主键，保存 `episode_key = "action:" + actionId`、`idempotency_key = uuid5(simulationId, "episode:" + actionId)`、`content`、`content_sha256`、`episode_uuid`、`state`、`write_outcome`、`attempts`、`round_index`、`simulated_at`、`source_event_seq`。

episode 正文由服务端确定性生成，包含角色、平台、轮次、模拟时间、动作、目标与简短发言；元数据至少包含 `purpose=simulation_memory`、`simulationId`、`actionId`、`memoryId`、`platformKey`、`roundIndex`、`simulatedAt`、`contentSha256`。正文与元数据都不得包含材料正文、完整来源地址、密钥或私有样本。

一个轮次事件可以对应多条行动；每条行动有自己的 `actionId`、`memoryId` 与 `episodeUuid`，但共享同一 `source_event_seq`。因此“第 2 轮的一条评论”可以精确定位到：`actionId`（评论）→ `roundIndex=2`、`simulatedAt` → `source_event_seq`（该轮事件）→ `memoryId` → `episodeUuid`。同一轮其他行动不会互相覆盖。

**理由**：按行动建 episode 才能让重复提交按行动去重，并让“第 1 轮没有、第 3 轮有”成为简单的时间截断，而不是批次内位置玄学。按轮事件共享 `source_event_seq` 又保留了 Change 09 的“每轮一次提交事件”语义。

**备选方案**：

- 每轮一个 episode：否决，重复提交单个行动会改变整批内容，无法幂等；也无法精确定位单条行动。
- 每条边一个 episode：否决，一次行动会拆成多个 episode，重复与来源会爆炸。
- 不保存 `source_event_seq`：否决，页面无法把图和原运行日志对上。

### 4. “第 R 轮结束后的图”与“角色开始第 R 轮前读到什么”是两套不同语义

**决定**：

| 语义 | 截止条件 | 平台 | 用途 |
| --- | --- | --- | --- |
| 查看第 R 轮结束后的运行图 | 记忆条目 `round_index <= R`；节点/边只要有一个来源条目的轮次 `<= R` 即纳入 | 可选 `platform` 过滤；不传时同时展示两平台，但每条来源仍带平台 | 历史回看、来源定位、页面轮次切换 |
| 角色开始第 R 轮行动前可读 | 同运行、同平台、`round_index < R` 的行动记忆 + 冻结材料事实 | 严格同一平台 | 角色规划与引擎请求的记忆注入 |

补充规则：

- “第 R 轮图”不按墙上时钟或当前最新轮次动态漂移；它只由持久化的 `round_index` 截断。刷新后同一 `runId` + `round` + `platform` 返回同一集合。
- 不传 `platform` 时，平台 A 的第 3 轮记忆与平台 B 的第 2 轮记忆可以同时出现在“第 3 轮图”中，但每条记录都标注自己的 `platformKey` 与 `roundIndex`；不会把平台 B 的第 2 轮冒充成第 3 轮。
- 角色读取严格排除：第 R 轮本身、同一轮其他角色、另一平台任意轮次、任何 `round_index >= R` 的记忆。若平台 A 已到第 3 轮而平台 B 还在第 2 轮，平台 B 第 2 轮角色只能看到平台 B 第 1 轮与材料事实。
- 材料事实对所有轮次可见，但只作为只读 `origin=material` 引用；不参与“第几轮行动记忆”的轮次计数。
- 每条计划行动在 `simulation_round_plans.request_payload.actions[].memoryRefs` 中携带本次实际使用的 `{memoryId, actionId, roundIndex, simulatedAt, episodeId?, sourceEventSeq?, materialRefs?}`；行动记账后写入 `simulation_action_memory_reads`，同一 `(action_id, memory_id)` 唯一。验收必须读这两处，而不是只读 `GET /memory`。

**理由**：回看与执行是两个不同的可观察结果。回看可以跨平台做时间切片；执行必须严格按平台和“开始行动前”截断，否则会读出未来或另一平台的行动。把 `memoryRefs` 放在实际请求/计划里，才能证明角色真的读到，而不是 API 事后能查到。

**备选方案**：

- 统一用“最新图”做角色记忆：否决，会读到未来行动。
- 统一用 `round_index <= R` 做角色记忆：否决，会读到同一轮已经执行的行动，破坏“开始第 R 轮前”的语义。
- 只在前端做轮次过滤：否决，服务端与引擎仍会读到未来记忆，无法验收。

### 5. 增量投影：每轮记账后尽力投影，未完成由 `simulation.memory` 任务续跑

**决定**：

- `advance` 仍保持 Change 09 的四步 `bind`、`environment`、`advance`、`publish`。每平台每轮 `_book_round()` 在同一事务写入行动、轮次、平台计数、该轮事件、`simulation_memory_entries` 与 `simulation_memory_episodes`（`state=planned`）。事务提交后，worker 对该轮记忆做一次**尽力而为**的增量投影：按 `action_seq` 顺序写入/确认 episode、等待 `processed`、读回本轮节点/边并挂来源。失败只更新该轮记忆的 `graphState` 与运行 `memoryStatus`，不阻断另一平台、不阻断后续轮次。
- `publish` 不等待全部记忆完成；它只发布动作/轮次/计数终态，并计算 `memoryStatus`。若仍有 `planned`/`in_flight`/`unconfirmed` episode，在终态事务提交后创建或复用 `simulation.memory` 任务；创建失败只影响记忆状态，不改变运行终态。
- `simulation.memory` 是独立 run 类型，固定三步 `bind`、`sync`、`publish`，操作键 `simulation-memory:v1:<simulationId>`。它只处理 `simulation_memory_episodes` 与节点/边读回，不创建 `simulation_actions`、`simulation_rounds`，不改变平台计数、运行 `phase`、`failedPlatforms` 或终态事件。
- `sync` 从最早未完成 episode 开始（按 `round_index`、`action_seq`、`platform_key` 排序），复用已持久化的幂等键与内容。每个 episode 的状态推进（`planned → in_flight → accepted → matched → processed` 或 `unconfirmed`）在独立事务提交；读回节点/边后更新 `simulation_memory_nodes`/`simulation_memory_edges`/`simulation_memory_provenance` 并刷新 `simulations.memory_status`。
- 页面在运行期间可刷新看到已处理轮次的图；未处理条目显示“投影中/待同步”，未确认 episode 显示“写入待确认”，不画成节点或关系。

**理由**：把记忆写入放进模拟主事务会让图服务故障直接拖死推演；完全异步到运行结束又会让“运行时图增长”变成空话。每轮尽力投影 + 独立补跑任务兼顾两者，并保持 Change 09 四步语义不变。

**备选方案**：

- 在 `advance` 内同步等待全部 episode `processed`：否决，慢图服务会阻塞两个平台，且拖长租约窗口。
- 运行结束后一次性建图：否决，第三阶段运行中看不到增长，角色也无法用上一轮记忆。
- 在 `app` 请求内写 episode：否决，违反长任务只能由 worker 执行。

### 6. 重复提交与写入未知：先查账，再补记；未知保留可见状态

**决定**：`simulation_memory_episodes.write_outcome` 取 `not_attempted`、`in_flight`、`rejected_retryable`、`accepted`、`unconfirmed`；`state` 取 `planned`、`accepted`、`matched`、`processed`、`unconfirmed`、`failed`。

写入前先在同一事务把 episode 置为 `in_flight` 并递增 `attempts`，再调用 `graph.episode.add(idempotency_key=...)`：

1. 返回 `episode.uuid_`：保存 `episode_uuid`、`state=accepted`、`write_outcome=accepted`。
2. 响应丢失、连接中断或返回空 UUID：保持原幂等键与内容，进入 `in_flight`，先 `search_episodes`/`episode.list` 按 `simulationId`、`actionId`、`memoryId` 与 `content_sha256` 精确匹配；命中则补记 UUID，MUST NOT 再次 `add`；未命中且预算内继续等待，MUST NOT 换键或改写内容。
3. 预算耗尽仍无法确认：`write_outcome=unconfirmed`、`state=unconfirmed`，保留在页面与数据库；不得当作成功，不得贡献节点/边，不得被角色读取为已提交记忆。
4. 有明确证据表明上一次写入被拒绝且可安全重试（如请求从未离开进程的本地失败）：`rejected_retryable`，复用原键重试；任何可能已送达的失败都按“未知”处理。

同一已确认行动的重复提交（同一 `actionId` 再次进入记账/投影）MUST 命中 `(simulation_id, memory_id)` 与 `idempotency_key` 唯一约束，只做状态校验，不新增记忆、episode、节点、边或来源行；`simulation_actions`、`simulation_rounds`、计数与事件序号都不增加。节点按 `(simulation_id, node_key)` 归并，边按 `(simulation_id, edge_key)` 归并；来源表以 `(simulation_id, target_kind, target_key, memory_id, facet)` 唯一，重复来源只保留一条。

**理由**：Zep `episode.add` 没有客户端可见的通用“查重”保证，唯一可控的是持久化幂等键与内容哈希。先查账再补记，才能覆盖“服务端已写入、本地未确认就中断”，也避免用户看到“失败”后重试产生第二份记忆。

**备选方案**：

- 响应丢失直接重写：否决，会产生重复 episode/节点/边。
- 未知时直接标失败并换新键：否决，真实写入可能已发生，换键等于故意制造重复。
- 用时间戳或前端按钮猜是否写入：否决，不是提交事实。

### 7. 图服务暂不可达：推演继续，记忆状态可见，恢复从最早未完成 episode 继续

**决定**：

- `simulations.memory_status` 取 `unconfigured`、`pending`、`syncing`、`complete`、`partial`、`unconfirmed`、`unavailable`。它不替代运行 `phase`，也不改变动作与计数。
- 图服务不可达时：行动、轮次、事件与本地记忆照常提交；页面显示“记忆同步暂不可用”、待同步 episode 数、最后已同步轮次与原因码 `memory_unavailable`。角色仍读取已提交的本地记忆；未提交/未确认的记忆不进入角色读取。
- 恢复入口：
  - 运行未终态时，`simulation.memory` 任务由 worker 重新领取，或 `advance` 下一轮边界再次尽力投影；从最早未完成 episode 继续，复用原键。
  - 运行已终态但记忆未完成时，`POST /api/projects/{p}/simulations/{runId}/memory/retry` 确保存在一个活动 `simulation.memory` 任务；已有活动任务返回 200 与同一 `memoryJobId`，否则创建新任务并返回 202。重试不改变运行 `phase`、动作、轮次、计数或终态事件。
  - 写入未知（`unconfirmed`）优先查账；只有查账确认后才转为 `committed`。重试不得以“用户点了重试”为由换键重写。
- `memory_status=complete` 的条件：所有行动记忆 episode 均已 `processed`，且节点/边读回完成；`partial` 表示部分轮次已可用、仍有未完成 episode；`unconfirmed` 表示至少一个写入结果未知；`unavailable` 表示最近一次同步因图服务不可达中止。

**理由**：用户需要区分“行动没发生”“行动发生了但记忆还没写”“记忆可能写了但无法确认”“图服务暂时不可达”。把这些差异压成运行失败会丢失已发生的动作，也会让恢复无从下手。

**备选方案**：

- 图服务不可达就让整个运行失败：否决，动作已写入引擎，失败语义会误导用户。
- 无限阻塞等待图服务：否决，worker 被长时间占用，停止与接手语义变复杂。
- 只显示前端“同步中”动画：否决，刷新即丢失，且无法解释未确认状态。

### 8. 来源分层：材料、角色发言、推演结果各自保留来源

**决定**：运行图读取响应固定包含三层：

| 层 | `origin` | 来源字段 |
| --- | --- | --- |
| 材料事实 | `material` | `snapshotId`、`nodeKey`/`edgeKey`、`documentId`、`pageNo`、`sourceStart`/`sourceEnd`（按需） |
| 角色发言 | `role` | `memoryId`、`actionId`、`actorRoleId`、`speechText`、`episodeUuid`、`sourceEventSeq`、`platformKey`、`roundIndex`、`simulatedAt` |
| 推演结果 | `simulation` | `memoryId`、`actionId`、`nativeAction`、`targetActionId`/`targetEngineRecordId`、`episodeUuid`、`sourceEventSeq`、`platformKey`、`roundIndex`、`simulatedAt` |

材料层只读：`GET /memory` 不写 `graph_snapshots`/`graph_nodes`/`graph_edges`/`graph_evidence`；运行后的快照行数、节点数、边数、来源数与 `snapshotId` 必须与运行前一致。角色发言与推演结果即使指向同一行动，也保留各自 `facet` 与 `memoryId`；节点/边可以按规范化名称归并，但来源表按 `facet` 保留两条可核对记录。

**理由**：材料事实是已签收证据，角色发言是生成内容，推演结果是动作事实。三者混成一种 `origin` 会让用户把角色话术当成材料事实，也会让运行图反过来“改写”第一阶段快照。

**备选方案**：

- 把角色发言写回材料快照：否决，破坏签收证据。
- 只保留一个 `simulation` 来源：否决，无法区分“材料提到”与“角色说了/做了”。

### 9. 接口契约与访问边界

| 方法 | 路径 | 语义 | 状态码 |
| --- | --- | --- | --- |
| GET | `/api/projects/{p}/simulations/{runId}/memory?round=R&platform=platform_b` | 第 R 轮结束后的运行图与记忆来源 | 200；400 `round_invalid`/`platform_invalid`；409 `round_not_available`；404 `项目不存在`/`运行不存在`；401；503 |
| POST | `/api/projects/{p}/simulations/{runId}/memory/retry` | 确保记忆同步任务 | 202 新建、200 复用、404、401、503 |
| GET | `/api/projects/{p}/simulations/{runId}` | 详情增加 `memory` 摘要与 `memoryJobId` | 200、404、401、503 |

`GET /memory` 响应形状：

```json
{
  "runId": "…",
  "round": 2,
  "platform": "platform_b",
  "cutoff": {"round": 2, "availableMaxRound": 3,
             "platforms": {"platform_a": {"completed": 3}, "platform_b": {"completed": 2}}},
  "status": {"code": "complete|partial|pending|unconfirmed|unavailable|unconfigured",
             "pendingEpisodes": 0, "unconfirmedEpisodes": 0,
             "lastSyncedRound": 2, "reason": null},
  "material": {"snapshotId": "…", "nodes": [], "edges": []},
  "runtime": {"nodes": [], "edges": []},
  "entries": [
    {"memoryId": "…", "actionId": "…", "platformKey": "platform_b",
     "roundIndex": 2, "simulatedAt": "…", "sourceEventSeq": 6,
     "actorRoleId": "…", "actorRoleName": "…", "nativeAction": "create_comment",
     "targetActionId": "…", "episodeId": "…", "graphState": "processed",
     "facets": ["role_speech", "simulation_result"]}
  ]
}
```

规则：

- `round` MUST 是整数且 `1 <= round <= simulations.rounds`；否则 400 `round_invalid`，不访问图服务。
- 若指定 `platform`，该平台已完成轮次 `< round` 时返回 409 `round_not_available`，并带 `availableMaxRound`；不指定平台时，至少一个平台已完成 `round` 才返回 200，否则 409。
- 已到达但记忆投影未完成时仍返回 200，`status.code` 为 `pending`/`partial`/`unconfirmed`/`unavailable`，已处理部分照常返回。
- 匿名请求在访问数据库前 401；他人项目、不存在项目统一 404 `项目不存在`；他人/不存在运行统一 404 `运行不存在`；数据库不可达 503 且不清除 Cookie。
- 响应 MUST NOT 包含材料正文、完整来源地址、密钥、完整 episode 正文或内部绝对路径；只返回标识、计数、摘要与来源引用。

**理由**：沿用 Change 09 的“身份 → 项目归属 → 运行归属”顺序，补上轮次合法性；把“尚未到达”和“参数非法”分开，页面才能给出不同提示。

### 10. 数据模型与前向迁移

新增 `app/migrations/0015_temporal_memory.sql`：

- 扩展 `runs_kind_check` 加入 `simulation.memory`。
- `simulation_graph_bindings`：运行级图谱绑定。
- `simulation_memory_entries`：`(simulation_id, action_id)` 唯一；保存行动记忆、`source_event_seq`、`simulated_at`、`speech_text`、`native_action`、目标字段。
- `simulation_memory_episodes`：`(simulation_id, memory_id)` 主键；`idempotency_key` 唯一；`content_sha256`、`episode_uuid`、`state`、`write_outcome`、`attempts`、`round_index`、`simulated_at`、`source_event_seq`。
- `simulation_memory_nodes`：`(simulation_id, node_key)` 主键；`node_type`、`label`、`origin`、`first_round`、`last_round`、`summary`。
- `simulation_memory_edges`：`(simulation_id, edge_key)` 主键；端点、`edge_type`、`label`、`origin`、`first_round`、`last_round`、`summary`。
- `simulation_memory_provenance`：`(simulation_id, target_kind, target_key, memory_id, facet)` 主键；`action_id`、`episode_uuid`、`platform_key`、`round_index`、`simulated_at`、`source_event_seq`、可空 `material_snapshot_id`/`material_key`。
- `simulation_round_plans`：`(simulation_id, platform_key, round_index)` 主键；`memory_cutoff_round`、`request_payload`、`request_sha256`、`created_at`。
- `simulation_action_memory_reads`：`(action_id, memory_id)` 主键；`simulation_id`、`platform_key`、`round_index`、`episode_uuid`、`read_at`。
- `simulation_memory_syncs`：每次尝试一行，`run_id` 主键、`job_id` 唯一；同一 `simulation_id` 允许多行并保留历史；`phase`、`pending_episodes`、`last_error_code`、时间戳。活动任务以 `jobs.status IN ('queued','running')` 复用，终态后允许同一 `simulationId` 新建任务。
- 索引：`simulation_memory_entries(simulation_id, platform_key, round_index, action_seq)`、`simulation_memory_episodes(simulation_id, state)`、`simulation_memory_provenance(simulation_id, target_kind, target_key)`、`simulation_action_memory_reads(simulation_id, action_id)`。

约束：`round_index >= 1`、`action_seq >= 1`、`state`/`write_outcome`/`memory_status` 枚举、`origin` 枚举（`role`/`simulation`）、`facet` 枚举（`role_speech`/`simulation_result`）、`content_sha256` 长度 64；`simulation_graph_bindings` 与 `simulation_memory_episodes` 通过复合外键指向 `simulations(id)`，旧数据卷启动时只新增空表，不回填、不改写。

迁移 MUST 在空数据卷与已有 Change 01–09 数据卷上各启动一次；旧 `simulations`、`simulation_actions`、`simulation_rounds`、`graph_snapshots` 数据不变，重复启动只执行一次。

### 11. 前端：轮次时间线、图层与迟到响应守卫

`web/src/views/DualPlatformRunPanel.vue` 增加记忆区：

- 轮次选择器：`1..platform.roundsCompleted`（不指定平台时取最大已完成轮），默认最新已完成轮；切换使用 `router.replace` 保留 `run`、`selection`、`build`、`preparation`、`layout`、`lang`，并设置 `round` 与可选 `platform`。
- 图层：材料、角色发言、推演结果三种图例；材料层来自 `material`，运行层来自 `runtime`；图节点/边详情显示 `origin` 与来源字段。
- 原行动回链：点击节点/边来源打开行动详情，显示 `actionId`、角色、平台、`roundIndex`、模拟时间、`sourceEventSeq` 与目标；只允许同一 `runId` 的 `actionId`。目标未解析时显示“未在本运行中定位”，不跳转。
- 状态徽标：`complete`、`partial`、`pending`、`syncing`、`unconfirmed`、`unavailable`、`unconfigured`；`unavailable` 显示重试按钮，`unconfirmed` 显示“写入待确认，不会换键重写”，不显示为完成。
- 刷新恢复：页面从 URL 读 `round`/`platform`，先读运行详情再读记忆；`runId` + `round` + `platform` 守卫丢弃迟到响应；切换运行或轮次时清空旧图，不把上一运行/轮次的节点画到新图。
- 记忆同步中不在前端累加计数；图节点/边、轮次与状态全部来自服务端响应。

`web/src/simulation/state.ts` 增加 `memory` 状态与 `applyMemoryDetail`/`switchMemoryView` 纯函数；`npm test` 覆盖迟到响应、切换轮次、切换平台、运行/轮次守卫、未确认状态与 `memoryStatus` 展示文案。

### 12. 配置与 provider 边界

新增配置（`.env.example`、`docker-compose.yml`、`app/config.py`、README 同步）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SIMULATION_MEMORY_PROVIDER` | `auto` | `auto|stub|zep|unconfigured`；`auto` 在 stub 允许时用 stub，Zep 完整配置时用 zep，否则 unconfigured |
| `SIMULATION_MEMORY_MAX_REFS` | `20` | 每次行动最多注入的前序记忆条数（1–200） |
| `SIMULATION_MEMORY_MAX_NODES` | `2000` | 运行图节点上限 |
| `SIMULATION_MEMORY_MAX_EDGES` | `4000` | 运行图边上限 |
| `SIMULATION_MEMORY_MAX_SOURCES_PER_ITEM` | `10` | 单个节点/边来源上限 |
| `SIMULATION_MEMORY_INDEX_TIMEOUT_SECONDS` | `30` | episode 索引确认预算 |
| `SIMULATION_MEMORY_PROCESSING_TIMEOUT_SECONDS` | `60` | episode 处理预算 |
| `SIMULATION_MEMORY_RETRY_BUDGET_SECONDS` | `120` | 记忆同步任务单次重试预算 |
| `SIMULATION_MEMORY_BATCH_SIZE` | `20` | 每次同步处理的 episode 批次 |

`SIMULATION_MEMORY_PROVIDER=auto` 的判定顺序：显式 `stub` 且 `EVIDENCE_GRAPH_ALLOW_STUB=true` → stub；`ZEP_API_KEY` 等 Zep 配置完整 → zep；否则 unconfigured。unconfigured 时 `memory_status=unconfigured`，不发起任何外部调用，模拟运行照常完成。默认 `make up`、默认 `make verify` 与 CI MUST 不产生真实模型/Zep 外呼；真实 Zep 只由开发者显式配置并执行。

### 13. 验收与证据

`scripts/verify.py` 注册 `TEMPORAL_MEMORY_CHANGE_ID = "2026-11-25-update-temporal-memory"`。默认 `make verify` 增加“未触发记忆任务”的轻量断言：无运行、无 `simulation.memory` job、`temporal_memory_evidence.mode="not-triggered"`、`calls.real=0`。

Change 10 专属场景（使用原创材料、stub 图谱、`engine=double`）：

1. 上传原创材料 → 建图（stub）→ 准备出角色 → 冻结 3 轮 → 开始运行；记录材料快照的 `snapshotId`、节点数、边数、来源数与哈希。
2. 运行完成后读取三个轮次图：`round=1` 不含第 2 轮评论的 `memoryId`/`actionId`；`round=2` 含；`round=3` 仍含。选择第 2 轮一条 `create_comment`，断言 `actionId`、`platformKey`、`roundIndex=2`、`simulatedAt`、`sourceEventSeq`、`memoryId`、`episodeUuid` 全部可对上，并可从运行图来源回到同一 run 的原行动。
3. 读取角色执行入口：对第 3 轮行动断言 `simulation_round_plans.request_payload.actions[].memoryRefs` 只含同平台 `round_index < 3` 的记忆；对平台 A 第 3 轮与平台 B 第 2 轮并发场景断言 B 第 2 轮不含 A 第 3 轮记忆；`simulation_action_memory_reads` 与请求中的 `memoryId` 一致。
4. 用同一 `selectionId` 重跑：新 `runId`、新 `graphId`、新 `memoryId`/`episodeUuid`；两次运行的记忆集合无交集；旧运行 `round=2` 仍可读。
5. 重复提交：对同一已确认行动触发两次记忆投影/同步，断言 `simulation_memory_entries`、`episodes`、`nodes`、`edges`、`provenance` 计数不变；`simulation_actions`、`rounds`、平台计数、`run_events` 序号不变。
6. 中断恢复：在 episode 写入响应丢失但服务端已受理后停止 worker，恢复后断言 `attempts` 增加、先查账补记、只保留一个 episode，节点/边不重复；在无法确认时断言 `write_outcome=unconfirmed`、`memory_status=unconfirmed`、页面可见、未换键；图服务不可达时断言动作继续、`memory_status=unavailable`、待同步数可见，恢复后从最早未完成 `memoryId` 继续。
7. 边界：匿名 401、他人项目 404、不存在运行 404、非法轮次 400、尚未到达轮次 409、停库 503 保留 Cookie、跨运行/跨平台来源不链接。
8. 断言材料快照行数、节点数、边数、来源数与哈希与运行前一致；`temporal_memory_evidence.calls.real=0`、`calls.double>0`；连续两次执行通过；未知 `CHANGE` 退出码 2。

`temporal_memory_evidence` 结构：

```json
{"mode":"offline-stub","graphProvider":"stub",
 "calls":{"real":0,"double":12},
 "callScope":[{"runId":"…","graphId":"…","episodes":9,"nodes":6,"edges":8}],
 "scenarios":[
   {"scenario":"temporal-memory-round-view","runId":"…","actionId":"…","memoryId":"…",
    "episodeId":"…","platformKey":"platform_b","roundIndex":2,"sourceEventSeq":6,
    "round1Contains":false,"round2Contains":true,"round3Contains":true,
    "materialSnapshotId":"…","snapshotUntouched":true},
   {"scenario":"temporal-memory-role-reads","runId":"…","platformKey":"platform_b",
    "roundIndex":3,"expectedMemoryIds":["…"],"actualMemoryRefs":["…"],
    "futureExcluded":true,"otherPlatformExcluded":true,"executionEntry":"round-plan"},
   {"scenario":"temporal-memory-idempotency-recovery","runId":"…",
    "duplicate":{"before":9,"after":9,"growth":0},
    "takeover":{"attemptsBefore":1,"attemptsAfter":2,"reconciled":true},
    "unconfirmed":{"state":"unconfirmed","visible":true,"newKeyCreated":false},
    "unavailable":{"status":"unavailable","pending":2,"resumedFromMemoryId":"…"}}
 ],
 "isolation":{"runA":"…","runB":"…","overlap":0},
 "workerRestored":{"env":"stub","ok":true}}
```

人工浏览器走查只写 `docs/acceptance/`：轮次切换、图例、来源回链、刷新恢复、迟到响应、未确认/暂不可用状态、中英文与三种布局。`make verify`、CI 不得创建、修改或删除该目录内容。

### 14. 三个核心验收场景（操作手册）

| # | 操作 | 预期观察值 | 证据位置 |
| --- | --- | --- | --- |
| ① | 用原创材料完成建图、准备与冻结 3 轮；开始运行；选择第 2 轮的一条评论；依次查看 `round=1`、`round=2`、`round=3` 的运行图；点击该评论来源回链 | 第 1 轮图查不到该 `memoryId`/`actionId`，第 2、3 轮图能查到；回链保持同一 `runId`、同一 `platformKey`，显示 `roundIndex=2`、`simulatedAt`、`sourceEventSeq` 与原行动；同轮其他行动有独立 `actionId`/`memoryId`；材料、角色发言、推演结果各自带来源；冻结材料快照计数与哈希不变 | `temporal_memory_evidence.scenarios[0]`；`GET /memory` 响应；`simulation_memory_entries`/`episodes`/`provenance`；浏览器截图与走查记录 |
| ② | 观察运行中每轮结束后的图增长；在平台 A 到第 3 轮、平台 B 到第 2 轮时查看 B 第 2 轮角色行动；刷新并切换历史轮次；用同一 `selectionId` 重跑 | 每轮结束后已处理节点/边增加，未处理记忆显示投影状态；B 第 2 轮角色的 `memoryRefs` 只含 B 第 1 轮与材料事实，不含 A 第 3 轮/B 第 2 轮/未来；`simulation_action_memory_reads` 与请求一致；刷新后同 `runId`+`round`+`platform` 返回同一集合；重跑得到新 `runId`/`graphId`，两次记忆无交集，旧运行仍可读 | `temporal_memory_evidence.scenarios[1]`、`isolation`；`simulation_round_plans.request_payload`；`simulation_action_memory_reads`；运行详情 `memory`；浏览器走查 |
| ③ | 对同一已确认行动重复提交；在 episode 已写入但本地未确认时中断 worker；在图服务不可达时继续运行；恢复后重试 | 重复提交后记忆、episode、节点、边、来源计数均不增长；接手先查账，有证据则补记且 `attempts>1`，不重复写；无法确认时 `write_unconfirmed`/`memory_status=unconfirmed` 可见且不换键；图服务不可达时动作继续、`memory_status=unavailable`、待同步数可见；恢复后从最早未完成 `memoryId` 继续 | `temporal_memory_evidence.scenarios[2]`；`simulation_memory_syncs`；`simulation_memory_episodes`；运行详情 `memory`；worker 日志（仅标识与原因码）；浏览器走查 |

## Risks / Trade-offs

- **Zep 对 `episode.add` 的幂等语义仍依赖服务端**：本项目只承诺持久化同一幂等键、同一内容与先查账；若服务端不按幂等键去重，响应丢失后仍可能产生重复 episode。缓解：查账以 `simulationId`/`actionId`/`memoryId`/内容哈希精确匹配，未知时保留 `write_unconfirmed`，不发布为可用记忆；验收覆盖“已受理、响应丢失、首次搜索未命中”。
- **每行动一个 episode 会增加 Zep 调用量**：1000 轮 × 多行动可能很大。缓解：批次上限、引用上限、节点/边上限、`SIMULATION_MEMORY_BATCH_SIZE`，并只对已确认行动投影；规模超限明确 `memory_too_large`，不静默截断成“完成”。
- **本地记忆账本与 Zep 投影可能短暂不一致**：页面必须显示 `graphState` 与 `memory_status`，不能把本地条目当成已发布节点；角色读取本地账本，图读取只读已处理 episode。
- **独立记忆同步任务增加一个 run/事件空间**：页面以运行详情 `memory` 摘要为准，记忆任务事件不进入模拟运行事件流；需要确保 `simulation.memory` 不写动作、轮次、计数或终态。
- **默认 `make up` 没有 Zep 配置**：`memory_status=unconfigured`，不发起外呼，第三阶段仍可运行但无图谱记忆；这是安全默认，不把离线结果写成真实图谱证据。
- **材料快照只读检查可能被误做成“复制快照”**：复制也会造成两个事实来源。缓解：只读 `graph_snapshots` 与 `graph_evidence`，不写入、不重发、不更新 `published`。

## Migration Plan

1. 新增 `0015_temporal_memory.sql`：扩展 `runs.kind`，创建运行图谱绑定、记忆条目/episode、运行图节点/边/来源、轮次计划、行动记忆读取与同步映射表；只前向，不回填。
2. 旧数据卷启动后旧 `simulations`、`simulation_actions`、`simulation_rounds`、`graph_snapshots` 不变；旧运行没有记忆记录，读取详情返回 `memory.status=unconfigured`（或 `pending` 的兼容空态），不伪造记忆。
3. 默认 `SIMULATION_MEMORY_PROVIDER=auto`：无 Zep、未允许 stub 时为 unconfigured；默认验收使用 stub。需要真实 Zep 时显式配置并单独记录真实证据。
4. 记忆任务只处理已存在的行动记忆；不重跑旧运行的动作。旧运行若从未产生记忆，不自动补建，以免把历史 action 当作新记忆写入。

## Open Questions

- 记忆同步任务是否需要在前端显示独立事件流，还是只通过运行详情轮询 `memory` 摘要？本轮设计为“详情摘要 + 刷新/轮询恢复”，不新增第二套事件协议；若后续需要实时记忆进度，再在 `run_events` 之外单独评估。
- 运行图节点/边是否需要与材料节点做跨层链接（例如角色行动指向材料主体）？本轮保留 `material_snapshot_id`/`material_key` 可空来源字段，不强制跨层合并，避免改变材料快照语义。
- 真实 Zep 的 `episode.list` 是否在所有账号版本可用？沿用 Change 07 的“优先 list，回退 search_episodes”策略，具体以真实联调为准，不在默认验收中假设。
