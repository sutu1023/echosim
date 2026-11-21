# Spec Delta

## Purpose

让服务端能够用一份原创微型材料从模型获得可核对的结构化事实，并把“上游返回 200”与“结果真的可用”严格区分：字段、类型、来源引用与输入偏移全部合格才算通过。

## ADDED Requirements

### Requirement: 模型配置只从服务端注入

系统 SHALL 从服务端配置读取 Chat Completions 兼容 API 根地址、模型名与密钥，并在三者任一缺失时把模型项标记为“因缺少配置未执行”，MUST NOT 使用硬编码地址、默认密钥或回退到其它模型。模型密钥 MUST 只注入需要它的引擎运行环境，MUST NOT 出现在日志、报告、错误响应或测试夹具中。

#### Scenario: 缺少任一配置时不发起调用

- **WHEN** 模型地址、模型名或密钥至少一项缺失
- **THEN** 报告把模型项标记为 `not_run`，原因码为 `missing_config`
- **AND** 本次运行对模型服务的真实调用次数为 0
- **AND** 命令以非零状态结束，且不把该情形计为通过

#### Scenario: 配置齐备时只使用服务端配置

- **WHEN** 三项模型配置齐备
- **THEN** 请求发往配置中的地址与模型名，并使用配置中的密钥
- **AND** 报告中只出现依赖名称、阶段、调用次数、耗时与脱敏结果，不出现地址中的凭据、密钥或完整请求体

### Requirement: 只用原创验收材料请求结构化结果

模型调用 SHALL 只发送本次运行生成的原创微型材料与运行标识，MUST NOT 读取、上传或引用任何项目材料、私有样本目录内容或既有材料正文。验收材料 MUST 在运行时构造并在结束后丢弃，不提交到仓库、不写入 `runtime/` 的持久夹具、不进入 CI 产物。

#### Scenario: 调用材料来自本次运行的原创夹具

- **WHEN** 模型项开始执行
- **THEN** 请求体中的材料标识与本次运行标识一致
- **AND** 材料正文是运行时生成的原创短文本，不包含任何项目材料或私有样本片段
- **AND** 报告记录材料来源为 `acceptance-fixture`，不记录完整正文

#### Scenario: 私有样本不进入模型调用

- **WHEN** 仓库外或 `private-inputs/` 中存在私有样本
- **THEN** 模型项不读取该目录、不把样本文件名或正文放进请求
- **AND** 日志与报告中也找不到该样本的文件名或正文片段

### Requirement: 结构化结果必须通过字段、类型与来源校验

系统 SHALL 要求模型返回一个 JSON 对象，至少包含 `materialId`、`summary` 与非空 `facts` 数组；每个事实 MUST 包含 `text`、`evidenceQuote`、`sourceStart`、`sourceEnd` 与 `kind`，其中 `kind` MUST 属于预先声明的枚举。`materialId` MUST 等于本次输入材料标识，`sourceStart` 与 `sourceEnd` MUST 是以 UTF-16 代码单元计数的左闭右开区间，且输入材料在该区间切出的文本 MUST 与 `evidenceQuote` 完全一致。至少一条事实的 `evidenceQuote` MUST 包含本次运行的唯一标识。单凭 HTTP 200 或“能够解析成 JSON” MUST NOT 判定为通过。

#### Scenario: 合格结果通过全部校验

- **WHEN** 模型返回的 JSON 包含全部必需字段，类型正确，事实非空，至少一条事实包含本次运行标识
- **AND** 每个事实的 `sourceStart`/`sourceEnd` 在输入材料中切出的文本等于其 `evidenceQuote`
- **THEN** 模型项判定为通过，报告记录事实条数、来源匹配数、运行标识命中为真
- **AND** 报告记录真实调用次数与总耗时

#### Scenario: 200 但结果不可解析或字段不合格

- **WHEN** 上游返回 HTTP 200，但响应不是 JSON 对象、缺少必需字段、字段类型错误、`facts` 为空、`kind` 不在枚举内，或 `materialId` 与输入不一致
- **THEN** 模型项判定为失败，原因码为 `invalid_output`
- **AND** 报告记录失败的具体校验项，不把 HTTP 200 计为成功

