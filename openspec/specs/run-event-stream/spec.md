# run-event-stream Specification

## Purpose
让 run 的业务事件先成为数据库中的持久事实，再以同域 SSE 推送给工作台，使断线、重连、刷新与进程重启后日志仍能按序号完整还原且不重复。

## Requirements

### Requirement: 先持久化再推送

run 事件 SHALL 与产生它的业务变更（步骤副作用、状态转换）在同一数据库事务中写入，之后才允许推送。SSE 推送 MUST 只读取已提交的事件，不得在推送路径上生成数据库之外的事件；推送失败或连接中断 MUST NOT 影响事件的持久化与任务继续执行。

#### Scenario: 断线期间事件仍进入数据库

- **WHEN** 客户端在收到 `seq=4` 后断开，worker 继续执行到终态
- **THEN** 数据库中该 run 的事件为 `seq` 1–10 且无缺口
- **AND** 断开期间没有任何事件因客户端不在线而丢失

#### Scenario: 推送失败不影响任务

- **WHEN** 事件订阅连接在任务执行中断开或报错
- **THEN** 任务继续执行并提交后续步骤
- **AND** 重新订阅时可用游标补齐断开期间的全部事件

### Requirement: 统一事件字段与 seq 语义

每条业务事件 SHALL 恰好包含 `runId`、`seq`、`type`、`stage`、`simulatedAt`、`payload` 六个字段。`seq` MUST 在同一 run 内从 1 开始、每次加 1 且不重复；`(runId, seq)` MUST 唯一。`type` MUST 限于 `progress`、`failed`、`canceled`、`completed` 四种。`simulatedAt` MUST 表示推演内的模拟时刻而非墙上时钟，使同一确定性任务重复执行得到相同取值。

确定性测试任务 MUST 总共产生 10 条业务事件：`seq` 1–9 为 `progress`，`seq` 10 为 `completed`；终态事件计入这 10 条，SSE 心跳与 `stream-end` 帧不计入。

#### Scenario: 事件字段完整

- **WHEN** 读取任一 run 的任一业务事件
- **THEN** 六个字段都存在，`payload` 为对象
- **AND** `type` 属于 `progress`、`failed`、`canceled`、`completed` 之一

#### Scenario: seq 从 1 连续递增且唯一

- **WHEN** 一个成功完成的确定性测试任务结束后读取其全部事件
- **THEN** `seq` 恰好为 1–10，每条出现一次，无缺号、无重复
- **AND** 不存在第二个 run 的 `seq` 混入该 run 的结果

#### Scenario: 模拟时刻可重复

- **WHEN** 同一确定性任务在两个不同项目、不同时间各执行一次
- **THEN** 两个 run 的第 N 条事件的 `simulatedAt` 相对各自 run 起点的偏移相同
- **AND** 该值不因 worker 停机时长或重连次数而改变

### Requirement: SSE 端点与帧契约

系统 SHALL 提供同域 SSE 端点推送 run 事件，路径包含项目与任务标识，请求 MUST 携带当前会话 Cookie。响应 MUST 为 `text/event-stream`，并对每条业务事件发送一帧：

```text
id: <runId>:<seq>
event: run-event
data: {"runId":"…","seq":4,"type":"progress","stage":"…","simulatedAt":"…","payload":{…}}
```

流开始 MUST 发送一次 `retry: 1000`；无业务事件时 MUST 以注释帧 `: keep-alive` 维持连接且该帧不携带 `id`。单条业务事件的 `id` MUST 为 `<runId>:<seq>`，使客户端可把最后收到的 `id` 原样作为下一次重连的 `Last-Event-ID`。

#### Scenario: 业务事件帧格式

- **WHEN** 客户端订阅一个刚开始的测试任务
- **THEN** 每一帧都包含 `id: <runId>:<seq>`、`event: run-event` 与完整 JSON 字段
- **AND** 帧之间以空行分隔，`data` 为单行 JSON

#### Scenario: 心跳不参与计数

- **WHEN** 订阅一个暂时没有新事件的任务超过心跳间隔
- **THEN** 客户端收到 `: keep-alive` 注释帧
- **AND** 该帧没有 `id`，不计入业务事件，也不改变客户端的重连游标

#### Scenario: 终态后结束连接

- **WHEN** 服务端发送了 `completed`、`failed` 或 `canceled` 终态事件
- **THEN** 随后发送一帧 `event: stream-end`，`data` 含 `runId` 与 `lastSeq`，不带业务 `id`
- **AND** 服务端关闭该连接，之后不再发送心跳或业务事件

### Requirement: 重连游标解析与非法游标

游标 MUST 统一指“客户端已完整处理的最后一个连续 `seq`”。无游标时 MUST 视为 0 并从 `seq > 0` 开始补发。合法范围 MUST 为 `0 <= 游标 <= 当前最大 seq`：当前最大序号为 4 时，`after=4` 有效并等待第 5 条；`after=5` MUST 返回 400。`Last-Event-ID` 合法时 MUST 优先于 `after`，非法、格式错误、属于其他 run 或超出当前最大序号的值 MUST 返回 400 JSON。鉴权、项目归属与任务归属 MUST 先于游标校验。

#### Scenario: 无游标从头补发

- **WHEN** 客户端不携带 `Last-Event-ID` 也不携带 `after` 订阅一个已有 4 条事件的 run
- **THEN** 服务端从 1 开始发送 1–4 的业务事件
- **AND** 不重复发送任何 `seq`

#### Scenario: 用 after=4 重连

