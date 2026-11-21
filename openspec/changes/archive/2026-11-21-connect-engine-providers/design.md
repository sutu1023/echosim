# Design

## Context

本变更建立在 Change 05 合并后的服务端基础上：`app` 负责 HTTP 与同域静态托管，`worker` 负责从 PostgreSQL 执行长任务，`scripts/verify.py` 只用 Python 标准库完成可重复验收，`/health` 与 `/livez` 的语义已固定。当前应用运行时使用 Python 3.11、FastAPI 0.165.0、pydantic-settings 2.16.3；本次规划时本机应用虚拟环境观察到 Pydantic 2.14.4。仓库目前没有任何模型、图谱或 OASIS 依赖。

目标运行环境是 Linux aarch64 容器、Python 3.11。当前开发机也是 Linux aarch64，Docker 服务端架构为 arm64。候选组合为 `camel-oasis==0.2.6`、`camel-ai==0.2.90`、`zep-cloud==4.1.0`，但尚未完成四阶段验证，因此本设计不把任何组合写成“已验证基线”。

本机 `.env` 中是否已有模型或 Zep 凭据：**未核实**。本轮规划与实现前检查均不读取 `.env` 的真实值；真实联调需要由开发者在本机填写模型配置与验收图谱 UUID。

本变更只做服务端接入与联通验收。完整证据图谱、角色与环境准备、正式双平台推演、报告生成与工作台页面属于后续变更；本设计不引入新的产品 API，也不改变现有任务、材料、会话与健康检查契约。

## Goals / Non-Goals

**Goals:**

- 用同一套领域契约同时驱动真实适配器与离线替身，使“字段合格”“写入受理”“检索命中”“行动记录”都能被机器判定。
- 把真实联调与离线回归彻底分开：没有凭据时 `make verify` 仍完整通过；真实调用只能由 `make smoke-live` 显式触发。
- 把引擎依赖与 `app`/`worker` 的运行时隔离，避免候选包的 Pydantic 上限反向锁死应用依赖。
- 让每次真实运行都留下可复核、脱敏、绑定 SHA 的报告，并让“未执行”与“失败”都导致非零退出。
- 让重复写入、动作超时与索引延迟不会产生无法解释的重复数据或虚假成功。
- 让缺失配置按依赖分别阻断，本地 OASIS 动作不被无关的外部密钥缺失拖住。

**Non-Goals:**

- 不实现证据图谱的实体消歧、关系推理、图数据库持久化或图算法。
- 不实现角色配置、环境参数、正式推演编排、报告生成、深度互动或工作台页面。
- 不把真实引擎调用接入用户请求、后台任务队列或材料解析流程。
- 不引入新的数据库表或迁移；本变更没有需要持久化的业务实体。
- 不在 CI 中执行真实联调，也不把 `make smoke-live` 的结果自动写入 `docs/acceptance/`。
- 不实现生产级配额、计费、审计、密钥轮换或多租户图谱隔离。

## Decisions

### 1. 领域契约、适配器与编排分离

新增以下模块，保持“纯契约可离线、SDK 只在真实适配器内导入”的边界：

| 模块 | 职责 | 是否可离线运行 |
| --- | --- | --- |
| `app/engines/contracts.py` | 材料夹具、结构化结果模型、校验函数、图谱状态枚举、行动记录、报告项与原因码 | 是，仅标准库 |
| `app/engines/settings.py` | 从环境变量构造引擎配置，提供缺配置检测与安全默认值 | 是，仅标准库 |
| `app/engines/model.py` | Chat Completions 兼容接口调用、`response_format` 移除重试与预算 | 否，真实网络 |
| `app/engines/graph.py` | `graph.episode.add` 写入、`graph.search_episodes` 检索、受理核对与幂等键 | 否，真实网络 |
| `app/engines/simulation.py` | OASIS 的 `twitter`/`reddit` 本地平台、原生动作与记录读回 | 否，本地 SDK |
| `app/engines/smoke.py` | 真实联调编排、逐项状态、调用计数、报告与退出码 | 否，编排真实项 |
| `app/engines/offline.py` | 用测试替身驱动同一契约的离线场景，供 `make verify CHANGE=…` 调用 | 是，仅标准库 |

`contracts.py` 不导入任何第三方包；`model.py`、`graph.py`、`simulation.py` 只在函数内部延迟导入 SDK，确保 `app` 启动与离线验收不会因为引擎依赖缺失而失败。替代方案是把所有适配器直接写进 `app/services/` 并共用应用依赖；否决原因是会把候选 SDK 的版本约束带入应用基线，且离线验收会被迫安装重型依赖。