#### Scenario: 来源引用与输入不对应

- **WHEN** 任一事实的偏移越界、区间切出的文本与 `evidenceQuote` 不一致，或没有一条事实包含本次运行标识
- **THEN** 模型项判定为失败，原因码为 `source_mismatch` 或 `marker_missing`
- **AND** 报告记录不匹配的事实数量与失败类别，不记录完整材料正文

### Requirement: Chat Completions 地址拼接与首次请求契约

系统 SHALL 把 `MODEL_API_BASE_URL` 视为 API 根地址（可以包含 `/v1`），在拼接 `/chat/completions` 时 MUST 保证最终路径中只出现一次 `/chat/completions`。首次请求 MUST 使用 `model`、`messages` 与 `response_format={"type":"json_object"}`，并 MUST 设置 `User-Agent: EchoSim/0.1.0`；正常调用、移除参数重试与网络重试 MUST 使用同一 User-Agent。字段、类型、UTF-16 偏移、原文引用与运行标识仍由 EchoSim 校验。具体供应商、模型名与地址由本机配置提供，兼容性 MUST 在真实联调中确认。

#### Scenario: 根地址包含 /v1 时只拼一次路径

- **WHEN** `MODEL_API_BASE_URL` 以 `/v1` 结尾
- **THEN** 请求路径只出现一次 `/chat/completions`，不出现 `/v1/v1/chat/completions` 或重复的 `/chat/completions`
- **AND** 请求发往配置中的主机与模型名

#### Scenario: 根地址不含 /v1 时同样只拼一次路径

- **WHEN** `MODEL_API_BASE_URL` 不以 `/v1` 结尾
- **THEN** 请求路径同样只出现一次 `/chat/completions`
- **AND** 不因缺少或重复 `/v1` 改变请求目标

#### Scenario: 首次请求使用 json_object 与 EchoSim User-Agent

- **WHEN** 模型项开始执行
- **THEN** 首次请求体包含 `model`、`messages` 与 `response_format={"type":"json_object"}`
- **AND** 请求头包含 `User-Agent: EchoSim/0.1.0` 与 Bearer 鉴权
- **AND** 模型名与消息中的材料来自本机配置与本次原创夹具

### Requirement: 仅当上游明确拒绝 response_format 时移除参数重试

只有上游返回 400 或 422，并明确表示 `response_format` 不受支持时，系统 MAY 移除该参数后重试 1 次；重试 MUST 使用同一份材料与同一套字段、类型和来源校验。认证失败、权限不足、限流、服务端错误、超时、其它 400/422 原因或结果不合格 MUST NOT 触发移除参数的重试。所有重试、移除参数重试与 SDK 内置重试 MUST 共用 30 秒、最多 2 次实际 HTTP 请求的预算。

#### Scenario: 上游明确不支持 response_format 时移除参数重试

- **WHEN** 第一次调用返回 400 或 422，且响应明确指出 `response_format` 不受支持，尚未收到任何可解析结果
- **THEN** 系统最多再发起 1 次不带 `response_format` 的调用，并在提示中要求只返回同一 JSON 契约
- **AND** 重试结果仍执行字段、类型、来源引用与运行标识校验
- **AND** 通过时报 `fallbackUsed=true` 与触发原因码，实际 HTTP 请求次数为 2

#### Scenario: 认证失败不触发移除参数重试

- **WHEN** 第一次调用返回 401 或 403
- **THEN** 模型项立即判定为失败，原因码为 `auth_failed`
- **AND** 不再发起移除参数重试，真实调用次数为 1
- **AND** 报告不包含密钥、Authorization 头或响应正文

#### Scenario: 两次调用后仍不合格

- **WHEN** 第一次调用触发移除参数重试，第二次调用仍返回不合格结果
- **THEN** 模型项判定为失败，原因码为 `invalid_output` 或 `source_mismatch`
- **AND** 不再发起第三次调用，报告记录两次调用的耗时与失败校验项

