# background-job-queue Specification

## Purpose
让长任务在 PostgreSQL 中排队、由独立 worker 进程执行，并在客户端断线、进程崩溃与重复点击后仍能凭数据库中的持久事实确定“做到哪一步、结果是什么、副作用有没有重复”。

## Requirements

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

一个项目 SHALL 拥有多个任务，一个任务 MUST 对应唯一一个 run（1:1），一个 run MUST 拥有唯一的事件序号空间与步骤副作用记录。开始响应 MUST 同时返回 `jobId` 与 `runId`：`jobId` 代表队列与控制身份，`runId` 代表执行与事件身份。run MUST 持久化创建参数（总步数、阶段与受控失败步骤），使任何 worker 重新领取后仍读到同一组参数。worker 接手 MUST 复用原 `runId`，使 `seq` 在同一 run 内继续递增；任务进入终态后再次开始 MUST 产生新的 `jobId` 与新的 `runId`，新 run 的 `seq` 从 1 重新开始。

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

对同一项目、同一操作，当已存在 `queued` 或 `running` 的有效任务时，开始接口 MUST NOT 创建第二个有效任务，而 MUST 返回 200 与既有 `jobId`、`runId`，并标记为重复开始。两个并发请求 MUST 得到一个 202 与一个 200，并指向同一对 `jobId`/`runId`；持久化结果 MUST 只新增一个 job 行与一个 run 行，冲突分支 MUST NOT 留下孤立 run。只有任务进入终态后才允许新的开始请求创建新任务。

#### Scenario: 快速双击同一操作

- **WHEN** 项目所有者对同一项目、同一操作连续点击两次开始
- **THEN** 第一次返回 202，第二次返回 200 且 `jobId`、`runId` 与第一次相同
- **AND** 项目内有效任务数量为 1

#### Scenario: 两个并发请求只产生一个任务

- **WHEN** 两个开始请求在同一项目上并发到达
- **THEN** 恰好一个返回 202、一个返回 200，但指向同一对 `jobId`/`runId`
- **AND** 持久化记录中该项目的有效任务恰好为 1 个，且 `jobs` 与 `runs` 各只新增 1 行，不存在孤立 run

#### Scenario: 终态后允许重新开始

- **WHEN** 前一个任务已进入 `canceled` 或 `failed`，项目所有者再次开始同一操作
- **THEN** 返回 202 与新的 `jobId`
- **AND** 旧任务保持其终态与事件不变

### Requirement: 领取、租约续期与过期接手

worker SHALL 通过一次带行锁的原子更新领命任务，并同时写入租约持有者、本次领取的独立租约令牌与租约到期时间；执行期间 MUST 周期性续期。只有一个 worker 且它已停止时，系统 MUST NOT 依赖任何其他进程把数据库行改回 `queued`：持久状态可以保持为 `running`，查询 MUST 根据租约到期以数据库当前时间判定并返回 `awaitingTakeover=true`；任一可用 worker 的领取 MUST 原子接手“`queued` 或租约已过期的 `running`”，沿用 `jobId`、`runId` 并递增 `attempts`。

#### Scenario: 租约在运行中持续续期

- **WHEN** 一个 worker 正常执行多步任务
- **THEN** 在其执行期间租约到期时间持续被推后，任务不会因超时被其他 worker 接管
- **AND** `attempts` 保持为 1

#### Scenario: 唯一 worker 被杀后任务仍可被接手

- **WHEN** 任务已提交至少 2 步、尚未进入终态时强制停止唯一的 worker
- **THEN** 没有任何进程把该任务改回 `queued`，持久状态仍为 `running`
- **AND** 租约到期后查询以数据库时间判定并返回 `awaitingTakeover=true`，同时保留 `jobId`、`runId` 与已提交步骤
- **AND** worker 恢复后由新的 `attempt` 原子接手并跟踪到终态
- **AND** 任务详情的 `attempts` 大于 1，可用于区分“从未开始”和“曾经中断”

#### Scenario: 接手从中断处继续

- **WHEN** 中断前已提交第 1–3 步
- **THEN** 接手 worker 从第 4 步开始执行
- **AND** 第 1–3 步的副作用与事件不被重写、不重复追加

#### Scenario: 两个同时存在的任务互不影响