- **WHEN** 客户端已收到 `seq=4` 后断开，并以 `after=4` 重新订阅
- **THEN** 服务端只发送 `seq` 5–10 的业务事件，不重发 1–4
- **AND** 客户端最终持有的序号集合为 1–10，每条一次

#### Scenario: 游标等于当前最大序号时继续等待

- **WHEN** 当前最大序号为 4，客户端以 `after=4` 订阅且 run 尚未终态
- **THEN** 服务端不报错，先不补发任何事件并保持连接
- **AND** 第 5 条事件提交后被推送给该连接

#### Scenario: 超出当前最大序号的游标被拒绝

- **WHEN** 当前最大序号为 4，客户端以 `after=5` 订阅
- **THEN** 返回 400 与 JSON 错误体
- **AND** 不建立 `text/event-stream`，也不返回任何事件帧

#### Scenario: Last-Event-ID 优先于 after

- **WHEN** 请求同时带有 `Last-Event-ID: <runId>:7` 与 `after=3`
- **THEN** 服务端以 7 为游标，只补发 `seq` 大于 7 的事件
- **AND** 不因查询参数回退到 3 而重复发送 4–7

#### Scenario: 非法游标被拒绝但归属优先

- **WHEN** 用户 B 以非法游标订阅 A 的任务，而匿名客户端以非法游标订阅任一任务
- **THEN** B 收到 404 `{"detail":"项目不存在"}`，匿名客户端收到 401，均不是 400
- **AND** 同一非法游标由项目所有者使用时才返回 400

### Requirement: 历史补发与实时推送无缝衔接

订阅端点 SHALL 先从数据库读取游标之后的全部已提交事件，再进入实时阶段；实时阶段 MUST 以数据库为事实来源，数据库通知/轮询只作为唤醒手段。补发与实时交接处 MUST 不产生缺口或重复：实时阶段每次只查询 `seq` 大于已发送最大序号的事件，且发送顺序严格按 `seq` 升序。

#### Scenario: 补发期间又产生新事件

- **WHEN** 服务端正在补发 5–7，而 worker 同时提交了 `seq=8`
- **THEN** 客户端仍按 5、6、7、8 的顺序收到事件
- **AND** 8 只出现一次，不因“补发快照”与“实时读取”竞争而重复或丢失

#### Scenario: 通知丢失时仍能补齐

- **WHEN** 实时阶段的数据库通知未被投递
- **THEN** 服务端在下一个轮询周期内仍能从数据库读出并推送新事件
- **AND** 客户端观察到的序号序列仍然连续

### Requirement: 前端按 runId 与 seq 去重

前端 SHALL 使用同一套日志处理逻辑处理历史补发与实时推送：以 `(runId, seq)` 为唯一键，重复键 MUST 被丢弃，来自其他 `runId` 的事件 MUST 被忽略。前端 MUST 维护“已完整处理的最后一个连续 `seq`”（从 1 开始，缺号即停）作为重连与重新同步的游标，而不是已收到的最大 `seq`；渲染顺序 MUST 按连续段升序。

#### Scenario: 补发与实时重叠不重复显示

- **WHEN** 前端先通过补发得到 1–4，随后实时帧又收到 3、4、5
- **THEN** 日志中 3 与 4 各只显示一次，5 正常追加
- **AND** 渲染顺序为 1、2、3、4、5，游标推进到 5

#### Scenario: 切换任务不混入旧 run

- **WHEN** 用户在同一页面从任务 X 切换到任务 Y
- **THEN** 日志只显示 Y 的 `runId` 事件
- **AND** 迟到的 X 事件被忽略，不追加到 Y 的日志

#### Scenario: 缺口时以连续游标而非最大序号重连

- **WHEN** 前端已有 1–4 而下一个到达的是 `seq=6`
- **THEN** 游标仍为 4，补发起点仍为 `after=4`，6 暂存为待补齐
- **AND** 收到 5 后形成连续日志 1–6，缺口提示消失
- **AND** 若长时间未补齐，页面提示“检测到事件缺口”并提供重新同步

### Requirement: 终态后的订阅行为

当 run 已处于终态时再次订阅，服务端 MUST 先补发游标之后的剩余事件（可能为空），随后立即发送 `stream-end` 并关闭连接，MUST NOT 保持长连接或发送心跳。客户端 MUST 在收到 `stream-end` 后关闭自身订阅，不再自动重连。

#### Scenario: 终态后以最大序号重连立即结束

- **WHEN** 客户端在任务 `succeeded` 之后以 `after=10`（等于当前最大序号）订阅
- **THEN** 服务端不补发业务事件，立即发送 `stream-end` 并关闭
- **AND** 连接不挂起等待第 11 条，客户端不进入重连循环

#### Scenario: 断线期间任务已进入终态

- **WHEN** 客户端断线后任务完成，客户端以 `after=4` 重连
- **THEN** 服务端补发 5–10 后发送 `stream-end`
- **AND** 页面最终日志为 1–10，状态显示成功

### Requirement: 事件内容可读且不泄露

事件 `payload` SHALL 只包含该 run 自身的进度信息（例如步骤序号、步骤名称、已提交步骤数、失败原因代码），MUST NOT 包含数据库连接串、凭据、会话令牌、其他项目或用户的标识，MUST NOT 包含完整异常堆栈。

#### Scenario: 失败事件只给出可读原因

- **WHEN** 任务受控失败并产生 `failed` 事件
- **THEN** `payload` 包含失败步骤与原因代码/文案
- **AND** 不包含 DSN、凭据、Cookie 或其他项目数据
