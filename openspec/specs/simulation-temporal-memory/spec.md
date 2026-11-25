# simulation-temporal-memory Specification

## Purpose

定义第三阶段推演的运行时序记忆：把每条已确认行动映射为可追溯的记忆与 episode，按轮截断提供运行图，让角色只读取同运行、同平台、早于当前轮的已提交记忆，并为重复提交、中断接手、写入未知与图服务暂不可达保留可恢复、可核对的持久状态。材料事实保持只读，角色发言与推演结果分别保留来源。

## Requirements

### Requirement: 运行级图谱隔离与冻结材料快照只读

每个 `simulation.run` SHALL 绑定一份只属于本次运行的时序记忆图谱。绑定 MUST 在调用图谱服务前持久化本次运行的创建幂等键与稳定请求参数；图谱创建、响应丢失重试、worker 接手与 episode 写入 MUST 复用同一个 `simulationId` 派生的幂等键，MUST NOT 因超时、连接中断或搜索未命中而换键、改请求参数或改用别的图谱。

运行图谱 MUST NOT 复用项目证据图谱、任何旧 build 图谱、其他运行的图谱或 `ZEP_ACCEPTANCE_GRAPH_ID`。同一冻结选择再次开始运行 MUST 创建新的 `simulationId`、新的运行图谱绑定与新的 episode 命名空间；旧运行的 `graph_id`、episode、节点、边、来源与按轮视图 MUST 保持可读且不被新运行改写。

冻结材料快照 MUST 只读：读取运行图可以引用 `graph_snapshots`、`graph_nodes`、`graph_edges` 与 `graph_evidence` 的 `snapshotId`/`nodeKey`/`edgeKey`/来源字段，但 MUST NOT 新增、更新、删除或重新发布这些记录，MUST NOT 改变其 `published`、计数、来源或哈希。

#### Scenario: 同一冻结选择重跑各自建图

- **WHEN** 所有者用同一个 `selectionId` 在首次运行终态后再次开始运行
- **THEN** 第二次运行返回新的 `runId` 与新的 `simulationId`
- **AND** 第二次运行创建新的运行图谱绑定与新的 `graph_id`
- **AND** 两次运行的 `memoryId`、`episodeUuid`、节点/边来源集合互不重叠

#### Scenario: 旧运行图保持可读

- **WHEN** 第二次运行完成后读取第一次运行的 `round` 视图与记忆来源
- **THEN** 第一次运行的轮次、记忆、episode、节点、边与来源保持不变
- **AND** 不出现第二次运行的记忆或节点混入第一次运行

#### Scenario: 图谱创建响应丢失只绑定一个运行图谱

- **WHEN** 首次运行图谱创建超时或连接中断，但服务端实际已创建
- **THEN** 接手或重试使用原 `simulationId` 派生的同一幂等键与原请求参数
- **AND** 最终只保存一个 `graph_id`，不创建第二个运行图谱

#### Scenario: 运行不污染项目证据图谱

- **WHEN** 一次双平台运行完成并写入运行时记忆
- **THEN** 项目证据图谱的绑定、build、快照、节点、边与来源行数不变
- **AND** 运行记忆只出现在该 `simulationId` 的绑定与记忆表中

#### Scenario: 冻结材料快照不被改写

- **WHEN** 运行前后比较冻结选择的 `snapshotId`、节点数、边数、来源数与快照内容哈希
- **THEN** 所有值完全一致
- **AND** 运行图只通过来源引用读取材料事实，不写入材料快照表

### Requirement: 行动、记忆、episode 与原运行事件的固定对应

每条已确认的 `simulation_action` MUST 在同一记账事务中产生恰好一条 `simulation_memory_entry` 与一条 `simulation_memory_episode` 计划。记忆 MUST 保存 `actionId`、`platformKey`、`roundIndex`、`actionSeq`、`actorRoleId`、`simulatedAt`、该轮提交事件在 `run_events` 中的 `seq`，并 MUST 使用由 `simulationId` 与 `actionId` 派生的稳定 `memoryId`。episode MUST 保存由同一对标识派生的稳定幂等键与内容哈希。

