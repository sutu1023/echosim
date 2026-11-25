# Spec Delta

## ADDED Requirements

### Requirement: Change 10 时序记忆离线验收

验收脚本 MUST 注册变更 `2026-11-25-update-temporal-memory`，默认 `make verify` MUST 增加“未触发时序记忆”的轻量断言：无模拟运行、无 `simulation.memory` 任务、`temporal_memory_evidence.mode="not-triggered"` 且 `calls.real=0`。未注册的 `CHANGE` MUST 继续以退出码 2 结束。

`make verify CHANGE=2026-11-25-update-temporal-memory` MUST 在同一 SHA 上使用原创材料、stub 图谱与替身引擎执行至少三个核心场景：

1. **按轮查看与回链**：选择第 2 轮的一条评论，断言第 1 轮图不含该 `actionId`/`memoryId`，第 2、3 轮图含；断言 `actionId`、`platformKey`、`roundIndex`、`simulatedAt`、`sourceEventSeq`、`memoryId`、`episodeUuid` 的完整映射；断言同轮多条行动各自可定位；断言材料、角色发言、推演结果来源分别可读；断言冻结材料快照的行数、计数与哈希不变。
2. **角色实际读取前序记忆**：在第 3 轮行动的实际执行入口读取 `simulation_round_plans.request_payload.actions[].memoryRefs` 与 `simulation_action_memory_reads`，断言只含同运行、同平台、`round_index < 3` 的记忆与材料事实；断言平台 A 第 3 轮与平台 B 第 2 轮并发时平台 B 第 2 轮不含任何未来或另一平台记忆；断言同一冻结选择重跑得到新 `runId`/`graphId`，两次运行记忆无交集，旧运行仍可读。
3. **重复提交与中断恢复**：对同一已确认行动重复投影/同步，断言记忆、episode、节点、边、来源计数与动作/轮次/事件序号不增长；在“服务端已写入、本地未确认”时中断 worker，断言接手 `attempts>1`、先查账补记、只保留一个 episode；在结果未知时断言 `write_unconfirmed`/`memory_status=unconfirmed` 可见且未换键；在图服务暂不可达时断言动作继续、`memory_status=unavailable`、待同步数可见，恢复后从最早未完成 `memoryId` 继续。

验收 MUST 覆盖访问边界：匿名 401、他人项目 404、不存在运行 404、非法轮次 400、尚未到达轮次 409、停库 503 保留 Cookie、跨运行/跨平台来源不链接。MUST 断言 `temporal_memory_evidence.calls.real=0`、`calls.double>0`、`snapshotUntouched=true`、两次运行 `overlap=0`、重复提交 `growth=0`、接手 `reconciled=true`、未知状态 `visible=true && newKeyCreated=false`。连续两次执行 MUST 通过。

#### Scenario: 默认验收不触发记忆任务

- **WHEN** 执行不带 `CHANGE` 的 `make verify`
- **THEN** 不创建模拟运行或记忆同步任务
- **AND** `temporal_memory_evidence.mode="not-triggered"`
- **AND** `calls.real=0` 且不读取私有材料

#### Scenario: 三场景在同一 SHA 上通过

- **WHEN** 执行 `make verify CHANGE=2026-11-25-update-temporal-memory`
- **THEN** 报告包含 `temporal-memory-round-view`、`temporal-memory-role-reads`、`temporal-memory-idempotency-recovery` 三个场景且均通过
- **AND** `tested_sha` 等于执行时 `HEAD`
- **AND** 报告 `result=passed` 且 `dirty=false`（干净克隆）

#### Scenario: 第 2 轮评论的时间截断与回链

- **WHEN** 读取第 1、2、3 轮运行图并点击第 2 轮评论来源
- **THEN** 第 1 轮 `contains=false`，第 2、3 轮 `contains=true`
- **AND** 回链的 `runId`、`platformKey`、`roundIndex`、`simulatedAt`、`sourceEventSeq`、`memoryId`、`episodeUuid` 与数据库一致
- **AND** 报告写入 `round1Contains`、`round2Contains`、`round3Contains` 与来源字段

