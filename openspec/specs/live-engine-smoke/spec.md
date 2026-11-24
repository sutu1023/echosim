# live-engine-smoke Specification

## Purpose
提供一条由开发者显式运行的真实联通验收入口，把模型结构化、图谱检索与双平台行动证据集中到一份可复核报告，并用逐项状态、真实/替身调用计数与退出码阻止“离线通过冒充真实联调”。

## Requirements

### Requirement: 真实联调由显式命令执行并生成可追溯报告

`make smoke-live` SHALL 是唯一执行真实模型、真实图谱与真实双平台动作的**验收与自动化**入口，MUST NOT 被 `make up`、默认 `make verify` 或 CI 自动触发。业务建图是用户主动发起的产品路径，不是验收入口：它只能在项目所有者点击开始时由业务 worker 执行，默认启动、默认 `make verify` 与 CI MUST NOT 触发；业务建图 MUST NOT 生成 `runtime/smoke-live/` 报告、MUST NOT 使用验收专用图谱、MUST NOT 被计为真实联调通过。

`make smoke-live` 命令 SHALL 在稳定路径 `runtime/smoke-live/last-run.json` 写入机器可读报告，并保留一份带 UTC 时间戳的同内容运行记录。报告 MUST 包含 `result`、`mode`、`tested_sha`、`dirty`、开始与结束时间、逐项结果、依赖基线、调用计数与脱敏证据。

#### Scenario: 显式执行生成报告

- **WHEN** 开发者显式执行 `make smoke-live`
- **THEN** 命令写入 `runtime/smoke-live/last-run.json` 与一份带时间戳的运行记录
- **AND** 报告中的 `tested_sha` 等于执行时 `HEAD`，`mode` 为 `live`
- **AND** 报告逐项列出模型、图谱、平台 A、平台 B 与清理结果

#### Scenario: 默认入口不触发真实调用

- **WHEN** 执行 `make up`、`make verify` 或 CI 工作流
- **THEN** 这些入口不执行 `make smoke-live`，不读取真实模型或 Zep 密钥
- **AND** 对模型、图谱与仿真平台的真实调用次数为 0
- **AND** 报告或日志不产生“真实联调已通过”的结论

#### Scenario: 业务建图不是验收入口

- **WHEN** 项目所有者点击开始业务建图
- **THEN** 业务 worker 只执行该项目的建图任务，不写入 `runtime/smoke-live/` 报告
- **AND** 不使用 `ZEP_ACCEPTANCE_GRAPH_ID` 指向的验收专用图谱
- **AND** 业务建图结果不被计为模型、图谱或双平台真实联调通过

#### Scenario: 默认入口不触发业务建图

- **WHEN** 执行 `make up`、默认 `make verify` 或 CI 工作流
- **THEN** 不自动创建业务建图任务，不发起业务模型或 Zep 调用
- **AND** 即使环境中存在业务密钥，也只有项目所有者主动开始建图时才允许真实调用

### Requirement: 脏工作树默认拒绝且显式允许时如实标记

`make smoke-live` SHALL 沿用仓库的 `ALLOW_DIRTY=1` 约定：默认在工作树存在未忽略改动时，MUST 在发起任何外部调用前拒绝执行；显式设置 `ALLOW_DIRTY=1` 时 MAY 继续本地调试，但报告 MUST 记录 `dirty=true`。签收只接受 `dirty=false`、`tested_sha` 与候选提交一致的真实报告。

#### Scenario: 默认拒绝脏工作树

- **WHEN** 工作树存在未忽略改动且未设置 `ALLOW_DIRTY=1`
- **THEN** 命令在模型、图谱或仿真平台调用前以非零状态结束
- **AND** 报告记录拒绝原因，真实调用次数为 0
- **AND** 不写出任何“通过”结论

#### Scenario: 显式允许脏工作树调试