同一轮的多条行动 MUST 各自拥有独立的 `actionId`、`memoryId` 与 `episodeUuid`，但 MAY 共享该轮事件的 `sourceEventSeq`。任何按轮读取、按行动回链或按 episode 查账都 MUST 能通过 `actionId → memoryId → episodeUuid → sourceEventSeq` 对上原行动与模拟时间；MUST NOT 用轮次摘要、动作调用返回值或前端序号替代这些标识。

#### Scenario: 第 2 轮评论的完整映射

- **WHEN** 平台 B 第 2 轮有一条已确认的 `create_comment`
- **THEN** 该行动有唯一 `actionId`、`memoryId`、`episodeUuid` 与 `sourceEventSeq`
- **AND** 记忆中的 `roundIndex=2`、`simulatedAt` 等于该轮模拟时刻
- **AND** `sourceEventSeq` 指向写入该轮行动、计数与记忆的同一事务事件

#### Scenario: 同一轮多条行动各自可定位

- **WHEN** 同一平台同一轮包含发帖、评论与点赞三条行动
- **THEN** 三条行动分别拥有不同 `actionId`、`memoryId` 与 `episodeUuid`
- **AND** 三条记忆的 `sourceEventSeq` 相同
- **AND** 读取该轮图时三条来源都能各自回到对应行动，不会互相覆盖

#### Scenario: 记忆与轮次、模拟时间一致

- **WHEN** 读取任一条行动记忆
- **THEN** 其 `platformKey`、`roundIndex`、`actionSeq` 与 `simulation_actions` 完全一致
- **AND** 其 `simulatedAt` 与 `simulation_rounds` 中该平台该轮的模拟时刻完全一致
- **AND** 不出现记忆指向另一运行、另一平台或另一轮次

### Requirement: 按轮运行图具有确定的截止语义

按轮运行图接口 SHALL 接受 `round` 与可选 `platform`，返回“第 R 轮结束后的运行图”。运行记忆 MUST 按 `round_index <= R` 截断；节点或边 MUST 在其至少一个来源记忆满足 `round_index <= R` 时纳入；MUST NOT 因当前运行已推进到更晚轮次而改变同一 `runId`+`round`+`platform` 的返回集合。

不指定 `platform` 时，接口 MAY 同时返回两个平台的运行记忆，但每条记忆、节点来源与边来源 MUST 保留自己的 `platformKey` 与 `roundIndex`。指定 `platform` 时，MUST 只返回该平台的运行记忆。MUST NOT 把另一平台更晚轮次的记忆冒充为当前平台的更早轮次。

`round` MUST 是 `1..simulation.rounds` 的整数；非法值 MUST 返回 400 `round_invalid`，且 MUST NOT 访问图谱服务。指定平台已完成轮次小于 `round` 时 MUST 返回 409 `round_not_available` 并带可用最大轮次；不指定平台时至少一个平台已完成 `round` 才返回 200，否则 409。已到达但记忆投影未完成时 MUST 返回 200 与明确的状态，不得用 409 或空图冒充完成。

#### Scenario: 第 1 轮图查不到第 2 轮评论

- **WHEN** 读取平台 B 的 `round=1` 运行图
- **THEN** 响应中不含第 2 轮评论的 `memoryId`、`actionId` 或 `episodeUuid`
- **AND** 第 1 轮及更早的已处理记忆仍正常返回

#### Scenario: 第 2 轮与第 3 轮图能查到同一评论

- **WHEN** 分别读取同一运行的 `round=2` 与 `round=3`
- **THEN** 两次响应都包含第 2 轮评论的同一 `memoryId`、`actionId` 与 `episodeUuid`
- **AND** 第 3 轮图额外包含第 3 轮已处理记忆，不删除第 2 轮记忆

#### Scenario: 另一平台未来轮次不冒充当前平台

- **WHEN** 平台 A 已完成第 3 轮而平台 B 只完成第 2 轮，读取平台 B 的 `round=2`
- **THEN** 响应只含平台 B 第 1–2 轮记忆
- **AND** 不含平台 A 第 3 轮或任何 `platformKey=platform_a` 的记忆
- **AND** 每条返回记忆的 `platformKey` 都是 `platform_b`

#### Scenario: 非法轮次与尚未到达的轮次分开

