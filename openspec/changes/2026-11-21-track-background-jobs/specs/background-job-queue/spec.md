# Spec Delta

## Purpose

让长任务在 PostgreSQL 中排队、由独立 worker 进程执行，并在客户端断线、进程崩溃与重复点击后仍能凭数据库中的持久事实确定“做到哪一步、结果是什么、副作用有没有重复”。

## ADDED Requirements

### Requirement: 持久队列与独立执行进程

系统 SHALL 使用 PostgreSQL 表作为任务队列的唯一事实来源，并由独立于 `app` 的 worker 进程领取与执行长任务。开始请求 MUST 只负责创建任务记录并立即返回，不得在 HTTP 请求内执行任务步骤；任务记录与已提交步骤 MUST 在 `app`、`worker` 进程重启后仍然存在并可查询。

任务状态 MUST 恰好为 `queued`、`running`、`succeeded`、`failed`、`canceled` 五种，且开始接口 MUST 返回 HTTP 202 与 `jobId`、`runId`。

#### Scenario: 开始任务立即返回 202

- **WHEN** 已登录的项目所有者 A 对自己的项目提交一次测试任务开始请求
- **THEN** 响应状态码为 202，响应体包含 `jobId`、`runId`、`status` 与是否为重复开始的标记
- **AND** 该请求的耗时与任务总步数无关，不等待任务执行完成
- **AND** 随后查询该 `jobId` 可看到状态从 `queued` 变为 `running` 或已进入终态

#### Scenario: 进程重启后任务与进度仍在

- **WHEN** 任务执行到第 3 步后停止并重新启动 `app` 与 `worker` 进程
- **THEN** 同一 `jobId` 仍可查询到该任务，`runId` 不变，已提交步骤数不少于 3
- **AND** worker 重新领取后从第一个未提交步骤继续，而不是从第 1 步重新执行

#### Scenario: worker 单独停止不影响 app 的存活与就绪

- **WHEN** 仅停止 `worker` 容器，随后请求 `GET /health` 与 `GET /livez`
- **THEN** 数据库可用时 `/health` 仍返回 200 `{"status":"ok","database":"ok"}`，`/livez` 仍返回 200
- **AND** `app` 容器未被重启，任务只表现为停留在 `queued` 等待接手

### Requirement: 状态转换与终态唯一性

任务状态转换 SHALL 限定为：新建→`queued`；`queued`→`running`（被领取）；`running`→`succeeded`（最后一步提交完成事件）；`running`→`failed`（受控失败）；`queued` 或 `running`→`canceled`（取消生效）。`succeeded`、`failed`、`canceled` MUST NOT 再转换到任何其他状态。

取消与完成同时发生时，MUST 以先提交事务的持久化结果为准：若完成先提交，取消请求 MUST 返回 `200` 与已持久化的 `succeeded`，不得改写结果；若取消先提交，worker 的下一次步骤提交 MUST 失败退出，不得再追加完成事件。

#### Scenario: 正常完成的转换序列

- **WHEN** 一个确定性测试任务被领取并执行到最后一步
- **THEN** 状态序列为 `queued` → `running` → `succeeded`
- **AND** `finished_at` 与终态事件在同一事务内写入，不存在“状态已成功但无完成事件”的记录

#### Scenario: 终态不可逆

- **WHEN** 任务已处于 `succeeded`、`failed` 或 `canceled`，再次提交取消请求
- **THEN** 响应为 200 且返回当前已持久化的状态
- **AND** 状态、`finished_at` 与事件序列均不发生变化

#### Scenario: 取消与完成同时发生以先提交者为准

- **WHEN** 任务的最后一步提交与取消请求几乎同时到达
- **THEN** 数据库中的终态等于先提交事务的结果，且只有一条终态事件
- **AND** 查询状态、事件流终态事件与页面日志三者都报告同一终态，不出现“已取消后又成功”

### Requirement: jobId、runId 与项目的标识关系

一个项目 SHALL 拥有多个任务，一个任务 MUST 对应唯一一个 run（1:1），一个 run MUST 拥有唯一的事件序号空间与步骤副作用记录。开始响应 MUST 同时返回 `jobId` 与 `runId`：`jobId` 代表队列与控制身份，`runId` 代表执行与事件身份。worker 接手 MUST 复用原 `runId`，使 `seq` 在同一 run 内继续递增；任务进入终态后再次开始 MUST 产生新的 `jobId` 与新的 `runId`，新 run 的 `seq` 从 1 重新开始。

#### Scenario: 标识关系可查询

- **WHEN** 项目所有者查询某个任务的详情与事件
- **THEN** 详情包含 `jobId`、`runId`、所属项目与当前状态
- **AND** 事件与副作用记录的 `runId` 与详情一致