#### Scenario: 角色入口读到且只读到前序同平台记忆

- **WHEN** 核对第 3 轮行动的 `memoryRefs` 与 `simulation_action_memory_reads`
- **THEN** 每个读取的 `memoryId` 都属于同一运行、同一平台且 `roundIndex < 3`
- **AND** 不含第 3 轮、另一平台或未来记忆
- **AND** `executionEntry` 记录为 `round-plan` 或引擎请求，而不是查询接口

#### Scenario: 重跑记忆隔离

- **WHEN** 用同一 `selectionId` 重跑并比较两次运行的记忆集合
- **THEN** `runId`、`graphId` 不同且 `overlap=0`
- **AND** 旧运行的第 2 轮图仍可读
- **AND** 新运行角色不读取旧运行记忆

#### Scenario: 重复提交不增长

- **WHEN** 对同一已确认行动触发两次记忆投影或同步
- **THEN** 记忆、episode、节点、边与来源计数在第二次后不变
- **AND** 动作、轮次、平台计数与事件序号不变
- **AND** 报告写入 `duplicate.before`、`duplicate.after` 与 `growth=0`

#### Scenario: 写入未知与图服务暂不可达可见

- **WHEN** 写入响应丢失后无法确认，或图服务暂不可达
- **THEN** 报告写入 `write_unconfirmed`/`memory_status`、待同步数、原因码与恢复起点
- **AND** 页面可见状态由浏览器走查记录补充
- **AND** 恢复复用原幂等键，`newKeyCreated=false`

#### Scenario: 边界与零真实外呼

- **WHEN** 执行匿名、他人项目、不存在运行、非法轮次、尚未到达轮次、停库与跨运行来源检查
- **THEN** 分别返回 401、404、404、400、409、503 与未解析来源
- **AND** 所有检查完成后 `calls.real=0`
- **AND** 不出现私有材料、正文、密钥或绝对路径

### Requirement: Change 10 报告字段与 CI 独立校验

验收报告 MUST 写入 `temporal_memory_evidence`，至少包含 `mode`、`graphProvider`、`calls`、`callScope`、`scenarios`、`isolation` 与 `workerRestored`。`calls.real` MUST 来自 provider 边界的逐运行持久化值，MUST NOT 由计划 episode 数、节点数或角色数推算。`callScope` MUST 列出被统计的 `runId`、`graphId`、episode/节点/边计数，便于与人工资格对齐。

CI MUST 保留默认 `make verify`，并新增独立的 Change 10 离线 job。Change 10 job MUST 使用与本地相同的 `make verify CHANGE=2026-11-25-update-temporal-memory`，上传独立 artifact 名称，并断言：`tested_sha` 等于触发 SHA、`dirty=false`、`result=passed`、`change=2026-11-25-update-temporal-memory`、`calls.real=0`、三个核心场景齐全、`snapshotUntouched=true`、`overlap=0`、`growth=0`、`reconciled=true`、未知状态可见且未换键。失败运行 MUST 同样上传报告，产物缺失 MUST 视为配置错误。

#### Scenario: 报告字段完整

- **WHEN** Change 10 验收完成
- **THEN** `runtime/verify/last-run.json` 含 `temporal_memory_evidence`
- **AND** `scenarios` 含三个核心场景，`isolation.runA`/`runB`/`overlap` 与 `workerRestored` 可读
- **AND** 报告不含私有材料正文、密钥或完整 episode 正文

#### Scenario: CI 断言 Change 10 场景

- **WHEN** CI 执行 Change 10 job
- **THEN** 断言 `calls.real=0`、三场景齐全、`snapshotUntouched=true`、`overlap=0`、`growth=0`
- **AND** 断言接手 `attempts>1` 且 `reconciled=true`
- **AND** 失败时仍上传独立 artifact

#### Scenario: 未知变更仍退出码 2

- **WHEN** 执行 `make verify CHANGE=2026-11-25-unknown`
- **THEN** 在任何运行、图谱或记忆任务前以退出码 2 拒绝
- **AND** 不创建 `simulation.memory` 任务或真实外呼
