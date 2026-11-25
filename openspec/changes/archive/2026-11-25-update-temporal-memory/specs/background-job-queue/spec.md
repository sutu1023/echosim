# Spec Delta

## ADDED Requirements

### Requirement: 时序记忆同步任务复用队列、租约与三步步骤事务

未完成的运行时记忆投影 SHALL 由新的 run 类型 `simulation.memory` 进入现有 PostgreSQL 队列，由独立 `worker` 领取执行。操作键 MUST 为 `simulation-memory:v1:<simulationId>`，使同一运行的并发重试只产生一个活动记忆任务；终态后再次重试 MUST 允许创建新的 run/job，并从最早的未完成 episode 继续。

一次记忆同步 MUST 固定为 `bind`、`sync`、`publish` 三步。每个步骤 MUST 在同一事务内完成“锁定任务行、用数据库实际时钟验证租约、写入步骤副作用、追加该 run 的事件、推进步号”；`run_steps` 与 `jobs.next_step` MUST 是步骤层唯一权威。worker 接手 MUST 复用同一 `jobId`、`runId` 与 `simulationId`，从第一个未提交步骤继续，MUST NOT 重跑已提交步骤或重复已确认 episode。

记忆同步任务 MUST NOT 创建或修改 `simulation_actions`、`simulation_rounds`、`simulation_platforms` 的轮次/动作计数，MUST NOT 改变模拟运行的 `phase`、`failedPlatforms`、终态或终态事件。它 MAY 更新 `simulations.memory_status`、`simulation_memory_episodes`、运行图节点/边/来源与 `simulation_memory_syncs`。

`simulation_memory_syncs` MUST 每次尝试一行：`run_id` 为主键、`job_id` 唯一，同一 `simulation_id` 允许多行并保留历史；活动任务以 `jobs.status IN ('queued','running')` 复用，终态后允许同一 `simulationId` 新建任务。

记忆任务进入终态后 MUST 把 `pending_episodes`、`last_error_code` 与结束时间写入 `simulation_memory_syncs`；`memory_unavailable`、`write_unconfirmed` 与 `memory_too_large` MUST 作为稳定原因码保留，不得被覆盖成成功。

#### Scenario: 记忆同步进入同一队列并执行三步

- **WHEN** 一个运行仍有未完成记忆，系统创建或领取 `simulation.memory` 任务
- **THEN** 任务使用独立 `runId`/`jobId`，操作键包含 `simulationId`
- **AND** `run_steps` 依次记录 `bind`、`sync`、`publish`
- **AND** 模拟运行的动作、轮次与计数不变

#### Scenario: 同一运行并发重试只产生一个任务

- **WHEN** 两个记忆重试请求并发到达同一 `runId`
- **THEN** 一个返回 202、另一个返回 200，`memoryJobId` 相同
- **AND** 数据库只新增一条活动记忆任务
- **AND** 不产生第二组 episode 或第二份节点/边

#### Scenario: worker 中断后从最早未完成记忆继续

- **WHEN** 记忆任务在 `sync` 中途失去租约，由新 worker 接手
- **THEN** `jobId`、`runId`、`simulationId` 与 `attempts` 保持可查
- **AND** 接手从最早的未完成 episode 开始，已处理 episode 被跳过
- **AND** 不重复写已确认 episode，不新建第二个幂等键

#### Scenario: 记忆任务不写行动或终态

- **WHEN** 记忆任务完成或失败
- **THEN** `simulation_actions`、`simulation_rounds` 与平台计数行数不变
- **AND** 模拟运行的 `phase`、`terminal` 与终态事件不变
- **AND** 只更新记忆状态、episode、节点、边、来源与同步映射

#### Scenario: 终态记忆任务允许新一次重试

- **WHEN** 上一次记忆任务已 `failed` 或 `succeeded`，用户再次请求记忆重试
- **THEN** 允许创建新的 `simulation.memory` run/job
- **AND** 新任务复用已持久化的 episode 幂等键与内容
- **AND** 旧记忆任务的步骤、事件与终态保持可读