- **WHEN** 工作树存在未忽略改动且设置 `ALLOW_DIRTY=1`
- **THEN** 命令可以继续执行，但报告记录 `dirty=true`
- **AND** 报告明确标注该结果不能作为签收证据
- **AND** 签收要求该报告不被引用为最终证据

### Requirement: 逐项状态与必需项共同决定退出码

报告 SHALL 把每个检查项标为 `passed`、`failed` 或 `not_run`；`not_run` MUST 附带原因码（如 `missing_config`、`prerequisite_failed`、`dependency_unresolved`）。必需项 MUST 至少包含模型结构化结果、图谱检索命中、平台 A 行动记录、平台 B 行动记录、资源清理与依赖基线；只有全部必需项为 `passed` 时命令才以退出码 0 结束。任一必需项为 `failed` 或 `not_run` MUST 以非零状态结束；非法调用或无法写出报告 MUST 以非零状态结束并说明原因。

#### Scenario: 全部必需项通过

- **WHEN** 模型结果、图谱检索、平台 A、平台 B、清理与依赖基线全部通过
- **THEN** 报告 `result` 为 `passed`，命令退出码为 0
- **AND** 每个必需项都有依赖名称、阶段、耗时与证据摘要

#### Scenario: 缺少图谱配置只阻断图谱

- **WHEN** Zep 密钥或 `ZEP_ACCEPTANCE_GRAPH_ID` 缺失，而模型配置与本地 OASIS 运行条件齐备
- **THEN** 图谱项标为 `not_run`，原因码为 `missing_config`，对图谱服务的真实调用次数为 0
- **AND** 模型项与两个本地 OASIS 平台动作仍可独立执行并记录证据
- **AND** 总结果因图谱必需项未通过而非零退出，不把本地动作通过解释为整体通过

#### Scenario: 缺少模型配置只阻断模型

- **WHEN** 模型地址、模型名或密钥缺失，而图谱与本地 OASIS 运行条件齐备
- **THEN** 模型项标为 `not_run`，原因码为 `missing_config`，对模型服务的真实调用次数为 0
- **AND** 图谱项与两个本地 OASIS 平台动作仍可独立执行并记录证据
- **AND** 总结果因模型必需项未通过而非零退出

#### Scenario: 多个依赖缺配置时分别判定

- **WHEN** 模型与图谱配置同时缺失
- **THEN** 模型项与图谱项分别标记 `not_run`/`missing_config`
- **AND** 不依赖外部密钥的本地 OASIS 平台动作仍继续执行
- **AND** 报告按依赖分别列出缺失项，不把所有项合并为一个笼统失败

#### Scenario: 单个平台失败仍保留另一个平台的证据

- **WHEN** 平台 A 通过而平台 B 失败
- **THEN** 报告保留平台 A 的通过证据与平台 B 的失败原因
- **AND** 双平台必需项判定为失败，命令以非零状态结束
- **AND** 不因为平台 A 通过而把整体结果写成 `passed`

### Requirement: 真实调用与测试替身分别计数

报告 SHALL 对每个依赖分别记录 `calls.real` 与 `calls.double`，并在顶层汇总。`make smoke-live` 的真实调用 MUST 计入 `calls.real`，测试替身 MUST 计入 `calls.double`；离线契约检查产生的替身调用 MUST NOT 混入真实联调报告。重试、移除参数重试、SDK 内置重试、写入核对与索引轮询都 MUST 按实际发生的调用计数，不得按“逻辑项”计数。

#### Scenario: 模型移除参数重试如实计数

- **WHEN** 模型第一次调用触发移除 `response_format` 的重试，第二次调用成功
- **THEN** 报告记录 `model.calls.real=2`、`fallbackUsed=true`
- **AND** 不把两次调用合并为一次

#### Scenario: 图谱写入核对与索引轮询分别计数

- **WHEN** 图谱写入超时后执行一次受理核对，并在索引等待中检索多次
- **THEN** 报告分别记录写入、核对与检索的真实调用次数
- **AND** 索引轮询次数与命中等待时间一致，不把轮询省略为一次

