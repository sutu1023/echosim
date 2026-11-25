# Spec Delta

## ADDED Requirements

### Requirement: 运行详情返回记忆摘要且记忆状态不改变动作终态

运行详情 SHALL 在 Change 09 的字段之外返回 `memory` 摘要：`status`、待处理 episode 数、未确认 episode 数、最后已同步轮次、可选原因码与可空 `memoryJobId`。摘要 MUST 由该 `runId` 的持久化记忆、episode 与同步任务派生，MUST NOT 由前端计数、节点数或事件条数推算。

记忆状态 MUST 与运行终态分离：`memory_status` 为 `pending`、`syncing`、`partial`、`unconfirmed`、`unavailable` 或 `unconfigured` 时，运行的 `phase`、轮次、动作、平台计数、`failedPlatforms` 与终态事件 MUST 保持 Change 09 已提交的值。记忆同步完成或失败 MUST NOT 追加模拟运行的第二个终态事件，MUST NOT 改写已终态运行的动作或计数。

同一冻结选择重跑时，新运行 MUST 返回自己的 `memory.graphId` 摘要与记忆计数；旧运行的 `memory` 摘要、轮次图与来源 MUST 仍可按其 `runId` 读取，不受新运行影响。

#### Scenario: 运行完成后记忆仍在同步

- **WHEN** 三轮动作已完成并进入 `succeeded`，但仍有 episode 未处理
- **THEN** 详情返回 `phase=succeeded` 与 `memory.status=pending|partial`
- **AND** 动作、轮次与平台计数保持完整
- **AND** 不因记忆未完成而追加失败终态或清空动作

#### Scenario: 记忆同步失败不改变运行终态

- **WHEN** 记忆同步任务以 `memory_unavailable` 或 `write_unconfirmed` 结束
- **THEN** 模拟运行的 `phase`、动作、轮次、计数与终态事件不变
- **AND** 详情返回记忆原因码与待处理/未确认计数
- **AND** 旧运行仍可按 `runId` 读取

#### Scenario: 重跑运行的记忆摘要各自独立

- **WHEN** 同一 `selectionId` 的第二次运行完成
- **THEN** 第二次运行的详情返回自己的记忆计数与 `memoryJobId`
- **AND** 第一次运行的记忆计数、最后同步轮次与来源不变
- **AND** 两次运行的 `memoryId` 与 `episodeUuid` 不混入对方详情

### Requirement: 按轮记忆视图与行动回链复用运行身份与平台映射

按轮记忆视图 MUST 以 `runId` 为运行身份，以 `platform_a/twitter` 与 `platform_b/reddit` 为平台映射；MUST NOT 接受调用方传入任意平台或原生平台别名。行动回链 MUST 返回 `actionId`、`platformKey`、`roundIndex`、`simulatedAt`、`sourceEventSeq` 与原行动摘要，且该行动 MUST 属于同一 `runId`。

视图中的目标或来源 MUST 只能解析到同一运行、同一平台内已提交的行动；跨运行、跨平台、已删除或不存在于本运行的目标 MUST 标记未解析并返回原因码 `target_unresolved`，MUST NOT 建立链接或伪造原行动。记忆视图的轮次 MUST 与 `simulation_rounds` 的已提交轮次一致，MUST NOT 用当前动作分页或前端时间线推算。

#### Scenario: 评论来源回到同一平台原行动

- **WHEN** 读取平台 B 第 2 轮评论的运行图来源
- **THEN** 来源返回同一 `runId`、`platformKey=platform_b` 的 `actionId`
- **AND** 页面可显示该行动的角色、内容、轮次、模拟时间与原事件序号
- **AND** 不跳转到平台 A 或另一运行的同名行动

#### Scenario: 跨运行目标不链接

- **WHEN** 运行图中出现指向旧运行 `actionId` 或另一 `runId` 的来源
- **THEN** 该来源标记 `target_unresolved`
- **AND** 不建立跳转、不改写目标行动、不伪造一条原行动
- **AND** 当前运行的记忆计数不受影响

#### Scenario: 平台映射不接受任意值

- **WHEN** 记忆视图请求传入 `platform=twitter`、`platform=reddit` 或未知平台
- **THEN** 返回 400 `platform_invalid`
- **AND** 不按原生平台名猜测平台别名
- **AND** 不读取另一平台的记忆