### 2. 引擎运行时隔离，不与应用共用 Pydantic 解析结果

候选 `camel-ai==0.2.90` 对 Pydantic 有上限约束，而应用当前使用 Pydantic 2.14.4、pydantic-settings 2.16.3、FastAPI 0.165.0。规划时仓库内没有候选包的元数据，因此其精确上限必须以目标容器中的解析器输出为准；在依赖解析任务完成前，不得把任何上限数字写成已验证事实。若直接在应用镜像中安装候选包，解析器可能要求把 Pydantic 降到应用基线之下；即使降级后表面可导入，也会把应用依赖的稳定性绑到一个只用于联通验收的 SDK 上。候选 `camel-oasis` 还会带来一组本地仿真依赖，其在 aarch64 上的 wheel/编译情况尚未验证。

因此选择**独立引擎镜像**：

- 新增 `app/requirements-engines.txt`，只锁定候选组合及其直接依赖；新增 `Dockerfile.engine`，从 `python:3.11-slim-bookworm` 构建，不安装 `app/requirements.txt` 的完整应用依赖。
- 引擎镜像只复制 `app/engines/`、必要的包初始化文件、引擎依赖清单与入口代码；MUST NOT 复制 `.env`、`.git/`、`private-inputs/`、项目材料目录或历史验收报告。
- `docker-compose.yml` 增加 `engine` 服务，使用 `profiles: ["live"]`，不发布端口，只接收模型/Zep 白名单变量与引擎超时配置；只把 `runtime/smoke-live` 挂载为可写报告目录，MUST NOT 把仓库根目录挂载进容器。`make up` 默认不构建、不启动它；`make smoke-live` 使用该 profile 执行一次性命令。
- 引擎运行时使用 `app/engines/settings.py` 读取环境变量，不导入 `app.config`（后者依赖 pydantic-settings）。`app/config.py` 仍按仓库规则增加同名配置项与安全默认值，供应用侧文档与未来接入使用；`tests/` 增加断言，确保两处配置名与默认值一致，避免漂移。
- `app` 与 `worker` 不接收 `MODEL_API_KEY`、`ZEP_API_KEY`；Compose 只对 `engine` 显式注入这些变量。

**候选基线的判定**：只有在目标 Linux aarch64、Python 3.11 容器中依次完成“依赖解析 → 容器安装 → 导入 → 最小运行”四个阶段，报告中的 `dependencyBaseline.status` 才允许为 `verified`。解析失败、编译失败、导入失败或最小运行失败都必须记录为 `unverified` 与具体阶段，且 `make smoke-live` 以非零状态结束。不得因为依赖清单写了版本、文档描述了组合或本地缓存存在包，就宣称基线可用。

**备选方案**：与应用共用镜像，在 `app/requirements.txt` 中锁定候选包。否决原因：Pydantic 上限冲突会迫使应用降级；引擎包体积与 aarch64 编译风险会污染默认 `make up`/CI；离线验收将无法在未安装引擎依赖时完成。未来若候选组合与应用依赖完全兼容，可在独立验证后合并，但不属于本变更。

### 3. 配置与密钥流

新增配置项及默认值：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MODEL_API_BASE_URL` | 空 | Chat Completions 兼容 API 根地址，可包含 `/v1`；空表示未配置 |
| `MODEL_API_KEY` | 空 | 模型密钥；只注入 `engine` |
| `MODEL_NAME` | 空 | 模型名；空表示未配置 |
| `MODEL_TIMEOUT_SECONDS` | `30` | 模型项总时限 |
| `ZEP_API_KEY` | 空 | Zep 密钥；只注入 `engine` |
| `ZEP_API_BASE_URL` | 空 | 可选的 Zep 服务地址；空表示使用 SDK 默认 |
| `ZEP_ACCEPTANCE_GRAPH_ID` | 空 | 验收专用图谱的 `graph_uuid`；空表示未配置 |
| `GRAPH_WRITE_TIMEOUT_SECONDS` | `20` | 单次写入时限 |
| `GRAPH_STATUS_TIMEOUT_SECONDS` | `10` | 写入响应丢失后的单次核对时限 |
| `GRAPH_INDEX_TIMEOUT_SECONDS` | `60` | 索引等待上限 |
| `GRAPH_POLL_INTERVAL_SECONDS` | `3` | 索引期间检索间隔 |
| `SIMULATION_TIMEOUT_SECONDS` | `60` | 单个平台的动作与读回总时限 |
| `SMOKE_LIVE_TOTAL_TIMEOUT_SECONDS` | `300` | 整次 `make smoke-live` 总时限 |

`.env.example` 只写明显占位值，例如 `https://model.example.invalid/v1`、`replace-with-model-key`、`replace-with-zep-key`、`00000000-0000-0000-0000-000000000000`，不写任何可用凭据。`.env` 继续被忽略。Compose 对 `engine` 使用显式 `environment:` 列表，不把数据库密码、会话配置或材料路径传给引擎。

