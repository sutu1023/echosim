# Spec Delta

## ADDED Requirements

### Requirement: 变更专属验收场景与任务证据

`make verify` SHALL 支持通过 `CHANGE=<change-id>` 选择变更专属场景，并在报告中记录所选变更。未指定 `CHANGE` 时 MUST 继续执行 Change 01–03 的全部既有场景，并额外执行本变更的任务底座基础场景；指定 `CHANGE=2026-11-21-track-background-jobs` 时 MUST 在基础场景之上追加断线续传、worker 中断恢复、并发重复开始、取消与受控失败场景。未知的 `CHANGE` 值 MUST 以非零退出码结束并说明原因，MUST NOT 静默跳过场景。

任务场景的结果 MUST 写入可机器读取的证据：`jobId`、`runId`、`attempts`、断开前收到的最大 `seq`、重连游标、最终事件序号列表、副作用总数与去重后的步骤序号。报告 MUST 继续满足既有 `tested_sha`、`dirty`、逐场景结果的契约。

#### Scenario: 默认执行保留既有回归

- **WHEN** 以不带 `CHANGE` 的 `make verify` 执行验收
- **THEN** 报告仍包含首页、`/health` 200/503/200、`/livez`、容器身份不变、账户隔离、停库 401/503、退出失效、重新登录与工作台深链接等 Change 01–03 场景
- **AND** 新增任务基础场景：开始返回 202、状态可查询、确定性任务到达 `succeeded`、匿名 401、他人项目 404、停库 503 保留 Cookie
- **AND** 报告中的 `tested_sha` 仍等于执行时 `HEAD`

#### Scenario: 断线续传被真实执行

- **WHEN** 以 `CHANGE=2026-11-21-track-background-jobs` 执行验收
- **THEN** 验收脚本建立真实 SSE 连接，读到 `seq=4` 后主动关闭连接
- **AND** 等待 worker 继续后以游标 4 重新订阅，直到收到 `stream-end`
- **AND** 报告记录断开前 `seq`、重连游标、最终序号为 1–10 且每条一次，`jobId` 与 `runId` 均被写入

#### Scenario: worker 中断恢复被数据判据核实

- **WHEN** 任务已提交至少 2 步且未终态时停止 worker 容器
- **THEN** 报告记录停止时的 `jobId`、`runId`、已提交步骤与 `attempts`
- **AND** 恢复 worker 后跟踪到终态，副作用查询返回总数 10、步骤序号 1–10 各一次，`attempts` 大于 1
- **AND** 若出现重复步骤或总数超过 10，场景判定失败

#### Scenario: 重复开始与取消/失败被独立断言

- **WHEN** 变更专属场景执行
- **THEN** 两个并发开始请求只产生一个有效 `jobId`，另一次响应指向同一 `jobId`
- **AND** 运行中取消的任务、受控失败的任务分别以 `canceled`、`failed` 终态结束，MUST NOT 出现 `completed` 事件
- **AND** 这些结果各自记录为独立场景

#### Scenario: 未知 CHANGE 明确失败

- **WHEN** 以未注册的 `CHANGE` 值执行 `make verify`
- **THEN** 命令以非零退出码结束并在报告中说明“未注册的变更”
- **AND** 不把任何场景记为通过

### Requirement: 浏览器行为与自动化结论分离

任务相关的 SSE 中断提示、worker 中断提示、重连过程、布局与语言保持、截图与可观察时限 MUST 由人工在浏览器中走查并记录，MUST NOT 被 `make verify` 记为通过。自动化报告 MUST 明确其只证明服务端断线续传、状态机与副作用计数。

人工验收记录 SHALL 包含被测 `tested_sha`、日期、验收人、`jobId`、`runId`、断开前与最终的实际事件序号、worker 中断时的已提交步骤，并引用同 SHA 的 `make verify` 报告与浏览器证据位置。

#### Scenario: 自动化不宣称页面已验收

- **WHEN** `make verify CHANGE=2026-11-21-track-background-jobs` 通过
- **THEN** 报告只声明服务端 SSE 断线续传、状态转换与副作用计数结果
- **AND** 不包含“页面提示已验证”“布局与语言保持已验证”之类的结论

#### Scenario: 人工记录按 SHA 与任务对齐

- **WHEN** 验收人在浏览器完成断线、重连、worker 中断与失败/取消走查
- **THEN** 记录写入 `docs/acceptance/`，包含 `tested_sha`、`jobId`、`runId` 与实际事件序号
- **AND** 可通过同一 SHA 把人工记录与 `make verify CHANGE=…` 报告对应起来
- **AND** `make verify` 与 CI 均不创建、修改或删除该记录

#### Scenario: 变更专属场景可重复执行

- **WHEN** 在同一环境连续两次执行 `make verify CHANGE=2026-11-21-track-background-jobs`
- **THEN** 两次都以退出码 0 结束
- **AND** 第二次执行前无需人工停止容器、清理数据卷或修复上一次的残留任务
