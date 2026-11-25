# Spec Delta

## ADDED Requirements

### Requirement: 双平台运行任务复用队列、租约与四步步骤事务

双平台运行 SHALL 作为新 run 类型 `simulation.run` 进入现有 PostgreSQL 队列，由独立 `worker` 领取执行。操作键 MUST 包含 `selectionId`，使同一选择的重复开始只产生一组 run/job，不同选择互不阻塞。开始接口 MUST 只创建运行、job 与 run 并立即返回；同一选择已有活动运行时 MUST 返回 200 与同一 `runId`。

运行 MUST 复用现有任务状态机、租约领取、续租、数据库实际时钟、旧令牌拒绝、`attempts`、`awaitingTakeover` 与终态唯一性。一次运行 MUST 固定为 `bind`、`environment`、`advance`、`publish` 四步；每步 MUST 在同一事务内完成“锁定任务行、验证租约、写入步骤副作用、追加事件、推进步号”。

`advance` 步内的轮次进度 MUST 以按平台轮次记录为唯一游标：每条轮次记录 MUST 与该轮动作、按平台计数和该轮事件在同一事务提交，MUST 只在该事务中推进该平台已完成轮数。worker 接手 MUST 从第一个未提交轮次继续，MUST NOT 重跑已提交轮次或重复追加事件。`run_steps` 与 `jobs.next_step` MUST 仍是步骤层的权威来源，轮次记录 MUST NOT 被用来改写步骤序号。

#### Scenario: 运行进入同一队列并执行四步

- **WHEN** 项目所有者从已确认选择开始运行
- **THEN** 数据库新增一条 `runs.kind=simulation.run` 的 run、一个 job 与一条运行记录
- **AND** `run_steps` 依次记录 `bind`、`environment`、`advance`、`publish`
- **AND** job 初始为 `queued`，由 worker 领取后才变为 `running`

#### Scenario: 重复开始只产生一组记录

- **WHEN** 同一 `selectionId` 的运行处于 `queued` 或 `running` 时再次开始
- **THEN** 返回 200 与同一 `runId`、`jobId`
- **AND** 数据库不新增第二个 job 或 run
- **AND** 不创建第二套平台或轮次记录

#### Scenario: 接手从第一个未提交轮次继续

- **WHEN** 运行已提交若干轮后 worker 中断，租约过期后由新 worker 接手
- **THEN** `runId`、`jobId` 不变，`attempts` 大于 1
- **AND** 接手复用已提交轮次，从第一个未提交轮次继续
- **AND** 不产生重复轮次、重复动作或重复事件

### Requirement: 运行取消与终态后写入拒绝

取消运行 MUST 与现有取消语义一致：取消事务 MUST 同时写入 job `canceled`、一条 `canceled` 终态事件、运行 `canceled` 与结束时间；已在执行的那一轮若已写入引擎 MUST 先读回并记账，再随终态冻结。终态提交后，所有轮次、动作、计数与事件写入 MUST 被拒绝，迟到结果 MUST 被丢弃且不改变终态。

重复取消与对终态运行取消 MUST 幂等，MUST NOT 追加第二个终态事件或改写计数。取消导致的终态 MUST 与“最后一轮完成”竞争时只有一方生效：完成先提交时运行保持 `succeeded`，取消先提交时运行保持 `canceled`。

#### Scenario: 取消事务同时写入四类记录

- **WHEN** 运行中请求取消
- **THEN** 同一事务写入 job `canceled`、`canceled` 终态事件、运行 `canceled` 与结束时间
- **AND** 终态事件计入该 run 的连续 `seq`
- **AND** 取消后不再执行新的轮次

#### Scenario: 终态后写入被拒绝

- **WHEN** 终态提交后旧 worker 或迟到子进程尝试写轮次、动作或事件
- **THEN** 写入影响 0 行
- **AND** 运行计数、终态与事件序号不变

#### Scenario: 完成与取消竞争只有一方生效

- **WHEN** 最后一轮提交与取消请求并发
- **THEN** 只存在一个终态，且与先提交的事务一致
- **AND** 另一种终态不追加第二个终态事件

### Requirement: worker 按 run 类型声明可执行能力

worker SHALL 只领取自身配置允许执行的 run 类型。默认 worker MUST 覆盖 `test.simulation`、`document.ingest`、`evidence.graph.build`、`agent.environment.prepare` 与替身引擎下的 `simulation.run`；启用真实本地 OASIS 的独立 worker 变体 MUST 只领取 `simulation.run`，且其镜像 MUST NOT 改变默认 `app` 与 `worker-graph` 的依赖或健康探针。

未被任何 worker 声明执行的 job MUST 保持 `queued`，MUST NOT 被标记为失败或丢失。运行任务 MUST NOT 进入测试任务面板、测试副作用计数或测试日志，其状态通过运行详情与对应任务事件流观察。

#### Scenario: 未声明的任务保持排队

- **WHEN** 只有一个不声明 `simulation.run` 的 worker 在运行
- **THEN** 运行 job 保持 `queued`
- **AND** 不被标记为 `failed`，也不写入测试面板

#### Scenario: 真实引擎 worker 只领取运行任务

- **WHEN** 启用真实本地 OASIS 的 worker 变体启动
- **THEN** 它只领取 `simulation.run`
- **AND** 默认 `app` 容器的健康与存活语义不变
- **AND** 两个 worker 同时存在时一个 job 只被一个 worker 领取

#### Scenario: 运行任务与测试面板隔离

- **WHEN** 同一项目同时存在测试任务、材料解析、建图、准备与运行任务
- **THEN** 任务列表仍只返回 `test.simulation` 任务
- **AND** 运行任务不写入测试副作用计数或测试日志