日志与报告只允许出现：依赖名称、阶段、调用次数、耗时、HTTP 状态类别、原因码、布尔校验结果、本次 `runId`、`episodeId`、`idempotencyKey`、平台键、原生平台、原生动作、目标帖标识与角色。禁止出现密钥、`Authorization` 头、带凭据的完整 URL、完整模型请求/响应正文、完整 episode 正文、材料正文或私有样本内容。

### 4. 模型接入：Chat Completions、json_object 与严格校验

模型适配器调用 Chat Completions 兼容端点。`MODEL_API_BASE_URL` 是 API 根地址，可以包含 `/v1`；适配器拼接 `/chat/completions` 时必须规范化，使最终路径中只出现一次 `/chat/completions`，不得出现 `/v1/v1/chat/completions` 或重复路径。具体供应商、模型名与地址由本机配置提供，兼容性在真实联调中确认。

首次请求使用：

```json
{
  "model": "<MODEL_NAME>",
  "messages": [{"role": "user", "content": "<原创材料与 JSON 契约>"}],
  "response_format": {"type": "json_object"},
  "temperature": 0
}
```

正式请求头固定包含 `Authorization: Bearer <KEY>`、`Content-Type: application/json`、`Accept: application/json` 与 `User-Agent: EchoSim/0.1.0`。正常调用、移除参数重试与网络重试 MUST 使用同一 User-Agent；不得回退到 `Python-urllib/*` 默认值。

请求体中的材料是运行时生成的原创短文本：

```
河畔社区图书角在 2026 年 3 月 14 日收到编号 ECHOSIM-ACCEPT-<runId> 的蓝色书箱。
志愿者林舟负责在每周六 10:00 开放借阅，并把新到书目登记在纸质清单上。
居民可以在留言簿写下推荐书目，但不允许把书带出活动室。
```

期望的 JSON 契约：

```json
{
  "materialId": "acceptance-<runId>",
  "summary": "非空字符串",
  "facts": [
    {
      "text": "非空字符串",
      "evidenceQuote": "输入材料中的精确子串",
      "sourceStart": 0,
      "sourceEnd": 12,
      "kind": "schedule|ownership|constraint"
    }
  ]
}
```

请求按原文行附上 UTF-16 起止位置，换行符计入下一行起点。模型从行文本选择证据并生成事实、摘要和分类，服务端以原文独立验证引用和位置。Go 订阅端点复用同一次运行的会话标识，`deepseek-v4.1-flash` 用非思考模式完成此短提取，以遵守既有时间预算。

校验顺序固定为：JSON 对象 → 必需字段 → 类型 → `materialId` 一致 → `facts` 非空且每条字段完整 → `kind` 枚举 → 偏移为整数且 `0 <= sourceStart < sourceEnd <= UTF-16 长度` → 输入材料切片等于 `evidenceQuote` → 至少一条事实包含 `ECHOSIM-ACCEPT-<runId>`。偏移按 UTF-16 代码单元计数、左闭右开，与材料分块的既有约定一致；不做换行归一或空白折叠，避免“看起来相似”被误判为精确匹配。HTTP 200 或“能解析成 JSON”都不构成成功。

**移除参数重试**：只有第一次调用返回 400/422，且响应明确指出 `response_format` 不受支持，且尚未得到可解析结果时，允许移除 `response_format` 后再试一次。第二次调用在提示中要求只返回同一 JSON 契约，并执行完全相同的校验。认证失败、权限不足、限流、5xx、超时、其它 400/422 原因与结果不合格都不触发移除参数重试。

**预算**：模型项最多 2 次实际 HTTP 请求、总时限 30 秒。重试、移除参数重试与 SDK 内置重试共用这 2 次预算，不得叠加；SDK 内部每次实际 HTTP 请求都计数。408/429/502/503/504/连接超时允许在预算内重试 1 次；400/401/403/404/422、结果不合格与来源不匹配不重试。超过预算立即失败，不把部分结果记为通过。

**HTTP 错误分类**：报告保留精确 `httpStatus` 与兼容字段 `httpStatusClass`。