#### Scenario: 真实联调不使用测试替身

- **WHEN** 以 `mode=live` 执行 `make smoke-live`
- **THEN** 报告顶层与各依赖的 `calls.double` 均为 0
- **AND** 任一依赖实际使用了替身时，该项不得标记为真实联调通过

#### Scenario: SDK 内置重试计入真实调用

- **WHEN** 模型或图谱 SDK 在内部自动重试
- **THEN** 每次实际 HTTP 请求都计入对应依赖的 `calls.real`
- **AND** 实际请求总数不得超过该项预算
- **AND** 报告不把 SDK 内部重试隐藏为一次逻辑调用

### Requirement: 凭据、材料与报告内容有明确脱敏边界

真实联调 SHALL 只从本机 `.env` 或进程环境读取模型与 Zep 密钥，MUST NOT 把密钥写入仓库、日志、报告、命令行参数或错误响应。模型与 Zep 密钥 MUST 只注入需要它们的运行环境：`engine` 接收 smoke 专用模型/Zep 配置与 `ZEP_ACCEPTANCE_GRAPH_ID`；业务 `worker` 只接收业务模型与业务 Zep 配置，MUST NOT 接收 `ZEP_ACCEPTANCE_GRAPH_ID`；`app` MUST NOT 接收任何模型或 Zep 密钥。

日志与报告 SHALL 记录依赖名称、阶段、调用次数、耗时与脱敏结果；模型项证据 MUST 包含精确 `httpStatus`、兼容字段 `httpStatusClass` 与可选的 `providerErrorCode`；后者 MUST 只包含已识别、可安全公开的允许列表错误码，未知值留空。报告 MUST NOT 记录完整密钥、Authorization 头、完整模型请求/响应正文、完整 episode 正文或私有样本内容。业务建图报告与 smoke 报告 MUST NOT 互相包含对方的材料正文、节点、边或来源。

模型 HTTP 402 MUST 归类为 `billing_required`，403 且有明确 Cloudflare 1010 证据 MUST 归类为 `edge_blocked`，401 与普通 403 MUST 保留 `auth_failed`，这些错误均只调用一次。

#### Scenario: 报告不包含密钥

- **WHEN** 使用一个具有唯一可识别值的测试密钥执行真实联调
- **THEN** `runtime/smoke-live/` 下的报告与命令输出中找不到该值
- **AND** 也不出现 `Authorization` 头的完整内容或带凭据的完整 URL

#### Scenario: 模型 HTTP 诊断字段脱敏且保留精确状态

- **WHEN** 模型端点返回 402、403/1010、普通 403 或 401
- **THEN** 报告分别记录 `billing_required`、`edge_blocked`、`auth_failed`、`auth_failed`
- **AND** 证据包含精确 `httpStatus` 与 `httpStatusClass`，并在可用时包含允许列表内的 `providerErrorCode`；未知或被回显的字符串留空
- **AND** 普通 403 只在无关元数据中出现 1010 时仍为 `auth_failed`
- **AND** 不保存密钥、请求头全集或原始响应正文
- **AND** 每项实际调用次数为 1

#### Scenario: 只使用原创验收材料

- **WHEN** 模型项与图谱项执行
- **THEN** 它们只使用本次运行生成的原创微型材料、运行标识与唯一事实
- **AND** 不读取项目材料、`private-inputs/` 或 25 页私有样本
- **AND** 报告中材料来源标记为 `acceptance-fixture`

#### Scenario: 业务 worker 凭据边界

- **WHEN** 审阅业务 worker、`app` 与 `engine` 的环境
- **THEN** `app` 不包含任何模型或 Zep 密钥
- **AND** 业务 worker 只包含业务模型/Zep 配置，不包含 `ZEP_ACCEPTANCE_GRAPH_ID`
- **AND** 只有 `engine` 包含 `ZEP_ACCEPTANCE_GRAPH_ID` 与 smoke 专用配置