- **WHEN** 数据库中同时存在任务 A 与任务 B，A 的一次步骤事务提交成功
- **THEN** A 的 `next_step`、事件与租约按预期推进
- **AND** B 的 `next_step`、状态、`lease_token`、`lease_expires_at` 与 `attempts` 均保持原值

### Requirement: 步骤级幂等与旧 worker 不可重复提交

每一步的业务写入 SHALL 在单个数据库事务内完成：先以任务 ID 与本次领取的租约令牌为条件锁住任务行，再在持锁后用独立的数据库语句读取**实际数据库时钟**判断租约未过期（事务开始时间不得作为判断依据），随后以 `(runId, stepIndex)` 唯一约束写入步骤副作用，随后追加该步事件并推进已提交步骤号。所有状态推进、续租、失败与完成写入 MUST 同时限定 `jobId` 与租约令牌，否则 MUST 不生效。若事务失败则全部回滚，使“已做过哪一步”始终可由数据库唯一判定。

任何 worker 在手写事务前发现租约令牌已不属于自己、租约已过期或任务已进入终态时 MUST 立即停止且不写入任何数据；已过期的心跳 MUST NOT 续活旧租约；旧 worker 在中断后恢复也 MUST NOT 重复提交已由接手者完成的步骤。

#### Scenario: 旧 worker 恢复后不能重复提交

- **WHEN** 第一次领取已将任务提交到第 3 步且租约到期，第二次领取已接手并提交到第 5 步，随后第一次领取的旧令牌尝试续租并写第 6 步
- **THEN** 旧令牌的续租影响 0 行，第 6 步写入被拒绝且不产生副作用与事件
- **AND** 任务的 `next_step`、事件序号集合与副作用计数均与接手前一致
- **AND** 新持有者继续完成第 6–10 步，终态为成功

#### Scenario: 过期心跳不能续活租约

- **WHEN** 租约已过期后持有者才发出续租请求
- **THEN** 续租语句因租约过期条件不满足而影响 0 行
- **AND** 任务仍保持 `awaitingTakeover=true`，直到其它 worker 以新令牌接手

#### Scenario: 锁等待跨过到期点不得续租或写入

- **WHEN** 续租或步骤提交请求在租约到期前开始，随后在 `FOR UPDATE` 等待行锁时跨过到期点，期间没有新 worker 接手，令牌未换发
- **THEN** 拿到锁后按实际数据库时钟判定租约已过期，续租返回未生效、步骤提交被判为失去租约
- **AND** 租约到期时间不被延长，已提交步骤数与事件数保持不变
- **AND** 令牌换发后旧持有者的续租与写入仍被拒绝

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

取消请求 SHALL 对 `queued` 任务立即生效并写终态；对 `running` 任务 MUST 在下一个步骤边界生效，已提交步骤保留，此后 MUST NOT 再追加进度或完成事件。取消生效 MUST 追加一条 `canceled` 终态事件，其 `seq` MUST 等于该 run 已持久化的最大 `seq` 加一（已提交 3 步时取消得到 `seq=4`）。取消接口 MUST 幂等：重复取消或取消已终态任务返回 200 与当前状态，不产生额外事件。

取消与最后一步完成竞争时，系统 MUST 只保留一个终态：先提交者生效，后到者不改变状态与事件。

#### Scenario: 运行中取消

- **WHEN** 运行中的任务已提交 3 条进度事件后收到取消请求
- **THEN** 任务在下一步边界进入 `canceled`，`canceled` 事件为 `seq=4`
- **AND** 之后不再出现第 4 条进度事件，也不出现 `completed` 事件
- **AND** 查询状态、终态事件与副作用计数三者一致（副作用数等于已提交步数）

#### Scenario: 取消与最后一步竞争只留一个终态

- **WHEN** 取消请求与最后一步提交几乎同时到达
- **THEN** 数据库中恰好一条终态事件，且状态与事件类型一致
- **AND** 若为 `succeeded` 则不存在 `canceled`，若为 `canceled` 则不存在 `completed`

#### Scenario: 排队中取消

- **WHEN** worker 未运行，用户在任务仍为 `queued` 时取消
- **THEN** 任务立即进入 `canceled`，终态事件 `seq` 为 1
- **AND** 该任务不会再被任何 worker 领取执行