| 状态 | 条件 | 原因码 | 调用次数 |
| --- | --- | --- | --- |
| 402 | 余额或支付方式问题 | `billing_required` | 1 |
| 403 | 响应有明确的 Cloudflare 1010 证据 | `edge_blocked` | 1 |
| 403 | 普通 403 | `auth_failed` | 1 |
| 401 | 鉴权失败 | `auth_failed` | 1 |

`providerErrorCode` 只记录允许列表内、已识别且可安全公开的错误码（当前为 `1010` 与 `insufficient_quota`）；未知值或被供应商回显的任意字符串一律留空。Cloudflare 1010 只从明确的 `error.code` / `error_code` 位置或 `error code: 1010` 文本识别；普通 403 即使无关元数据含 1010 仍归类 `auth_failed`。报告不保存密钥、请求头全集或原始响应正文。

**备选方案**：只接受结构化模式，不做移除参数重试。否决原因：部分兼容端点不支持 `response_format`，若直接失败会把“上游能力差异”误报为联通失败；但移除参数必须受严格触发条件与同一校验约束，不能变成“解析出任何 JSON 就算成功”。

### 5. 图谱接入：固定 graph_uuid、graph.episode.add 与 search_episodes

图谱适配器使用 `zep-cloud==4.1.0`，通过配置的 `ZEP_ACCEPTANCE_GRAPH_ID`（对应 `graph_uuid`）写入**预先配置的固定验收专用图谱**，不写入任何用户项目图谱，也不在每次运行时动态创建新图谱。每次运行生成不同 `runId`，并写入一条 episode，事实包含：

```
河畔社区图书角验收批次 <runId> 的蓝色书箱编号为 ECHOSIM-ACCEPT-<runId>，保管人是林舟。
```

写入使用 `graph.episode.add`。每次新的逻辑写入在重试循环前生成一次 `str(uuid.uuid4())` 作为 `idempotency_key`；同一次逻辑写入的所有重试 MUST 复用原键、原 `graph_uuid` 与原请求体；不得因超时、连接中断或搜索未命中而换键或改写请求体。两次独立运行生成不同的 UUIDv4 键。写入成功后保存服务端返回的 episode UUID。

状态机：

```
submitting ──2xx+episode UUID──> accepted ──首次未命中──> indexing ──精确命中──> searchable
     │                              │                      │
     ├─超时/连接中断/5xx─> checking ──已受理──> accepted
     │                              └──同一幂等键重试（预算内）──> accepted / write_unconfirmed
     └─400/401/403/422──> failed(invalid_request/auth_failed/write_failed)
```

写入响应丢失时，第一次 `graph.search_episodes` 未命中 **不能** 作为“肯定未写入”的证据，也不能成为换键重写的依据。系统保持原 `idempotency_key`、原 `graph_uuid` 与原请求体，在预算内继续搜索或用同一幂等键重试；写入与核对总时限不超过 60 秒，仍无法确认则判定 `write_unconfirmed`。

检索必须扫描完整批候选：元数据里的 marker 只用于筛选候选，不能单独构成命中；已知写入 UUID 时，命中项必须同时满足 UUID 一致、正文包含本次运行标记与约定唯一事实。错误 UUID 或错误事实的候选在前时不得提前失败，后面的精确命中仍然通过。

索引等待：默认每 3 秒用 `graph.search_episodes` 在同一图谱检索一次，上限 60 秒。`accepted` 表示写入已受理但尚未检索；`indexing` 表示至少一次检索未命中；`searchable` 表示返回的 episode UUID 与写入 UUID 一致，且内容包含 `runId` 与唯一事实。固定验收图谱可能同时返回旧记录；只要本次 episode 精确命中即可通过，旧记录本身不构成失败。只返回旧记录时继续等待或按 `index_timeout` 失败。检索阶段 429/5xx 只在截止时间内继续轮询，不延长截止时间，也不触发新写入。若运行标识出现在错误 UUID 上，判定 `stale_or_foreign_hit`。

离线契约必须增加“服务端已受理、响应丢失、首次搜索未命中”的回归：替身记录服务端已接收写入，客户端第一次读取响应失败，第一次搜索只返回空或旧记录；断言系统保持原幂等键与原请求体，后续精确命中则通过，预算耗尽则 `write_unconfirmed`。

### 6. 双平台仿真：twitter/create_post 与 reddit/create_comment

OASIS 的两个本地模拟平台使用稳定别名报告，并保留原生平台键与原生动作：