#### Scenario: 接手不更换 runId

- **WHEN** 第一个 worker 在第 3 步之后中断，另一个 worker 接手
- **THEN** `jobId` 与 `runId` 均保持不变
- **AND** 后续事件的 `seq` 从 4 继续，而不是新的 run 从 1 开始

#### Scenario: 终态后重新开始产生新标识

- **WHEN** 一个任务已 `succeeded`，同一项目再次提交同一操作
- **THEN** 返回新的 `jobId` 与新的 `runId`
- **AND** 新 run 的事件 `seq` 从 1 开始，旧 run 的事件不被修改

### Requirement: 重复开始保护

对同一项目、同一操作，当已存在 `queued` 或 `running` 的有效任务时，开始接口 MUST NOT 创建第二个有效任务，而 MUST 返回 200 与既有 `jobId`、`runId`，并标记为重复开始。该保护 MUST 在数据库层强制生效，使两个并发请求也只能产生一个有效任务；只有任务进入终态后才允许新的开始请求创建新任务。

#### Scenario: 快速双击同一操作

- **WHEN** 项目所有者对同一项目、同一操作连续点击两次开始
- **THEN** 第一次返回 202，第二次返回 200 且 `jobId`、`runId` 与第一次相同
- **AND** 项目内有效任务数量为 1

#### Scenario: 两个并发请求只产生一个任务

- **WHEN** 两个开始请求在同一项目上并发到达
- **THEN** 两次响应都成功，但指向同一个 `jobId`，仅其中一个标记为新建
- **AND** 持久化记录中该项目的有效任务恰好为 1 个，不存在第二个 `queued` 任务

#### Scenario: 终态后允许重新开始

- **WHEN** 前一个任务已进入 `canceled` 或 `failed`，项目所有者再次开始同一操作
- **THEN** 返回 202 与新的 `jobId`
- **AND** 旧任务保持其终态与事件不变

### Requirement: 领取、租约续期与过期接手

worker SHALL 通过一次带行锁的原子更新领取任务，并同时写入租约持有者与租约到期时间；执行期间 MUST 周期性续期。未及时续期的 `running` 任务 MUST 在租约到期后被判定为“等待接手”，其状态 MUST 回到可领取状态且保留已提交步骤，由任一可用 worker 重新领取，`attempts` 计数 MUST 递增。

#### Scenario: 租约在运行中持续续期

- **WHEN** 一个 worker 正常执行多步任务
- **THEN** 在其执行期间租约到期时间持续被推后，任务不会因超时被其他 worker 接管
- **AND** `attempts` 保持为 1

#### Scenario: worker 被杀后任务被接手

- **WHEN** 任务已提交至少 2 步、尚未进入终态时强制停止 worker
- **THEN** 租约到期前任务仍为 `running`，到期后查询显示为等待接手状态
- **AND** worker 恢复后由新的 `attempt` 领取，并跟踪到终态
- **AND** 任务详情的 `attempts` 大于 1，可用于区分“从未开始”和“曾经中断”

#### Scenario: 接手从中断处继续

- **WHEN** 中断前已提交第 1–3 步
- **THEN** 接手 worker 从第 4 步开始执行
- **AND** 第 1–3 步的副作用与事件不被重写、不重复追加

### Requirement: 步骤级幂等与旧 worker 不可重复提交

每一步的业务写入 SHALL 在单个数据库事务内完成：先以租约持有者与未过期租约为条件锁住任务行，再以 `(runId, stepIndex)` 唯一约束写入步骤副作用，随后追加该步事件并推进已提交步骤号。若事务失败则全部回滚，使“已做过哪一步”始终可由数据库唯一判定。

任何 worker 在手写事务前发现租约已不属于自己、或任务已进入终态时 MUST 立即停止且不写入任何数据；旧 worker 在中断后恢复也 MUST NOT 重复提交已由接手者完成的步骤。

#### Scenario: 旧 worker 恢复后不能重复提交

- **WHEN** worker 1 在第 3 步后失去租约，worker 2 已接手并提交到第 5 步，随后 worker 1 恢复并尝试继续
- **THEN** worker 1 的步骤事务因租约条件不满足而失败，不写入副作用、不追加事件
- **AND** 数据库中的副作用记录数、事件数与 `seq` 序列不因 worker 1 的恢复而变化

#### Scenario: 同一步骤重复写入被忽略

- **WHEN** 任一路径尝试对同一 `(runId, stepIndex)` 再次写入副作用
- **THEN** 唯一约束使该写入不产生新记录，且不追加新事件
- **AND** 该步在副作用查询中仍只出现一次

#### Scenario: 每一步只追加一条事件