#### Scenario: 对已成功任务取消是幂等的

- **WHEN** 任务已 `succeeded` 后收到取消请求
- **THEN** 返回 200 与 `succeeded`，响应明确标记本次取消未生效
- **AND** 事件序列、副作用与 `finished_at` 均不变

### Requirement: 受控失败

测试任务 SHALL 支持仅用于验收的受控失败参数，并 MUST 在创建任务时把该参数持久化到 run 记录，使独立 worker 重启后仍知道在哪一步失败。失败 MUST 追加一条 `failed` 终态事件且 MUST NOT 追加 `completed` 事件；失败任务 MUST NOT 被报告为成功，且 `failed` MUST NOT 再转换为任何其他状态。

#### Scenario: 受控失败到终态

- **WHEN** 项目所有者以“在第 3 步失败”的参数开始测试任务
- **THEN** 任务终态为 `failed`，终态事件 `type` 为 `failed` 且 `seq` 为 3
- **AND** 事件中不存在 `completed`，查询状态与页面日志都不显示成功
- **AND** 已提交副作用的步骤数少于总步数

#### Scenario: 失败参数跨 worker 重启仍生效

- **WHEN** 以 `failAtStep=3` 创建任务后立即停止 worker，随后重启并在新进程中领取
- **THEN** 新 worker 仍从持久化参数读到第 3 步失败
- **AND** 终态仍为 `failed`，终态事件仍为 `seq=3`

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

### Requirement: 业务建图任务复用队列、租约与六步步骤事务

证据图谱建图 SHALL 作为新的 run 类型 `evidence.graph.build` 进入现有 PostgreSQL 队列，由独立 `worker` 进程领取执行。开始接口 MUST 只创建 build、job 与 run 并立即返回，同一项目同一建图操作的活动任务最多一个；并发或重复开始 MUST 返回既有身份，不新增任务。

建图任务 MUST 复用现有任务状态机、租约领取、续租、旧令牌拒绝、中断接手与终态唯一性。一次 build MUST 固定为 `bind`、`plan`、`extract`、`write`、`readback`、`publish` 六步。每个步骤 MUST 在同一事务内完成“锁定任务行、用数据库实际时钟验证租约、写入步骤副作用、写入建图业务状态、追加事件、推进步号”；接手 MUST 保持同一 `jobId`、`runId` 与 `buildId`，从第一个未提交步骤继续，不得重复已提交步骤或 episode。

第 6 步 `publish` 可以准备或校验候选快照，但候选快照 MUST NOT 对外可见。第 6 步 MUST 在单个事务内发布最终快照、`phase=ready`、`canContinue=true`、完成事件与任务 `succeeded`；只有该事务提交后，外部才能同时看到这五类结果。发布前崩溃、取消或失败 MUST NOT 产生可见的最终快照。

`run_steps` 与 `jobs.next_step` MUST 继续是“做到第几步”的唯一权威来源；建图快照版本是业务读模型，不得替代步骤事实。建图任务进入终态后 MUST 允许再次开始新的建图，旧任务保持其终态与事件不变。

#### Scenario: 建图任务进入同一队列并执行六步

- **WHEN** 项目所有者发起一次有效的业务建图
- **THEN** 数据库新增一条 `runs.kind=evidence.graph.build` 的 run、一条 job 与一条 build
- **AND** `run_steps` 依次记录 `bind`、`plan`、`extract`、`write`、`readback`、`publish`
- **AND** 任务初始为 `queued`，由 worker 领取后才变为 `running`

#### Scenario: 建图任务遵循既有状态与终态语义

- **WHEN** 建图任务被正常执行、受控失败或被取消
- **THEN** 状态分别进入 `succeeded`、`failed`、`canceled`，且终态不可逆
- **AND** 完成、失败或取消事件与业务状态在同一事务内提交
- **AND** 取消与发布竞争时只保留先提交的终态

#### Scenario: 发布事务同时暴露五类结果

- **WHEN** 第 6 步发布事务提交
- **THEN** 最终快照 `complete=true`、`phase=ready`、`canContinue=true`、完成事件与任务 `succeeded` 同时可见
- **AND** 不存在只看到其中一部分的中间状态
- **AND** 该事务提交前，候选快照不可通过接口读取

#### Scenario: 发布前崩溃不留下可见最终快照

