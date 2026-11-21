# Proposal

## Why

材料入库已经能证明“原文与位置是真的”，但系统还没有证明三件更关键的事：模型能把材料变成可核对的结构化事实，证据图谱能把一条事实写入并检索回来，双平台仿真能在两个本地引擎里留下真实动作记录。如果这三条路径只停留在接口文档或测试替身，后续的证据图谱、角色准备与正式推演都无法建立在可信的连通性之上。

本变更把“真实调用一次、留下可复核证据”做成服务端可重复执行的联通验收，同时把离线回归与真实联调严格分开：没有密钥、没有外网时仍能验证契约与失败路径；有密钥时由开发者显式运行真实联调，任何必需项未通过都不得宣称联通成功。

## What Changes

- 新增模型接入：从服务端配置读取 Chat Completions 兼容 API 根地址、模型名与密钥，用原创微型材料请求 `json_object` 结构化结果，校验必需字段、类型、来源引用与输入偏移；HTTP 200 或“能解析成 JSON”不构成成功。仅当上游明确不支持 `response_format` 时移除该参数重试一次，所有重试与 SDK 内置重试共用 30 秒、最多 2 次实际请求预算。
- 新增证据图谱接入：使用固定验收专用 `graph_uuid`，通过 `graph.episode.add` 写入一条含唯一事实的 episode，保存服务端返回的 episode UUID；所有重试复用同一 `idempotency_key`、原图谱与原请求体。用 `graph.search_episodes` 在截止时间内等待索引并精确核对。分别报告“写入已受理”“仍在索引”“可以检索”，响应丢失时搜索未命中不得作为换键重写依据。
- 新增双平台仿真接入：在 OASIS 的两个本地模拟平台各自建立最小环境。`platform_a` 对应原生 `twitter` 并执行 `create_post`；`platform_b` 对应原生 `reddit`，先建立本次目标帖，再执行 `create_comment`。报告保留原生平台、原生动作、角色、记录标识与目标帖标识；两个平台分别判定、分别归属失败，只有两个平台都有实际行动记录，双平台验收才通过。
- 新增 `make smoke-live`：由开发者显式执行，逐项报告模型结构校验、图谱检索命中和两个平台的动作证据；每项标明通过、失败或因前置条件未执行，任一必需项未通过即以非零状态退出。真实调用与测试替身次数分别统计。
- 新增隔离的引擎运行环境与依赖基线：候选组合为 `camel-oasis==0.2.6`、`camel-ai==0.2.90`、`zep-cloud==4.1.0`，目标为 Linux aarch64、Python 3.11。只有在目标容器中完成依赖解析、安装、导入与最小运行后，才允许把该组合记录为“已验证基线”。
- 扩展离线验收：`make verify` 与 `make verify CHANGE=2026-11-21-connect-engine-providers` 必须在没有真实密钥、没有模型或图谱服务网络连接时完成，保留 Change 01–05 回归与 `/health`、`/livez` 既有语义。CI 同时保留默认验收与独立的 Change 06 离线检查，两份报告使用不同产物名称，CI 不注入真实密钥、不执行 `make smoke-live`、不构建 live 引擎镜像。
- 明确配置与隐私边界：真实调用只使用原创验收材料；模型与 Zep 密钥只通过本机 `.env` 注入需要它们的服务，`.env.example` 只放占位值；日志与报告只记录依赖名称、阶段、调用次数、耗时和脱敏结果；25 页私有样本不进入任何外部调用。

## Capabilities

### New Capabilities

- `structured-model-access`: 模型服务配置、原创材料 `json_object` 结构化请求、字段与类型校验、来源引用校验、移除 `response_format` 重试与失败分类。
- `evidence-graph-access`: 验收专用图谱的写入受理、索引等待、检索命中、唯一事实核对与超时处理。
- `dual-platform-simulation`: OASIS 两个本地模拟平台的独立最小环境、动作执行、引擎记录读回、分别判定与资源关闭。
- `live-engine-smoke`: `make smoke-live` 的逐项编排、真实/替身计数、脱敏证据、退出码、配置缺失处理与依赖运行基线。

### Modified Capabilities

- `reproducible-verification`: 新增 Change 06 的离线专属场景，明确真实联调不进入默认验收、没有凭据时仍可完成离线回归，并保留 Change 01–05 与既有健康检查语义。

## Impact

- 代码：新增引擎适配层、结构化结果校验、图谱状态机、双平台运行器与 `make smoke-live` 入口；不在本变更中接入证据图谱计算、角色准备、正式推演或工作台页面。
- 依赖：新增隔离的引擎依赖清单，固定候选 `camel-oasis==0.2.6`、`camel-ai==0.2.90`、`zep-cloud==4.1.0`；候选 `camel-ai` 的 Pydantic 上限可能低于当前应用环境的 Pydantic 版本，因此不得直接降级应用依赖，是否共用或隔离以实际解析结果为准。
- 编排：新增不暴露 HTTP 端口的隔离引擎运行入口，只包含所需代码、显式配置与 `runtime/smoke-live` 报告目录，不挂载整仓、`.env`、私有材料或历史报告；仅供显式真实联调使用。`make smoke-live` 默认拒绝脏工作树，`ALLOW_DIRTY=1` 仅用于本地调试。`make up` 与默认 `make verify` 不要求真实密钥或外部网络。
- 配置：新增模型地址/模型名/密钥、Zep 密钥与验收图谱标识、引擎超时与重试预算等配置，同步 `.env.example`、`docker-compose.yml`、`app/config.py` 与 README 配置表。
- 验收：新增 `make smoke-live` 报告 `runtime/smoke-live/last-run.json`，不写入 `runtime/verify/` 或 `docs/acceptance/`；默认 `make verify` 与 Change 06 离线场景不发起真实外部调用。
- 非目标：完整证据图谱与图数据库持久化、实体消歧与图谱算法、角色与环境配置、正式双平台推演、报告生成、深度互动、工作台页面、生产级限流/计费/配额管理，以及把真实联调结果自动写入人工验收记录。