- **WHEN** 任务成功执行完 10 步
- **THEN** 每个步骤序号对应恰好一条业务事件，`(runId, seq)` 无重复
- **AND** 不存在同一 `seq` 的两条不同事件

### Requirement: 可查询、可计数的测试副作用

测试任务每一步 SHALL 提交一条可独立查询、可独立计数的副作用记录，并提供只读查询接口返回副作用总数、出现过的步骤序号与去重后的数量。验收者 MUST 能不看页面、只看该接口或数据库即可判断“是否重复执行”。

#### Scenario: 验收者独立核实副作用

- **WHEN** 一个成功任务的终态为 `succeeded`
- **THEN** 副作用查询返回总数 10、步骤序号 1–10 各出现一次
- **AND** 去重后的步骤数与总数相等，不存在缺号或重复号

#### Scenario: 中断恢复后计数仍为 10

- **WHEN** 任务在中断并被接手后最终成功
- **THEN** 副作用查询仍返回总数 10，步骤序号 1–10 各一次
- **AND** 事件总数仍为 10，`seq` 为 1–10 且无重复

### Requirement: 取消语义

取消请求 SHALL 对 `queued` 任务立即生效并写终态；对 `running` 任务 MUST 在下一个步骤边界生效，已提交步骤保留，此后 MUST NOT 再追加进度或完成事件。取消生效 MUST 追加一条 `canceled` 终态事件，其 `seq` 为当前 run 的下一个序号。取消接口 MUST 幂等：重复取消或取消已终态任务返回 200 与当前状态，不产生额外事件。

#### Scenario: 运行中取消

- **WHEN** 运行中的任务已提交 2 条进度事件后收到取消请求
- **THEN** 任务在下一步边界进入 `canceled`，`canceled` 事件为第 3 条业务事件
- **AND** 之后不再出现第 4 条进度事件，也不出现 `completed` 事件
- **AND** 查询状态、事件流与页面日志一致报告“已取消”

#### Scenario: 排队中取消

- **WHEN** worker 未运行，用户在任务仍为 `queued` 时取消
- **THEN** 任务立即进入 `canceled`，终态事件 `seq` 为 1
- **AND** 该任务不会再被任何 worker 领取执行

#### Scenario: 对已成功任务取消是幂等的

- **WHEN** 任务已 `succeeded` 后收到取消请求
- **THEN** 返回 200 与 `succeeded`，响应明确标记本次取消未生效
- **AND** 事件序列、副作用与 `finished_at` 均不变

### Requirement: 受控失败

测试任务 SHALL 支持仅用于验收的受控失败参数，使任务在指定步骤失败并进入 `failed` 终态。失败 MUST 追加一条 `failed` 终态事件且 MUST NOT 追加 `completed` 事件；失败任务 MUST NOT 被报告为成功。

#### Scenario: 受控失败到终态

- **WHEN** 项目所有者以“在第 3 步失败”的参数开始测试任务
- **THEN** 任务终态为 `failed`，终态事件 `type` 为 `failed` 且 `seq` 为 3
- **AND** 事件中不存在 `completed`，查询状态与页面日志都不显示成功
- **AND** 已提交副作用的步骤数少于总步数

### Requirement: 非法输入不落库

开始、取消与查询接口 SHALL 校验输入：未知操作、非法阶段、非法 `failAtStep` 范围（必须为 1–10 的整数或省略）以及格式非法的 `jobId` MUST 返回 422 或 404，且 MUST NOT 创建任务、追加事件或写入副作用。

#### Scenario: 非法参数被拒绝

- **WHEN** 项目所有者提交未知操作名、非法阶段 slug 或 `failAtStep=0`
- **THEN** 返回 422 并指出非法字段
- **AND** 该项目的任务数量与事件数量保持不变

#### Scenario: 非法 jobId 不产生 500

- **WHEN** 项目所有者用格式非法的 jobId 查询任务详情
- **THEN** 返回 404 `{"detail":"任务不存在"}`
- **AND** 不暴露 ID 解析细节或数据库错误

### Requirement: worker 故障可诊断且不泄露凭据

worker 与任务接口 SHALL 在日志中按 `jobId`、`runId`、`attempt` 记录领取、续期、接手、终态与异常类型，且 MUST NOT 记录数据库 DSN、凭据、会话令牌或完整堆栈到对外响应。worker 处理单条任务失败 MUST NOT 使 `app` 的 `/health` 变为 503。

#### Scenario: 中断与接手可按任务关联

- **WHEN** 一个任务经历 worker 中断与接手
- **THEN** worker 日志包含同一 `jobId` 的两次领取记录，`attempt` 分别为 1 与 2
- **AND** 接口响应与日志摘要中都不出现数据库连接串或凭据