- **WHEN** worker 在第 6 步准备或校验候选快照后、发布事务提交前崩溃
- **THEN** 候选快照即使已写入也对外不可见，任务仍可在接手后继续
- **AND** 当前 build 最近一次已发布的部分快照仍可查询
- **AND** 不出现 `ready`、`canContinue=true` 或完成事件

#### Scenario: 建图任务中断后从下一步继续

- **WHEN** 建图任务已提交前 3 步后 worker 中断并由新 worker 接手
- **THEN** `jobId`、`runId`、`buildId` 保持不变，`attempts` 大于 1
- **AND** 接手从第 4 步继续，前 3 步的步骤记录与事件不被重写或重复追加
- **AND** 已持久化的 episode 幂等键与内容被复用，不产生重复 episode

#### Scenario: 同一项目只允许一个活动建图

- **WHEN** 同一项目已有活动建图任务时再次开始
- **THEN** 返回既有 `buildId`、`jobId`、`runId`，不新增第二组记录
- **AND** 两个并发开始请求得到一个 202 与一个 200，且只新增一行 build、job 与 run

#### Scenario: 终态后允许新 build

- **WHEN** 前一个建图任务已 `succeeded`、`failed` 或 `canceled`
- **THEN** 同一项目再次开始会创建新的 `buildId`、`jobId`、`runId` 与新的步骤序号空间
- **AND** 旧 build 的状态、事件、快照与来源不被修改

### Requirement: 建图任务与测试任务面板隔离

任务列表接口 SHALL 继续只返回 `test.simulation` 任务；业务建图任务 MUST NOT 出现在“测试任务”面板、副作用计数或测试日志中。建图任务 MUST 通过证据图谱接口与对应的任务事件流观察，且其加载、失败或重试 MUST NOT 改写测试任务面板状态。第一阶段 MUST 继续保留“测试任务”面板，业务建图与测试任务在数据和界面上互不混入。

#### Scenario: 测试任务列表不混入建图任务

- **WHEN** 同一项目同时存在测试任务、材料解析任务与建图任务
- **THEN** 任务列表只返回测试任务，不出现建图任务的 `jobId`、`runId`、问题或节点数
- **AND** 测试任务面板不显示建图步骤或建图事件

#### Scenario: 建图事件不影响测试任务状态

- **WHEN** 建图任务推送进度或进入终态
- **THEN** 测试任务面板的当前任务、日志与副作用计数保持不变
- **AND** 建图面板与测试面板各自只反映自己的任务

#### Scenario: 第一阶段同时保留两种面板

- **WHEN** 项目所有者打开第一阶段
- **THEN** 材料面板、证据图谱面板与“测试任务”面板都可访问
- **AND** 测试任务面板仍明确标注为连通性自检，不产生推演结论
- **AND** 建图进度不写入测试任务日志

### Requirement: 角色与环境准备任务复用队列、租约与五步步骤事务

“准备角色与环境”SHALL 作为新的 run 类型 `agent.environment.prepare` 进入现有 PostgreSQL 队列，由独立 `worker` 领取执行。没有既有准备时，普通开始接口 MUST 只创建准备、job 与 run 并立即返回；同一 build 的 `preparing` 或 `ready` 准备最多一个，操作键 MUST 包含 build 标识，使不同 build 的准备互不阻塞。

普通开始与重试 MUST 分开。同一 build 已有 `preparing` 或 `ready` 准备时，普通开始 MUST 返回 200 与原 `preparationId`、`jobId`、`runId`，不新增记录；最近准备为 `failed` 或 `canceled` 时，普通开始 MUST 返回 409 `preparation_retry_required`，只有显式重试接口可以创建新的准备、job 与 run 并返回 202。worker 接手 MUST 保留原 `preparationId`、`jobId`、`runId`。

准备任务 MUST 复用现有任务状态机、租约领取、续租、数据库实际时钟、旧令牌拒绝、`attempts`、`awaitingTakeover` 与终态唯一性。一次准备 MUST 固定为 `bind`、`select`、`profile`、`compose`、`publish` 五步；每个步骤 MUST 在同一事务内完成“锁定任务行、验证租约、写入步骤副作用、写入准备状态、追加事件、推进步号”。`run_steps` 与 `jobs.next_step` MUST 是“做到第几步”的唯一权威来源，角色草稿不得替代步骤事实。