#### Scenario: SDK 内置重试也计入预算

- **WHEN** 模型客户端或 SDK 在内部对同一次调用自动重试
- **THEN** 每次实际 HTTP 请求都计入模型项的调用次数
- **AND** 实际请求总数达到 2 次后不得再发起请求，即使 SDK 仍希望重试
- **AND** 报告记录实际 HTTP 请求次数，而不是只记录逻辑调用次数

### Requirement: 超时、限流与网络失败有确定的重试预算

模型调用 SHALL 使用固定总时限与最多 2 次实际 HTTP 请求的预算，SDK 内置重试也计入该预算。对 408、429、502、503、504 与连接超时，系统 MAY 在总预算内重试 1 次；除“400/422 明确指出 `response_format` 不受支持”这一种情形外，对 400、401、403、404、422、结果不合格与来源不匹配 MUST NOT 重试。重试与移除参数重试共用同一预算，不得叠加。超过总时限后 MUST 立即以失败结束，不得把部分结果记为通过。

#### Scenario: 限流后一次重试成功

- **WHEN** 第一次调用返回 429，且预算尚未耗尽
- **THEN** 系统最多等待 1 秒后重试 1 次，总请求次数不超过 2
- **AND** 第二次结果按同一套契约校验，通过时报告 `retryUsed=true`

#### Scenario: 限流重试仍失败

- **WHEN** 第二次调用仍返回 429
- **THEN** 模型项判定为失败，原因码为 `rate_limited`
- **AND** 真实调用次数为 2，不再发起第三次调用

#### Scenario: 超时后不产生部分成功

- **WHEN** 调用在总时限内未完成
- **THEN** 模型项判定为失败，原因码为 `timeout`
- **AND** 报告记录已发生的调用次数与耗时，不记录任何未校验的响应内容
- **AND** 模型项不得被标记为通过

### Requirement: HTTP 错误保留精确状态码并按 402/403/401 分类

模型项证据 SHALL 记录精确的 `httpStatus` 与兼容字段 `httpStatusClass`。HTTP 402 MUST 归类为 `billing_required`；HTTP 403 且响应包含明确的 Cloudflare 1010 证据时 MUST 归类为 `edge_blocked`；HTTP 401 与普通 403 MUST 保留 `auth_failed` 语义。上述错误 MUST NOT 重试，MUST NOT 触发移除 `response_format` 的重试。报告 MAY 记录脱敏的 `providerErrorCode`，MUST NOT 保存密钥、请求头全集或原始响应正文。

#### Scenario: 402 归类为 billing_required

- **WHEN** 模型端点返回 HTTP 402，并提示余额或支付方式问题
- **THEN** 模型项判定为失败，原因码为 `billing_required`
- **AND** 报告记录 `httpStatus=402`、`httpStatusClass=4xx` 与脱敏的供应商错误码
- **AND** 实际 HTTP 调用次数为 1，不重试

#### Scenario: 403 且明确 Cloudflare 1010 归类为 edge_blocked

- **WHEN** 模型端点返回 HTTP 403，响应包含明确的 Cloudflare 1010 证据
- **THEN** 模型项判定为失败，原因码为 `edge_blocked`
- **AND** 报告记录 `httpStatus=403` 与 `providerErrorCode=1010`
- **AND** 实际 HTTP 调用次数为 1，不重试

#### Scenario: 普通 403 保留 auth_failed

- **WHEN** 模型端点返回 HTTP 403，但没有明确的 Cloudflare 1010 证据
- **THEN** 模型项判定为失败，原因码为 `auth_failed`
- **AND** 报告记录 `httpStatus=403`
- **AND** 实际 HTTP 调用次数为 1，不重试

#### Scenario: 401 保留 auth_failed

- **WHEN** 模型端点返回 HTTP 401
- **THEN** 模型项判定为失败，原因码为 `auth_failed`
- **AND** 报告记录 `httpStatus=401`
- **AND** 实际 HTTP 调用次数为 1，不重试