- **WHEN** 请求 `round=0`、`round` 非整数或 `round` 大于冻结轮数
- **THEN** 返回 400 `round_invalid`
- **AND** 不读取图谱服务、不返回部分图
- **WHEN** 请求的 `round` 合法但指定平台尚未完成该轮
- **THEN** 返回 409 `round_not_available` 与 `availableMaxRound`
- **AND** 不把尚未发生的轮次显示为空图

#### Scenario: 刷新后同一轮次视图稳定

- **WHEN** 页面刷新后以相同 `runId`、`round` 与 `platform` 再次请求
- **THEN** 已处理节点、边、来源与状态与刷新前一致
- **AND** 不因当前运行推进或事件重连而漂移到其他轮次

### Requirement: 角色在行动前读取同平台前序记忆并留下执行入口证据

角色开始第 R 轮行动前 SHALL 读取记忆；读取范围 MUST 严格限定为同一 `runId`、同一 `platformKey`、`round_index < R` 的已提交行动记忆，加上冻结材料事实。MUST NOT 读取第 R 轮本身、同一轮其他角色、另一平台任意轮次或任何 `round_index >= R` 的记忆。

每次实际读取 MUST 在行动执行入口留下可核对的记忆标识：轮次计划 MUST 持久化 `memoryRefs`（至少含 `memoryId`、`actionId`、`roundIndex`、`simulatedAt`、可空 `episodeUuid`、可空 `materialRef`），引擎请求 MUST 由该计划派生；行动记账后 MUST 写入 `simulation_action_memory_reads`，以 `(actionId, memoryId)` 唯一。只证明查询接口能返回节点或关系 MUST NOT 视为角色已读取。

同一行动重复提交 MUST NOT 新增重复的读取记录。前序记忆的图谱投影可尚未完成，但该记忆 MUST 已由行动事务提交；未提交或 `write_unconfirmed` 的记忆 MUST NOT 进入角色读取。

#### Scenario: 第 3 轮角色只读到前两轮同平台记忆

- **WHEN** 平台 B 第 3 轮行动开始
- **THEN** 其 `memoryRefs` 只包含平台 B 第 1–2 轮已提交记忆与材料事实
- **AND** 不含平台 B 第 3 轮、平台 A 任意轮次或 `round_index=3` 的行动记忆
- **AND** `simulation_action_memory_reads` 中的 `memoryId` 与计划 `memoryRefs` 完全一致

#### Scenario: 平台独立推进不泄漏未来行动

- **WHEN** 平台 A 已完成第 3 轮而平台 B 刚开始第 2 轮
- **THEN** 平台 B 第 2 轮角色的 `memoryRefs` 不含平台 A 第 3 轮记忆
- **AND** 也不含平台 B 第 2 轮已执行的其他行动
- **AND** 平台 A 第 3 轮行动仍可读取自己的前两轮记忆

#### Scenario: 执行入口证据不是查询 API 结果

- **WHEN** 验收只读取 `GET /memory` 而不读取轮次计划或行动读取记录
- **THEN** 该证据不足以证明角色实际读取
- **AND** 验收 MUST 在 `simulation_round_plans.request_payload.actions[].memoryRefs` 或引擎请求中核对同一组 `memoryId`
- **AND** 该组标识 MUST 能与 `simulation_action_memory_reads` 对上

#### Scenario: 投影未完成的前序记忆仍可被读取

- **WHEN** 第 1 轮行动已提交，但其 episode 仍为 `planned` 或 `in_flight`
- **THEN** 第 2 轮角色仍可读取第 1 轮的已提交本地记忆
- **AND** 该记忆在图谱视图中标为未处理，不伪造节点或关系
- **AND** 未提交或被标为 `write_unconfirmed` 的记忆不出现在 `memoryRefs`

#### Scenario: 没有前序记忆时正常行动

- **WHEN** 平台第 1 轮角色开始行动且只有材料事实
- **THEN** `memoryRefs` 可以只含材料引用或不含行动记忆
- **AND** 行动仍按冻结选择与轮次计划执行，不因空记忆失败

### Requirement: 记忆增量投影、状态可见与图读回

运行期间 SHALL 在每平台每轮记账后增量投影该轮行动记忆：按稳定幂等键写入 episode、确认 `processed`、按本次 episode 集合读回节点与边并写入运行图来源。投影 MUST 不阻塞另一平台、不阻塞下一轮行动，也不改变该轮动作、计数与事件。