`profile` 步 MUST 按稳定批次生成。每批角色通过来源校验后写入 `preview_ready_at`，准备详情 MAY 返回只读 `previewRoles`；未通过来源校验的草稿、平台、初始话题、首发话题与激活顺序 MUST NOT 对外可见。worker 接手 MUST 复用已 `preview_ready_at` 的角色并跳过已完成批次，不重复生成已提交批次。

第 5 步 `publish` MUST 在单个事务内发布最终角色、角色来源、平台映射、初始话题、首发话题、激活顺序、`phase=ready`、完成事件与任务 `succeeded`。预览角色 MUST NOT 被用于确认或启动第三阶段；失败、取消或发布事务回滚 MUST NOT 产生最终角色、平台、话题、顺序或冻结选择。失败/取消后 MAY 保留只读的未完成预览，但必须标记 `previewOnly=true`、`canContinue=false`。

准备任务进入 `failed` 或 `canceled` 后，显式重试 MUST 创建新的准备与 run，旧准备、旧 job、旧 run 与旧预览保持其终态与可诊断性。

#### Scenario: 准备任务进入同一队列并执行五步

- **WHEN** 项目所有者对一个可继续的 build 开始准备
- **THEN** 数据库新增一条 `runs.kind=agent.environment.prepare` 的 run、一条 job 与一条准备记录
- **AND** `run_steps` 依次记录 `bind`、`select`、`profile`、`compose`、`publish`
- **AND** 任务初始为 `queued`，由 worker 领取后才变为 `running`

#### Scenario: 普通开始复用 preparing 或 ready 准备

- **WHEN** 同一 build 已有 `preparing` 或 `ready` 准备，用户再次点击普通开始
- **THEN** 响应为 200，返回原 `preparationId`、`jobId`、`runId`
- **AND** 数据库不新增第二组准备、job 或 run
- **AND** 不创建第二套角色草稿

#### Scenario: 失败或取消后只允许显式重试

- **WHEN** 同一 build 最近准备为 `failed` 或 `canceled`
- **THEN** 普通开始返回 409 `preparation_retry_required`，不创建新准备
- **AND** 显式重试返回 202，并创建新的准备、job 与 run
- **AND** 旧准备的终态、事件与未完成预览不被改写

#### Scenario: 不同 build 的准备互不阻塞

- **WHEN** 同一项目分别为两个不同 build 开始准备
- **THEN** 两个操作键不同，可各自创建准备与任务
- **AND** 各自只读取绑定 build 的最终快照与角色，不互相覆盖

#### Scenario: 中断接手保持身份并复用已完成批次

- **WHEN** 准备已提交第一批画像后 worker 中断，租约过期后由新 worker 接手
- **THEN** `preparationId`、`jobId`、`runId` 不变，`attempts` 大于 1
- **AND** 接手从第 3 步的未完成批次继续，已提交批次与事件不被重写或重复追加
- **AND** 已 `preview_ready_at` 的角色被复用，不产生重复角色

#### Scenario: 预览可见但发布结果原子

- **WHEN** 第 5 步发布事务尚未提交
- **THEN** 已完成批次可作为只读预览读取，未完成草稿不可见
- **AND** 平台映射、初始话题、首发话题、激活顺序与冻结选择均不可见
- **AND** 第 5 步提交后最终角色与全部配置同时可见

#### Scenario: 失败与取消不发布最终结果

- **WHEN** 准备因模型不可用、输出不合法、来源校验失败或用户取消进入终态
- **THEN** 状态分别为 `failed` 或 `canceled`，终态不可逆
- **AND** 读取接口不返回最终角色、平台、话题、顺序或冻结选择
- **AND** 已完成批次最多只读显示为未完成预览，`canContinue=false`
- **AND** 显式重试创建新的准备与 run，旧终态记录不被改写

#### Scenario: 准备任务与测试任务面板隔离

- **WHEN** 同一项目同时存在测试任务、材料解析任务、建图任务与准备任务
- **THEN** 任务列表仍只返回 `test.simulation` 任务
- **AND** 准备任务不写入测试副作用计数或测试日志
- **AND** 准备任务通过准备详情与对应任务事件流观察

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
