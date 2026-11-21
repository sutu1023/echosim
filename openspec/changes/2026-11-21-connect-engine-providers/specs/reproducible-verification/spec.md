# Spec Delta

## ADDED Requirements

### Requirement: Change 06 离线验收不依赖真实凭据与外部网络

`make verify` 与 `make verify CHANGE=2026-11-21-connect-engine-providers` SHALL 在没有真实模型密钥、没有 Zep 密钥、也没有模型服务或图谱服务网络连接的条件下完成。默认 `make verify` MUST 继续执行 Change 01–05 的全部既有场景；Change 06 专属场景 MUST 使用本地测试替身与纯契约校验增加引擎接入的离线回归，MUST NOT 读取 `.env` 中的真实密钥、MUST NOT 安装候选引擎依赖、MUST NOT 发起真实模型、图谱或仿真平台调用。

#### Scenario: 默认验收在没有引擎凭据时仍通过

- **WHEN** 环境中不存在模型与 Zep 密钥，且模型与图谱服务不可达
- **THEN** 不带 `CHANGE` 的 `make verify` 仍以退出码 0 结束
- **AND** 报告继续包含 Change 01–05 的首页、`/health` 200/503/200、`/livez`、容器身份、账户隔离、停库鉴权、任务事件、材料冒烟与工作台深链接等既有场景
- **AND** 报告记录真实引擎调用次数为 0，且运行过程不构建 `engine` profile 或安装候选引擎依赖

#### Scenario: Change 06 专属离线场景通过

- **WHEN** 以 `CHANGE=2026-11-21-connect-engine-providers` 执行 `make verify`
- **THEN** 命令在无真实密钥与无外部网络时以退出码 0 结束
- **AND** 报告 `change` 字段等于 `2026-11-21-connect-engine-providers`
- **AND** 报告新增离线引擎证据，至少覆盖模型字段与来源校验、移除 `response_format` 重试、图谱受理/索引/命中状态机、服务端已受理但响应丢失且首次搜索未命中、双平台独立判定、单平台失败保留另一平台证据、清理失败与缺配置依赖分别判定

#### Scenario: 离线场景使用测试替身而非真实服务

- **WHEN** Change 06 离线场景执行
- **THEN** 报告记录 `mode=offline-contract`、真实调用次数为 0、测试替身调用次数大于 0
- **AND** 每个替身场景都断言请求契约、状态转换与失败分类
- **AND** 报告不包含真实密钥、真实服务地址或私有样本内容

#### Scenario: 缺配置按依赖分别判定

- **WHEN** 离线场景分别模拟只缺 Zep 配置、只缺模型配置与两者都缺
- **THEN** 缺失依赖对应的项为 `not_run`/`missing_config`，其它可独立运行的项仍按自己的契约执行
- **AND** 本地 OASIS 动作不因缺少模型或图谱密钥而被跳过
- **AND** 总结果为非零，不把部分独立项通过写成整体通过

### Requirement: Change 06 离线入口保留未知变更与 SHA 规则

`make verify CHANGE=<未知值>` MUST 继续以退出码 2 结束，并说明“未注册的变更”。Change 06 报告 MUST 继续记录 `tested_sha` 等于执行时 `HEAD`、`dirty` 与逐场景结果；工作树存在未忽略改动时 MUST 沿用既有拒绝执行规则，除非显式允许并记录 `dirty: true`。

#### Scenario: 未注册变更仍明确失败

- **WHEN** 以未注册的 `CHANGE` 值执行 `make verify`
- **THEN** 命令以退出码 2 结束并在报告中说明“未注册的变更”
- **AND** 不把任何 Change 06 场景记为通过

#### Scenario: 报告继续绑定被测 SHA

- **WHEN** 在干净提交上执行 `make verify CHANGE=2026-11-21-connect-engine-providers`
- **THEN** 报告中的 `tested_sha` 等于执行时 `HEAD`，`dirty` 为 `false`
- **AND** 报告保留既有的逐场景结果、开始与结束时间、最终退出码
- **AND** 离线引擎证据与同一份报告一起写入 `runtime/verify/last-run.json`

### Requirement: 离线通过不得替代真实联调结论

`make verify` 与 Change 06 离线报告 MUST 明确其只证明适配层契约、状态机与失败分类，MUST NOT 声称模型、图谱或双平台已在真实服务上联通。真实联调结论 MUST 只来自同 SHA 的 `mode=live` `make smoke-live` 报告，并仍需人工签收；离线报告 MUST NOT 被写入 `docs/acceptance/` 或替代人工记录。

#### Scenario: 自动化报告不宣称真实联通

- **WHEN** Change 06 离线验收通过
- **THEN** 报告只声明离线契约与测试替身结果
- **AND** 不包含“模型 API 已调用成功”“Zep 已检索命中”“OASIS 双平台已运行”之类结论
- **AND** 真实调用计数为 0

#### Scenario: 真实联调结论等待 live 报告与人工签收

- **WHEN** 验收人准备签收真实联通
- **THEN** 必须引用同 SHA 的 `mode=live` 报告，其中模型、图谱、平台 A、平台 B、清理与依赖基线全部为 `passed`，且 `dirty=false`
- **AND** 人工验收记录写入 `docs/acceptance/`，由验收人签收
- **AND** `make verify`、CI 与 `make smoke-live` 均不创建或修改该人工记录

### Requirement: CI 同时保留默认验收与 Change 06 离线检查

CI SHALL 保留现有默认 `make verify` 工作，并增加独立的 Change 06 离线检查，调用 `make verify CHANGE=2026-11-21-connect-engine-providers`。两次检查 MUST 使用不同名称上传报告产物，并分别断言 `tested_sha` 与触发 SHA 一致、`dirty=false`、`result=passed`；Change 06 专属检查 MUST 额外断言 `calls.real=0`。CI MUST NOT 注入模型或 Zep 密钥、MUST NOT 执行 `make smoke-live`、MUST NOT 构建 live 引擎镜像。

#### Scenario: 默认与专属离线检查分别产出报告

- **WHEN** CI 在干净检出上运行
- **THEN** 默认 `make verify` 与 `make verify CHANGE=2026-11-21-connect-engine-providers` 分别执行
- **AND** 两份报告使用不同 artifact 名称上传，产物缺失即失败
- **AND** 两份报告都断言 `tested_sha`、`dirty=false`、`result=passed`

#### Scenario: 专属离线检查断言无真实调用

- **WHEN** 审查 Change 06 的 CI 报告
- **THEN** 报告中的 `calls.real` 为 0，`mode=offline-contract`，测试替身调用次数大于 0
- **AND** 工作流没有注入模型或 Zep 密钥
- **AND** 工作流没有执行 `make smoke-live`，也没有构建 `Dockerfile.engine` 或启用 live profile