只有 `processed` 的 episode 才能贡献运行图节点与边。`planned`、`in_flight`、`unconfirmed`、`failed` 的记忆 MUST 保留可见状态，但 MUST NOT 贡献节点、边、来源计数或“已完成”状态。运行详情 MUST 返回记忆状态摘要：`memory_status`、待处理 episode 数、未确认 episode 数、最后已同步轮次、可选原因码与记忆任务标识。

`memory_status` MUST 至少区分：`unconfigured`（未配置图谱，不发起外呼）、`pending`（待同步）、`syncing`（同步中）、`complete`（全部已处理并读回）、`partial`（部分轮次可用但仍有未完成）、`unconfirmed`（至少一个写入结果未知）、`unavailable`（图服务暂不可达）。该状态 MUST NOT 改变运行 `phase`、动作计数或终态事件。

#### Scenario: 运行图按轮增长

- **WHEN** 一次三轮运行逐轮完成且图谱投影成功
- **THEN** 每轮结束后已处理记忆数、节点数与边数按该轮新增
- **AND** 第 3 轮图包含第 1–3 轮已处理记忆，不删除前两轮节点或边
- **AND** 页面刷新后仍能读到同一增长结果

#### Scenario: 未处理记忆不伪造节点

- **WHEN** 某条记忆的 episode 仍为 `planned` 或 `in_flight`
- **THEN** `GET /memory` 可以返回该记忆条目与 `graphState`
- **AND** 运行图节点/边集合不包含该记忆贡献的对象
- **AND** 状态显示为 `pending` 或 `partial`，不显示为 `complete`

#### Scenario: 图服务暂不可达时动作继续

- **WHEN** 图谱服务不可达而某轮行动已成功写入引擎并记账
- **THEN** 运行详情仍返回该轮行动、轮次与计数
- **AND** `memory_status` 为 `unavailable`，待处理 episode 数大于 0
- **AND** 页面显示“记忆同步暂不可用”，不把运行标为完成记忆

#### Scenario: 未配置图谱时零外呼

- **WHEN** 默认配置没有 Zep 且未允许 stub
- **THEN** `memory_status=unconfigured`
- **AND** 不发起任何图谱或模型外部调用
- **AND** 运行行动、轮次与计数仍正常完成

### Requirement: 重复提交不增长记忆、图节点、边或来源

同一已确认行动被重复提交、重复投影或重复同步时，系统 MUST 以 `(simulationId, actionId)`、`(simulationId, memoryId)`、episode 幂等键与内容哈希识别同一逻辑写入，并 MUST 只保留一条记忆、一条 episode、同一组节点、边与来源。重复提交 MUST NOT 增加 `simulation_actions`、`simulation_rounds`、平台计数或 `run_events` 序号，MUST NOT 产生第二个 `episodeUuid`。

运行图节点 MUST 按 `(simulationId, node_key)` 归并，边 MUST 按 `(simulationId, edge_key)` 归并；来源 MUST 按 `(simulationId, targetKind, targetKey, memoryId, facet)` 唯一。同一行动重复读回只能补充尚未记录的状态或来源，MUST NOT 追加重复来源或重复计数。

#### Scenario: 同一行动提交两次不增长

- **WHEN** 对已确认的第 2 轮评论触发两次记忆投影
- **THEN** `simulation_memory_entries`、`simulation_memory_episodes`、节点、边与来源计数都与一次提交相同
- **AND** 该行动仍只有一个 `memoryId` 与一个 `episodeUuid`
- **AND** 动作计数、轮次计数与事件序号不变

#### Scenario: 重复同步不重复节点与关系

- **WHEN** `simulation.memory` 任务对同一批 episode 连续同步两次
- **THEN** 第二次只校验既有状态，不新增节点、边或来源
- **AND** 节点/边计数与来源计数保持不变
- **AND** `attempts` 可以增加，但 `episodeUuid` 不改变

#### Scenario: 并发重复提交只留一份

- **WHEN** 两个 worker 或两个同步任务同时尝试写入同一 `memoryId`
- **THEN** 数据库唯一约束只允许一行记忆与一行 episode
- **AND** 后到者观察到既有记录并按查账结果继续，不产生重复图谱对象
- **AND** 已提交的动作、轮次、计数与事件不被改写

### Requirement: 写入未知、中断接手与图服务恢复