| 平台 | 原生平台 | 角色 | 有效动作 | 通过条件 |
| --- | --- | --- | --- | --- |
| `platform_a` | `twitter` | 居民代表 | `create_post` | 从 `twitter` 引擎记录读回：原生平台为 `twitter`、角色一致、原生动作为 `create_post`、内容含运行标识、结果成功 |
| `platform_b` | `reddit` | 值班志愿者 | 先建立本次目标帖，再 `create_comment` | 从 `reddit` 引擎记录读回：原生平台为 `reddit`、角色一致、原生动作为 `create_comment`、内容含运行标识、结果成功，且评论对应的 `targetPostId` 等于本次夹具目标帖 |

每个平台使用独立的临时目录、独立的本地数据库/进程与独立的 SDK 环境对象；不共享可变状态。目标帖是平台 B 的环境准备，不替代 `create_comment` 作为平台 B 的证据动作。动作结果必须从引擎记录读回，不能只使用动作调用返回值。动作超时或连接中断时，先读回记录：已存在且匹配则通过且不重发；明确不存在且预算允许才最多重试 1 次；无法确认则 `action_unconfirmed`，不盲目重试。单平台总时限 60 秒。

失败归属：

| 原因码 | 归属 | 处理 |
| --- | --- | --- |
| `shared_setup` | 两个平台共用 | 依赖导入、配置或共享运行时失败；两个平台标记 `not_run` 或失败，不归到 A/B |
| `platform_a_setup` / `platform_b_setup` | 单平台 | 该平台环境或 B 的目标帖创建失败；另一个平台继续 |
| `platform_a_action` / `platform_b_action` | 单平台 | 原生动作被拒绝或重试后仍失败；另一个平台继续 |
| `platform_a_record` / `platform_b_record` | 单平台 | 读回缺失、字段不匹配或 B 的评论指向其它帖子；另一个平台继续 |
| `platform_a_cleanup` / `platform_b_cleanup` | 单平台 | 资源未关闭；记录为运行失败，但不清除已获得的行动证据 |

清理在 `finally` 等价路径执行：停止本地进程/服务、关闭数据库与 SDK 客户端、删除 `runtime/smoke-live/<runId>/platform-a` 与 `platform-b`。清理失败必须记录并导致整体非零退出。双平台总判定只在 A 与 B 都通过时成立；一个平台通过不得抵消另一个平台的失败。

### 7. `make smoke-live` 编排、报告、脏工作树与退出码

`make smoke-live` 在 `engine` profile 中执行 `python -m app.engines.smoke`，流程为：

1. 检查工作树：默认拒绝脏工作树；仅在 `ALLOW_DIRTY=1` 时继续，并在报告中记录 `dirty=true`。
2. 读取配置并做依赖基线前置检查（不满足则相关项 `not_run`）。
3. 模型项：原创材料 → Chat Completions 请求 → 校验 / 移除参数重试 / 预算内重试。
4. 图谱项：`graph.episode.add` → 响应丢失核对 → 索引等待 → `graph.search_episodes` 精确检索。
5. 平台 A：`twitter` 环境 → `create_post` → 引擎记录读回 → 清理。
6. 平台 B：`reddit` 环境 → 目标帖 → `create_comment` → 引擎记录读回 → 清理。
7. 汇总逐项状态、调用计数、依赖基线与 SHA，写报告并决定退出码。

**缺配置按依赖分别判定**：缺模型配置只阻断模型项；缺 Zep 配置只阻断图谱项；本地 OASIS 动作不依赖这些外部密钥，仍应继续执行并记录证据。总结果因必需项缺失而非零，但不能把可独立运行的项合并成一个笼统失败。

报告写入 `runtime/smoke-live/last-run.json`，同时保留 `runtime/smoke-live/run-<UTC 时间戳>.json`。报告结构示例（值为示意，不含敏感内容）：

```json
{
  "result": "passed|failed",
  "mode": "live",
  "tested_sha": "<HEAD>",
  "dirty": false,
  "started_at": "<ISO-8601>",
  "finished_at": "<ISO-8601>",
  "dependencyBaseline": {
    "status": "verified|unverified",
    "target": "linux-aarch64",
    "python": "3.11",
    "pins": {
      "camel-oasis": "0.2.6",
      "camel-ai": "0.2.90",
      "zep-cloud": "4.1.0"
    },
    "stages": {
      "resolve": "passed|failed|not_run",
      "install": "passed|failed|not_run",
      "import": "passed|failed|not_run",
      "minimalRun": "passed|failed|not_run"
    }
  },
  "items": [
    {
      "id": "model.structured",
      "status": "passed|failed|not_run",
      "reason": null,
      "dependency": "model",
      "stage": "structured-extraction",
      "durationMs": 1234,
      "calls": {"real": 1, "double": 0},
      "evidence": {
        "fieldsValid": true,
        "sourceMatches": 3,
        "markerMatched": true,
        "fallbackUsed": false,
        "httpStatus": 200,
        "httpStatusClass": "2xx",
        "providerErrorCode": null
      }
    },
    {
      "id": "graph.searchable",
      "status": "passed|failed|not_run",
      "evidence": {
        "episodeId": "<uuid>",
        "idempotencyKey": "<runId>-episode",
        "state": "searchable",
        "indexWaitMs": 9000
      }
    },
    {
      "id": "simulation.platform_b",
      "status": "passed|failed|not_run",
      "evidence": {
        "platformKey": "platform_b",
        "nativePlatform": "reddit",
        "nativeAction": "create_comment",
        "targetPostId": "<post-id>",
        "recordId": "<record-id>"
      }
    }
  ],
  "calls": {"real": 6, "double": 0},
  "cleanup": {"platform_a": "passed", "platform_b": "passed"}
}
```

