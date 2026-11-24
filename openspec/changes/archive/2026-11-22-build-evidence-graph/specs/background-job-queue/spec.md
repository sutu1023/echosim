# Spec Delta

## ADDED Requirements

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
