# Proposal

## Why

Change 09 已经把冻结选择跑成双平台原生动作，但第三阶段仍只有“当前动作流”和“当前计数”：

- 页面无法按轮查看“某一轮结束后的运行图”，也无法从第 3 轮图里的一个节点回到第 2 轮产生它的评论或转发；行动、轮次、模拟时间、原运行事件与图谱 episode 之间没有可核对的对应关系。
- 角色在下一轮行动时没有读取前序行动形成的记忆。即使查询接口能返回节点或关系，也不能证明角色在实际行动入口真的读到了它们；平台 A 先到第 3 轮时，平台 B 第 2 轮的角色还可能误读未来行动。
- 运行时图谱更新没有“重复提交不增长、写入已发生但本地未确认时先查账、图服务暂不可达时可恢复”的持久纪律。一次重试、worker 接手或响应丢失就可能产生重复 episode、重复节点/关系，或让用户无法判断当前记忆到底写没写入。
- 运行图与冻结材料快照没有明确分层。若把角色发言或推演结果直接写回证据图谱快照，会改写第一阶段已签收的材料事实。

本变更补齐第三阶段的时序记忆：把每次已确认行动变成可定位、可去重、可恢复的记忆与 episode，按轮提供只读运行图，让角色在行动前实际读取同运行、同平台、前序轮次的记忆，并把图服务未知结果保留为可见状态而不是假成功或重复写入。

## What Changes

- 新增运行级时序记忆：每个 `simulation.run` 绑定一份只属于本次运行的图谱，重跑同一冻结选择得到新的运行图谱；旧运行图谱与记忆永久可读。运行图谱 MUST NOT 复用项目证据图谱、旧 build 图谱或验收专用图谱。
- 新增“每个已确认行动对应一条记忆与一个 episode”的映射：`actionId`、`platformKey`、`roundIndex`、`simulatedAt`、原运行事件序号、`memoryId`、`episodeUuid` 一一可查。同一轮的多条行动各自有独立 `actionId`/`memoryId`/`episodeUuid`，共用该轮的提交事件。
- 新增按轮运行图读取：`GET /api/projects/{projectId}/simulations/{runId}/memory?round=R&platform=...` 返回“第 R 轮结束后的运行图”。运行记忆按 `round_index <= R` 截断；材料层只读冻结快照；角色发言与推演结果分别带 `memoryId`、`actionId`、`episodeUuid` 与事件来源；节点或边可回到同一运行、同一平台的原行动。
- 新增角色行动前记忆读取：角色开始第 R 轮时只读取同一运行、同一平台、`round_index < R` 的记忆，加上冻结材料事实；不得读取第 R 轮、同一轮其他角色、另一平台或任何未来行动。读取到的记忆标识 MUST 在实际行动入口（轮次计划与引擎请求）可见，并持久化为 `simulation_action_memory_reads`，不能只用查询接口结果证明。
- 新增增量投影：行动记账时在同一事务写入本地记忆；每轮结束前后对本次运行图谱做增量 episode 写入与读回。页面显示已处理的图和“投影中/待同步/待确认/暂不可用”的状态；未确认的 episode 不得伪装成已发布节点或关系。
- 新增重复提交与恢复纪律：同一已确认行动重复提交不新增记忆、episode、节点、边或来源计数；写入响应丢失、worker 中断或图服务暂不可达时，先按 `actionId`/`memoryId`/内容哈希查账，有证据则补记，结果仍未知则保留 `write_unconfirmed` 可见状态且不换键重写；恢复从最早的未完成 episode 继续，复用原幂等键。
- 新增记忆同步任务与重试入口：模拟运行主体仍保持 Change 09 的四步 `bind`、`environment`、`advance`、`publish`；未完成的记忆投影由独立 `simulation.memory` 任务在 worker 中续跑，操作键绑定 `simulationId`。记忆同步任务只处理 episode 与读回，不新增动作、轮次或计数，也不改写运行终态。
- 新增第三阶段界面：轮次选择器（1..已完成最大轮）、平台筛选、运行图层、材料/角色/推演来源图例、记忆同步状态、原行动回链、刷新恢复与迟到响应守卫。切换轮次使用 `replace` 保留 `run`、`selection`、`build`、`preparation`、`layout`、`lang`。
- 新增验收：默认验收增加“未触发记忆任务”的轻量断言；`make verify CHANGE=2026-11-25-update-temporal-memory` 追加三个核心场景（按轮查看与回链、角色实际读取前序记忆、重复提交与中断/暂不可用恢复），并断言 `calls.real=0`、材料快照未改写、跨平台/跨运行记忆隔离。

## Capabilities

### New Capabilities

- `simulation-temporal-memory`：运行级图谱隔离、行动与记忆/episode/事件映射、按轮截断视图、角色行动前记忆读取、增量投影、来源分层、重复提交幂等、写入未知与中断恢复、图服务暂不可达状态、记忆接口访问边界。

### Modified Capabilities

- `dual-platform-run`：运行详情增加记忆状态摘要；记忆同步不改变动作、轮次、计数和运行终态；旧运行与重跑运行的记忆各自可读。
- `background-job-queue`：新增 `simulation.memory` 任务类型与按 `simulationId` 的操作键、三步同步事务、租约与接手；记忆任务不得写入行动或轮次。
- `project-workbench`：第三阶段增加轮次时间线、运行时记忆图、来源图例、原行动回链、同步状态、刷新与迟到响应守卫。
- `reproducible-verification`：新增 Change 10 离线验收场景、`temporal_memory_evidence` 报告字段、零真实外呼与 CI 独立 artifact 断言。

## Impact

- 代码：新增记忆持久化与投影服务、运行图谱绑定、记忆读取注入、记忆同步 worker 分支、记忆读取 API、第三阶段记忆面板与状态模块；复用现有租约、步骤事务、事件流、图谱 provider 与 `run_events`，不改变 Change 06 探针与 Change 07 建图语义。
- 数据库：新增 `0015_temporal_memory.sql`，扩展 `runs.kind` 加入 `simulation.memory`，新增运行图谱绑定、记忆条目、记忆 episode、运行图节点/边/来源、行动记忆读取、轮次计划与记忆同步映射；旧表数据保持不变。
- 接口：新增按轮记忆读取与记忆重试接口；运行详情增加 `memory` 摘要。全部复用 `resolve_owned_project`，匿名 401、他人/不存在项目 404、他人/不存在运行 404、非法轮次 400、尚未到达的轮次 409、数据库不可用 503。
- 配置：新增 `SIMULATION_MEMORY_*`（provider、预算、引用上限、节点/边上限、重试与读回预算）；默认使用 stub 图谱且零真实外呼，真实 Zep 只在显式配置下启用。
- 验收：默认 `make verify` 只断言记忆未触发；Change 10 专属验收使用原创材料、stub 图谱与替身引擎，报告写 `temporal_memory_evidence`，CI 新增独立 Change 10 离线 job 与 artifact。
- 非目标：不实现第四阶段报告、第五阶段深度互动、真实外网 Twitter/Reddit、账号凭据、跨项目复用运行记忆、改写或重新发布冻结材料快照、把私有 25 页报告或私有截图写入仓库/夹具/报告/CI 产物。
