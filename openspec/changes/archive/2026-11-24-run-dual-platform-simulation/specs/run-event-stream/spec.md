# Spec Delta

## ADDED Requirements

### Requirement: 模拟运行事件与轮次提交同事务推进

双平台运行 SHALL 复用现有 run 事件流与任务事件订阅端点。每条运行事件 MUST 与该平台轮次记录、该轮动作、按平台计数和运行状态在同一事务内提交，MUST 只在提交后推送。事件 MUST 携带 `simulationId`、`selectionId`、`buildId`、`snapshotId`、`roundIndex`、`totalRounds`、`platformKey`、`nativePlatform`、`roundsCompleted`、`actionCount`、`nativeActionCounts`，终态事件 MUST 携带最终计数与原因码；`simulatedAt` MUST 表示该轮的模拟时刻。

事件 MUST NOT 携带动作正文、角色画像正文、材料正文、完整引文、引擎路径、密钥或凭据；动作正文只通过受鉴权的运行详情与动作接口读取。运行事件 MUST NOT 混入测试任务面板、测试副作用计数或建图/准备事件载荷。

刷新与重连 MUST 统一为“先运行详情、后事件”：先读取运行详情取得当前阶段、轮次与计数；已终态则直接渲染并停止订阅；否则再用 `Last-Event-ID` 优先、`after` 次之补齐事件。客户端 MUST 用 `runId` 与 `selectionId` 绑定当前展示，与当前运行不匹配的迟到事件或详情响应 MUST 被丢弃。终态事件与运行详情冲突时，以运行详情为准。

#### Scenario: 每平台每轮提交一条事件

- **WHEN** 某平台提交一轮
- **THEN** 提交一条带运行、选择、平台、轮次、模拟时刻与累计计数的事件
- **AND** 事件与该轮记录、动作和计数同事务
- **AND** 事件不包含动作正文

#### Scenario: 终态事件与结果一致

- **WHEN** 运行提交 `completed`、`failed` 或 `canceled` 终态事件
- **THEN** 该事件与该运行的终态状态、结束时间与最终计数同事务提交
- **AND** 客户端收到终态后读取的运行详情与事件中的计数一致
- **AND** 终态后不再出现新的进度事件

#### Scenario: 刷新与重连先读详情再补事件

- **WHEN** 客户端在收到若干事件后断线，随后用 `Last-Event-ID` 重连
- **THEN** 客户端先读取运行详情，取得阶段、轮次与计数
- **AND** 再从游标之后补齐已提交事件
- **AND** 已显示的事件不重复，行动流不从事件载荷重建

#### Scenario: 迟到的旧运行事件被丢弃

- **WHEN** 用户已切换到同一选择的新运行或另一选择，旧运行的进度事件才到达
- **THEN** 客户端因 `runId` 或 `selectionId` 不匹配丢弃该事件
- **AND** 当前时钟、计数与行动流仍属于新运行

#### Scenario: 事件订阅先校验归属

- **WHEN** 匿名用户或他人项目请求运行任务的事件流
- **THEN** 匿名返回 401，他人项目返回 404 `项目不存在`
- **AND** 在发送任何事件字节前完成校验，不建立事件流
