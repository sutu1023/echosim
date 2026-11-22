# Spec Delta

## MODIFIED Requirements

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