必需项固定为：`model.structured`、`graph.accepted`（作为 `graph.searchable` 的前置状态证据）、`graph.searchable`、`simulation.platform_a`、`simulation.platform_b`、`cleanup`、`dependencyBaseline`。只有全部通过，`result` 才为 `passed`，退出码为 0；任一必需项为 `failed` 或 `not_run`，退出码为 1；非法调用、脏工作树被拒绝或报告无法写出，退出码为 2 并给出原因。缺少配置时依赖项标为 `not_run`、原因码 `missing_config`，该项真实调用次数为 0，退出码为 1。

报告记录 `tested_sha` 与实际 `dirty`。默认在发起外部调用前拒绝脏工作树；`ALLOW_DIRTY=1` 时允许本地调试但记录 `dirty=true`，不得作为签收证据。签收要求 `mode=live`、全部必需项通过、`dirty=false`，且 `tested_sha` 与候选提交一致。调用计数按实际 HTTP/SDK 调用计数：模型移除参数重试算 2 次，SDK 内置重试计入，图谱写入、核对与每次检索分别计数，平台动作与读回分别计数。`calls.double` 在 `mode=live` 中必须为 0。

### 8. 离线验收与 CI：同一契约、测试替身、无真实网络

`scripts/verify.py` 继续只使用标准库。新增 `ENGINE_CHANGE_ID = "2026-11-21-connect-engine-providers"` 并注册到 `KNOWN_CHANGES`；不带 `CHANGE` 的默认流程保持 Change 01–05 全部场景，新增内容不改变既有断言。

Change 06 专属流程调用 `app/engines/offline.py` 的离线契约检查（可在宿主机以 `PYTHONPATH=. python3 -m app.engines.offline --json` 运行，也可在 `app` 容器内运行；该模块只依赖标准库）。离线检查使用进程内测试替身：

- 模型：合法结果通过；非法 JSON / 缺字段 / 类型错误 / 来源偏移不匹配失败；Chat Completions 路径只出现一次 `/chat/completions`；首次请求包含 `response_format={"type":"json_object"}`；仅 400/422 明确不支持 `response_format` 时移除参数重试一次；401 不重试；限流只重试一次；SDK 内置重试计入；缺配置不调用。
- 图谱：固定 `graph_uuid`；`accepted → indexing → searchable`；写入响应丢失后第一次搜索未命中；同一幂等键与原请求体重试；旧记录与本次记录同时返回时精确命中；错误 UUID 失败；预算耗尽 `write_unconfirmed`；认证失败不重试。
- 双平台：`platform_a/twitter/create_post` 与 `platform_b/reddit/create_comment` 都通过；仅 A 通过时总判定失败但保留 B 的证据；B 缺少目标帖或评论指向其它帖子失败；单平台 setup/action/record 失败只影响该平台；清理失败导致运行失败；共享依赖失败单独归因。
- 缺配置：只缺 Zep 时模型与 OASIS 仍可执行；只缺模型时图谱与 OASIS 仍可执行；两者都缺时 OASIS 仍可执行；总结果非零。
- 报告与退出码：全部必需项通过为 0；缺配置/单平台失败/索引超时为非零；未知 `CHANGE` 为 2。

离线检查结果写入 verify 报告的 `engine_evidence` 字段，至少包含 `mode=offline-contract`、逐场景状态、`calls.real=0`、`calls.double`、原因码与断言摘要。离线检查不得读取 `.env` 中的真实密钥，不得安装候选引擎依赖，不得访问模型、图谱或 OASIS 服务；所有测试替身都在进程内或本地临时目录完成。