episode 写入 MUST 区分“未尝试”“可能已受理”“明确可重试拒绝”“已受理”与“结果未知”。调用图谱服务前 MUST 先把本次尝试持久化为 `in_flight` 并递增 `attempts`，再使用已持久化的幂等键与内容发起写入。响应丢失、连接中断或返回空 UUID 时 MUST 先按 `simulationId`、`actionId`、`memoryId`、episode 幂等键与内容哈希查账；命中 MUST 补记 `episodeUuid`，MUST NOT 再次 `add`；未命中 MUST 在预算内继续查账，MUST NOT 换键或改写内容。

确认预算耗尽仍无法匹配时 MUST 判定 `write_unconfirmed`，保留在数据库与页面可见状态，MUST NOT 把该记忆贡献为节点、边、来源或角色可读的已提交记忆。worker 中断或租约过期后，接手 MUST 先查账再继续，从最早的未完成记忆（按 `roundIndex`、`actionSeq`、`platformKey` 排序）恢复，MUST NOT 从第 1 轮重写或新建第二个 episode。

图服务暂不可达时，行动与本地记忆 MUST 继续提交；恢复后 `simulation.memory` 任务 MUST 从最早未完成 episode 继续，复用原幂等键、原图谱与原内容。重试入口 MUST 幂等：活动同步任务存在时返回 200 与同一 `memoryJobId`，否则创建新任务并返回 202；重试 MUST NOT 改变运行终态、动作、轮次或计数。

#### Scenario: 服务端已受理但本地未确认时中断

- **WHEN** episode 写入已被服务端受理，但 worker 在保存 `episodeUuid` 前中断，随后由新 worker 接手
- **THEN** 接手先按幂等键、`actionId` 与内容哈希查账
- **AND** 命中后补记同一 `episodeUuid`，不再次写入
- **AND** 最终只有一条 episode，节点、边与来源不重复

#### Scenario: 首次查账未命中不换键重写

- **WHEN** 响应丢失后第一次查账尚未返回本次 episode
- **THEN** 状态保持 `in_flight` 或等待确认
- **AND** 继续使用原幂等键与原内容查账
- **AND** 不生成新键、不改写内容、不重复 `add`

#### Scenario: 结果仍未知时保留可见状态

- **WHEN** 确认预算耗尽仍无法确认写入结果
- **THEN** 该记忆状态为 `write_unconfirmed`，运行 `memory_status=unconfirmed`
- **AND** 页面显示“写入待确认”，不显示为完成
- **AND** 该记忆不贡献节点/边，也不进入角色读取
- **AND** 后续恢复仍使用原幂等键，不新建替代 episode

#### Scenario: 图服务恢复后从最早未完成记忆继续

- **WHEN** 图服务不可达导致部分轮次记忆未完成，随后服务恢复并创建或领取记忆同步任务
- **THEN** 任务从最早的未完成 `memoryId` 开始，而不是从第 1 轮或最新轮开始
- **AND** 已完成的记忆被跳过，未完成的复用原键与原内容
- **AND** 最终 `memory_status` 回到 `complete` 或明确 `partial`，动作与计数不变

#### Scenario: 记忆重试不改变运行终态

- **WHEN** 运行已 `succeeded` 但 `memory_status` 为 `pending`、`unconfirmed` 或 `unavailable`，用户请求记忆重试
- **THEN** 返回 202 或 200 与记忆任务标识
- **AND** 运行 `phase`、动作、轮次、平台计数与终态事件保持不变
- **AND** 记忆任务只更新 episode、节点、边、来源与 `memory_status`

### Requirement: 材料事实、角色发言与推演结果分别保留来源

运行图 SHALL 区分 `origin=material`、`origin=role` 与 `origin=simulation` 三类来源。材料事实 MUST 引用冻结快照的 `snapshotId` 与 `nodeKey`/`edgeKey`（以及可用的 `documentId`、页码或 UTF-16 区间），MUST NOT 被运行图复制成新的材料记录或改写原快照。

角色发言 MUST 引用 `memoryId`、`actionId`、`actorRoleId`、发言正文、`episodeUuid`、`sourceEventSeq` 与模拟时间；推演结果 MUST 引用 `memoryId`、`actionId`、原生动作、目标动作/引擎记录、`episodeUuid`、`sourceEventSeq` 与模拟时间。二者即使来自同一行动，也 MUST 以不同 `facet` 保留，MUST NOT 合并成一条无法区分“说了什么”与“做了什么”的来源。

