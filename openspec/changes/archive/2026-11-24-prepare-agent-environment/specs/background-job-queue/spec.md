# Spec Delta

## ADDED Requirements

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