#### Scenario: 业务建图不读取验收图谱

- **WHEN** 业务 worker 执行项目建图
- **THEN** 只使用该项目绑定记录中的图谱标识
- **AND** 不读取或写入验收专用图谱，不生成 smoke 报告
- **AND** 业务建图的节点、边、来源与材料名称不进入 smoke 报告

### Requirement: 引擎运行环境只获得所需代码与显式配置

`engine` 运行环境 SHALL 只包含引擎适配器、离线契约、显式注入的配置与报告目录；MUST NOT 通过整仓挂载或镜像复制把 `.env`、私有材料、项目材料、历史验收报告或数据库凭据暴露给引擎。模型与 Zep 密钥 MUST 只通过显式环境变量注入，报告目录 MUST 只挂载 `runtime/smoke-live`。

#### Scenario: 引擎镜像不包含整仓

- **WHEN** 构建并启动 `engine` 运行环境
- **THEN** 镜像只包含 `app/engines/`、必要的包初始化文件与引擎依赖
- **AND** 不包含 `.env`、`.git/`、`private-inputs/`、项目材料目录或 `runtime/verify/` 历史报告
- **AND** 运行时不把仓库根目录挂载到容器内

#### Scenario: 引擎只接收显式配置与报告目录

- **WHEN** `make smoke-live` 启动引擎
- **THEN** 只注入模型/Zep 相关白名单变量与引擎超时配置
- **AND** 只把 `runtime/smoke-live` 挂载为可写报告目录
- **AND** 数据库密码、会话配置与材料存储路径不出现在引擎环境中

### Requirement: 依赖组合只有实际验证后才能成为可用基线

报告 SHALL 记录候选组合的精确版本、目标平台（Linux aarch64）、Python 版本，以及依赖解析、容器安装、导入与最小运行四个阶段的独立结果。只有四个阶段都在目标容器中实际成功，`dependencyBaseline.status` 才可以为 `verified`；仅存在于依赖清单、文档或本地缓存 MUST NOT 记为已验证。候选 `camel-ai` 的 Pydantic 约束若与当前应用环境冲突，MUST 使用隔离的引擎运行环境，MUST NOT 通过降级 `app` 的 Pydantic 来强行共用。

业务建图的 Zep 客户端 MUST 只进入独立 worker 构建目标，MUST NOT 迫使 `app` 安装 camel/oasis 或降低 `app` 的 Pydantic；smoke 候选依赖仍只进入 `engine`。`app` 与 smoke `engine` 的既有依赖集合与版本 MUST 保持不变；worker 只允许增加业务 Zep 客户端及其必要传递依赖。

#### Scenario: 四阶段全部成功才记录为已验证基线

- **WHEN** 候选 `camel-oasis==0.2.6`、`camel-ai==0.2.90`、`zep-cloud==4.1.0` 在目标 Linux aarch64、Python 3.11 容器中完成解析、安装、导入与最小运行
- **THEN** 报告记录四个阶段均成功，`dependencyBaseline.status` 为 `verified`
- **AND** 报告记录实际 Python 版本、平台架构与解析出的传递依赖版本

#### Scenario: 解析冲突时不得宣称基线可用

- **WHEN** 依赖解析因 Pydantic 上限或其它版本冲突失败
- **THEN** `dependencyBaseline.status` 为 `unverified`，并记录冲突的包与约束
- **AND** 命令以非零状态结束，后续真实项标记为 `not_run`，原因码为 `dependency_unresolved`
- **AND** 不通过修改 `app/requirements.txt` 降级应用依赖来消除冲突

#### Scenario: 隔离环境不改变应用既有依赖