`make up`、默认 `make verify` 与 CI 都不启用 `engine` profile，因此不会安装候选引擎依赖。Change 06 的离线场景也不构建 `Dockerfile.engine`；真实依赖基线只在开发者显式执行 `make smoke-live` 时验证。

**CI**：保留现有默认 `make verify` 工作，并增加独立的 Change 06 离线检查，调用 `make verify CHANGE=2026-11-21-connect-engine-providers`。两次检查使用不同 artifact 名称上传报告，分别断言 `tested_sha` 与触发 SHA 一致、`dirty=false`、`result=passed`；Change 06 专属检查额外断言 `calls.real=0`。CI 不注入模型/Zep 密钥，不执行 `make smoke-live`，不构建 live 引擎镜像。

### 9. 依赖解析、容器安装、导入与最小运行的任务化

实施时必须把四件事分开记录，不能合并成一个“装上了”的结论：

| 阶段 | 目标容器动作 | 证据 |
| --- | --- | --- |
| 依赖解析 | 在 Linux aarch64、Python 3.11 容器中运行解析器（如 `pip install --dry-run --report`） | 解析成功/冲突包、最终版本集合、是否需要降级 Pydantic |
| 容器安装 | 构建 `Dockerfile.engine` 并安装 `app/requirements-engines.txt` | 退出码、wheel/源码构建情况、安装耗时、镜像大小 |
| 导入 | 在容器内逐个导入实际模块并打印版本 | 三个包与关键传递依赖的版本、导入耗时 |
| 最小运行 | 不访问外部网络的本地最小脚本：构造客户端、创建并关闭一个最小仿真环境、验证图谱/模型适配器可实例化 | 退出码、资源关闭结果、未发起真实外部调用的证明 |

只有四阶段全部成功，才把候选组合记录为可用基线。若 Pydantic 上限导致解析失败，先在隔离引擎环境中尝试；隔离环境仍失败则记录 `unverified` 与冲突约束，`make smoke-live` 以非零状态结束，禁止通过修改 `app/requirements.txt` 降级应用依赖来“解决”冲突。若 aarch64 缺少 wheel 且源码构建失败，同样记录 `unverified`，不把 x86 本地成功外推为 aarch64 基线。

### 10. 能力 → 场景 → 验收入口 → 证据

| 能力 | 场景 | 验收入口 | 证据 |
| --- | --- | --- | --- |
| `structured-model-access` | Chat Completions 路径、json_object、User-Agent、合格结构化结果、非法输出、来源不匹配、移除参数重试、402/403-1010/普通 403/401、限流、超时 | `make smoke-live`（真实）；`make verify CHANGE=…`（替身） | `items.model.structured`、`fieldsValid`、`sourceMatches`、`markerMatched`、`fallbackUsed`、`httpStatus`、`httpStatusClass`、`providerErrorCode`、`calls.real`、`reason` |
| `evidence-graph-access` | 固定 graph_uuid、写入受理、仍在索引、可以检索、响应丢失、同键重试、索引超时、旧记录共存 | `make smoke-live`（真实）；`make verify CHANGE=…`（替身） | `items.graph.*`、`episodeId`、`idempotencyKey`、状态序列、`indexWaitMs`、`searchAttempts`、`episodeMatched`、`markerMatched` |
| `dual-platform-simulation` | twitter/create_post、reddit/目标帖/create_comment、记录读回、单平台失败、动作超时、清理失败 | `make smoke-live`（真实）；`make verify CHANGE=…`（替身） | `items.simulation.platform_a/b`、`platformKey`、`nativePlatform`、`nativeAction`、角色、结果、`recordId`、`targetPostId`、`cleanup` |
| `live-engine-smoke` | 全部通过、脏工作树拒绝、缺配置按依赖判定、凭据失效、超时、限流、单平台失败、依赖未验证 | `make smoke-live` | `runtime/smoke-live/last-run.json`、`result`、逐项状态、`calls.real/double`、`dependencyBaseline`、`dirty`、退出码 |
| `reproducible-verification` | 无密钥/无外网默认回归、Change 06 离线契约、未知 CHANGE、SHA 规则、CI 双检查 | `make verify`；`make verify CHANGE=2026-11-21-connect-engine-providers`；CI | `runtime/verify/last-run.json`、`engine_evidence`、`mode=offline-contract`、`calls.real=0`、两份不同名称的 CI artifact |
| 人工签收 | 真实联通结论、平台动作、报告签收 | 浏览器/人工流程，不在本变更自动化 | `docs/acceptance/` 记录，绑定同 SHA、`dirty=false` 的 `mode=live` 报告；不得由命令自动写入 |