节点或边可以按规范化名称与关系类型归并，但来源 MUST 分别保留；同一来源重复出现 MUST 去重。点击节点或边的来源 MUST 回到同一 `runId`、同一 `platformKey` 的原行动；跨运行、跨平台或不存在于本运行的目标 MUST 标记为未解析，MUST NOT 建立跳转。

#### Scenario: 三类来源分别可见

- **WHEN** 读取一条由材料主体、角色发言与评论动作组成的运行图
- **THEN** 材料层节点/边带 `snapshotId` 与材料键
- **AND** 角色发言来源带 `memoryId`、`actionId`、`actorRoleId`、发言正文与 `episodeUuid`
- **AND** 推演结果来源带 `memoryId`、`actionId`、原生动作、目标与 `episodeUuid`
- **AND** 三类来源在图例与详情中可区分

#### Scenario: 运行图不改写材料快照

- **WHEN** 一次运行读取并展示材料节点后再次查询冻结快照
- **THEN** 材料快照的 `snapshotId`、节点、边、来源与发布状态完全不变
- **AND** 运行图新增节点/边只存在于该运行记忆表

#### Scenario: 来源回链只指向同一运行同一平台

- **WHEN** 点击第 2 轮评论的运行图来源
- **THEN** 返回的 `actionId` 属于同一 `runId` 与同一 `platformKey`
- **AND** 显示该行动的轮次、模拟时间、原事件序号、角色与目标
- **AND** 指向另一运行或另一平台的 `actionId` 不会被解析或跳转

#### Scenario: 同轮多来源不互相覆盖

- **WHEN** 同一轮的发帖、评论与点赞都贡献同一角色节点
- **THEN** 该节点保留三条不同的 `memoryId`/`actionId` 来源
- **AND** 每条来源仍能回到各自行动
- **AND** 节点计数只增加一次，不按来源数重复计算

### Requirement: 记忆读取接口沿用项目与运行访问边界

所有记忆读取、记忆重试与含记忆摘要的运行详情接口 MUST 复用 `resolve_owned_project`，顺序固定为“身份 → 项目归属 → 运行归属”。匿名请求 MUST 返回 401 且不访问记忆、运行或图谱数据；他人项目、不存在项目与非法项目标识 MUST 返回相同 404 `项目不存在`；他人或不存在运行 MUST 返回相同 404 `运行不存在`。数据库不可达但会话存在时 MUST 返回 503 且不清除 Cookie。

错误响应 MUST NOT 包含材料正文、完整 episode 正文、运行内部路径、密钥或来源地址。`round` 非法 MUST 在访问图谱服务前返回 400 `round_invalid`；合法但尚未到达的轮次 MUST 返回 409 `round_not_available` 与可用最大轮次。图服务不可达 MUST NOT 把记忆接口变成 500 或伪造空完成图；应返回已提交的本地可见数据与 `memory_status=unavailable`。

#### Scenario: 匿名访问记忆接口

- **WHEN** 未携带会话 Cookie 请求按轮记忆、记忆重试或运行详情
- **THEN** 返回 401
- **AND** 不读取运行、记忆、episode 或图谱数据

#### Scenario: 他人项目与不存在运行统一 404

- **WHEN** 用户 B 请求 A 项目的记忆视图，或请求一个不存在的 `runId`
- **THEN** 前者返回 404 `项目不存在`
- **AND** 后者返回 404 `运行不存在`
- **AND** 两者都不包含记忆正文、角色、路径或内部标识

#### Scenario: 停库时保留会话

- **WHEN** 数据库不可达时携带会话请求记忆视图
- **THEN** 返回 503
- **AND** 响应不清除 Cookie
- **AND** 数据库恢复后同一会话可继续读取同一运行记忆

#### Scenario: 图服务不可达不是 500

- **WHEN** 数据库正常但图谱服务不可达，请求已到达轮次的记忆视图
- **THEN** 返回 200 与已提交的本地记忆、材料引用和 `memory_status=unavailable`
- **AND** 不返回 500，不把未处理记忆画成节点或边
- **AND** 响应包含待处理计数与稳定原因码