- **WHEN** 使用隔离的引擎运行环境安装候选组合
- **THEN** `app` 与 smoke `engine` 的既有依赖集合与版本保持不变
- **AND** 业务 worker 只在独立构建目标增加 Zep 客户端，不安装 camel/oasis
- **AND** 默认 `make up`、默认 `make verify` 仍可在未安装引擎候选包时通过
- **AND** 报告记录隔离环境与应用环境的依赖差异

### Requirement: 失败处理覆盖缺配置、凭据、超时、限流、输出与索引

`make smoke-live` SHALL 对缺配置、失效凭据、超时、限流、模型输出不合规、索引等待超时与单个平台失败给出确定处理与原因码，并在报告中保留已完成的其它项证据。缺配置 MUST NOT 发起调用；失效凭据 MUST NOT 重试；超时与限流 MUST 在各自总时限与重试预算内结束；模型输出不合规 MUST NOT 计为通过；索引等待超时 MUST 保留已受理事实但判定图谱项失败；单个平台失败 MUST 只影响该平台与双平台总判定。

#### Scenario: 失效凭据快速失败

- **WHEN** 模型或图谱返回 401 或 403
- **THEN** 对应项标记为 `failed`，原因码为 `auth_failed`
- **AND** 不重试，不继续后续依赖该凭据的步骤
- **AND** 其它不依赖该凭据的项仍按自己的前置条件执行或标记 `not_run`

#### Scenario: 临时失效凭据恢复后可以重跑成功

- **WHEN** 开发者用临时覆盖把某个模型或图谱凭据替换为失效值并执行 `make smoke-live`
- **THEN** 对应项以 `auth_failed` 快速失败且不重试，报告记录临时覆盖已生效
- **AND** 恢复有效凭据后再次执行同一命令，全部必需项可以通过并以退出码 0 结束
- **AND** 有效凭据不被写入仓库、报告或日志，临时覆盖只存在于本次进程环境

#### Scenario: 超时与限流不无限重试

- **WHEN** 模型、图谱写入或平台动作遇到超时或 429
- **THEN** 每项按各自规范的总时限与最多 1 次重试预算结束
- **AND** 报告记录重试次数、真实调用次数与最终原因码
- **AND** 超过预算后不得继续等待或发起新的调用

#### Scenario: 模型输出不合规或索引超时

- **WHEN** 模型返回 HTTP 200 但字段、类型或来源引用不合格，或图谱在索引截止时间内未命中
- **THEN** 模型项或图谱项标记为 `failed`，原因码分别为 `invalid_output`/`source_mismatch` 与 `index_timeout`
- **AND** 报告保留图谱已受理标识与等待时间，但整体不得为 `passed`

### Requirement: 离线通过不能替代真实联调签收

离线 `make verify` 与 `make verify CHANGE=2026-11-21-connect-engine-providers` 的报告 MUST 标记 `mode=offline-contract` 或等价字段，MUST 明确真实调用次数为 0，MUST NOT 声称模型、图谱或双平台已在真实服务上联通。只有 `mode=live` 且全部必需项通过的 `make smoke-live` 报告可以用于真实联调签收，且仍需人工验收记录与同一 SHA 对齐。

#### Scenario: 离线报告明确没有真实调用

- **WHEN** 查看 `make verify CHANGE=2026-11-21-connect-engine-providers` 的报告
- **THEN** 报告记录真实调用为 0、测试替身调用次数与离线契约结果
- **AND** 不包含“真实模型已联通”“图谱已检索命中”“双平台已运行”之类结论

#### Scenario: 真实联调签收需要 live 报告与人工记录

- **WHEN** 验收人准备签收真实联调
- **THEN** 必须引用一份同 SHA 的 `mode=live` 报告，其中全部必需项为 `passed`、`dirty=false`，且 `tested_sha` 与候选提交一致
- **AND** 人工验收记录写入 `docs/acceptance/`，不由 `make smoke-live` 自动创建或修改
- **AND** 仅有离线报告或 `dirty=true` 的报告不足以签收