**必须等待真实凭据与人工签收的结论**：模型真实调用成功、Zep 写入与检索命中、两个 OASIS 平台产生实际行动记录、`make smoke-live` 退出码 0、依赖组合在目标 aarch64 容器成为可用基线、真实联调可用于交付。离线 `make verify` 只证明契约、状态机与失败分类，不证明上述任何一项。

## Risks / Trade-offs

- [候选 `camel-ai` 的 Pydantic 上限与应用当前 2.14.4 冲突] → 使用独立引擎镜像；不在应用依赖中降级 Pydantic；解析失败时记录 `unverified` 并以非零退出，而不是伪造基线。
- [aarch64 上候选包缺少 wheel 或源码编译失败] → 在目标容器中记录解析/安装阶段失败；不把 x86 或本地缓存成功当作基线；实施任务要求保存完整日志摘要。
- [Zep 索引延迟超过 60 秒] → 状态机区分 `accepted` 与 `indexing`，超时保留受理证据但判定失败；不通过换键重写制造命中。
- [写入响应丢失后重试产生重复 episode] → 固定 `graph_uuid`、唯一 `idempotency_key`，所有重试复用原键与原请求体；搜索未命中不作为换键依据；无法确认即 `write_unconfirmed`。
- [模型输出不稳定或提示注入] → `temperature=0`、严格字段/类型/偏移校验；材料中的命令与提示只按普通文本处理；不合格即失败，不“尽力猜测”。
- [本地 OASIS 平台端口、临时目录或进程残留] → 每个平台独立目录与进程；`finally` 清理；清理失败使运行失败；报告保留证据。
- [真实密钥进入日志或报告] → 只注入 `engine`；引擎镜像与运行时不接触整仓、`.env`、私有材料或历史报告；日志/报告字段白名单与脱敏测试；不记录完整请求/响应。
- [离线替身与真实 SDK 行为漂移] → 共享 `contracts.py`；离线只证明契约与状态机；真实联调必须单独运行 `make smoke-live`，离线通过不能签收。
- [引擎镜像增大构建时间] → 使用 Compose profile，默认 `make up`/CI 不构建；`make smoke-live` 才构建。
- [`make smoke-live` 在脏工作树运行] → 默认拒绝；`ALLOW_DIRTY=1` 仅用于本地调试并记录 `dirty=true`；签收只接受 `dirty=false` 且 SHA 一致的 live 报告。

## Migration Plan

1. 新增配置项与 `.env.example` 占位值；`app/config.py` 给出安全默认值，Compose 只把模型/Zep 变量注入 `engine`，且只挂载 `runtime/smoke-live`。
2. 新增 `app/engines/` 纯契约与离线检查、真实适配器、`app/requirements-engines.txt`、`Dockerfile.engine` 与 `engine` profile；默认 `make up` 不启动、不构建引擎服务。
3. 在目标 Linux aarch64、Python 3.11 容器中执行依赖解析、安装、导入与最小运行；只有四阶段成功才记录基线。若失败，保留失败证据并停止真实联调，不修改应用依赖。
4. 先用离线替身完成 `make verify CHANGE=2026-11-21-connect-engine-providers`，确认默认 `make verify` 与 Change 01–05 回归不受影响。
5. 更新 CI：保留默认 verify，增加独立 Change 06 离线 job 与不同 artifact 名称；CI 不注入真实密钥、不执行 live、不构建引擎镜像。
6. 由开发者在 `.env` 注入真实密钥并填写 `ZEP_ACCEPTANCE_GRAPH_ID`（graph_uuid），显式运行 `make smoke-live`；缺少配置时验证依赖分别 `not_run`、本地 OASIS 继续执行、总结果非零。
7. 真实联调通过后，准备同 SHA、`dirty=false` 的人工签收清单；签收记录只写 `docs/acceptance/`，不归档、不合并，直到验收人确认。

**回滚**：本变更没有数据库迁移。回滚时移除 `engine` profile、引擎依赖清单与适配器、Change 06 CI job 与离线场景即可；`app`/`worker` 的既有依赖、接口与健康检查不受影响。已写入验收专用图谱的 episode 不影响产品数据；如需清理，按 `runId` 人工处理，不在自动流程中删除。`runtime/smoke-live/` 已被忽略，不进入提交。

## Resolved Decisions and Implementation-time Probes

五项决定已确认，不再作为开放问题。实施期仍需用真实环境探测的未知项（模型供应商兼容性、Zep SDK 返回形状、OASIS 原生 API 细节）已转为 `tasks.md` 中的具体验证任务；任何探测结果都不得改变已确认的预算、幂等、原生映射与离线/真实分离边界。
