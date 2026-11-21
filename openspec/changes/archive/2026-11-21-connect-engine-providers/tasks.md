# Tasks

## 1. 契约、夹具与范围确认

- [x] 1.1 把已确认的五项决定固化到 `design.md`、对应 specs 与任务：Chat Completions/`json_object`、固定 `graph_uuid` 与幂等键、`twitter`/`reddit` 原生动作、`ALLOW_DIRTY=1` 约定、CI 默认与 Change 06 双检查；验证方式是五项决定在三个产物中无相互矛盾，且不再有“待决定”表述
- [x] 1.2 新增 `app/engines/__init__.py` 与 `app/engines/contracts.py`：定义材料夹具、结构化结果模型、字段/类型/UTF-16 偏移校验、图谱状态枚举、行动记录、报告项与原因码；验证方式是 `tests/test_engine_contracts.py` 覆盖合法结果、缺字段、类型错误、偏移越界、切片不一致与运行标识缺失
- [x] 1.3 在 `contracts.py` 定义统一的原因码与状态映射（`missing_config`、`auth_failed`、`invalid_output`、`source_mismatch`、`marker_missing`、`write_unconfirmed`、`index_timeout`、`stale_or_foreign_hit`、`shared_setup`、`platform_*_setup/action/record/cleanup`、`dependency_unresolved`），验证方式是单元测试断言未知原因码不能进入 `passed`
- [x] 1.4 在测试夹具模块中生成原创微型材料、唯一运行标识、图谱唯一事实与两个平台的动作内容，验证方式是断言材料不含仓库内项目数据、运行标识每次不同、标识只出现在本次夹具中，且不引用 `private-inputs/` 或 25 页样本的文件名与正文
- [x] 1.5 确认本变更不新增产品路由、工作台页面、数据库迁移或后台任务类型；验证方式是 `git diff --stat` 不包含 `web/` 产品页面、`app/migrations/`、`app/routers/` 的新增业务路由，并运行既有路由测试

## 2. 配置、密钥与报告边界

- [x] 2.1 在 `app/config.py` 增加 `model_api_base_url`、`model_api_key`、`model_name`、`model_timeout_seconds`、`zep_api_key`、`zep_api_base_url`、`zep_acceptance_graph_id`、`graph_write_timeout_seconds`、`graph_status_timeout_seconds`、`graph_index_timeout_seconds`、`graph_poll_interval_seconds`、`simulation_timeout_seconds`、`smoke_live_total_timeout_seconds`，全部给出安全默认值；验证方式是 `python -c "from app.config import settings; print(settings.model_timeout_seconds, settings.graph_index_timeout_seconds)"` 可加载
- [x] 2.2 在 `.env.example` 增加同名占位值（如 `https://model.example.invalid/v1`、`replace-with-model-key`、`replace-with-zep-key`、全零 UUID），验证方式是人工核对没有真实凭据，且 `git check-ignore .env` 仍成立
- [x] 2.3 在 `docker-compose.yml` 增加 `engine` 服务与 `profiles: ["live"]`，不发布端口，只通过显式 `environment:` 白名单接收模型/Zep 变量，只挂载 `runtime/smoke-live` 为报告目录，不挂载仓库根目录或 `.env`；验证方式是 `docker compose config` 中 `app` 与 `worker` 不出现 `MODEL_API_KEY`/`ZEP_API_KEY`，`engine` 不出现数据库密码、材料路径、整仓挂载或 `.env`
- [x] 2.4 新增 `app/engines/settings.py`，仅用标准库解析引擎环境变量并分别给出模型、图谱缺配置列表；验证方式是 `tests/test_engine_settings.py` 覆盖只缺模型、只缺 Zep、两者都缺、空字符串视为缺失与安全默认值
- [x] 2.5 增加配置一致性测试：`app/config.py`、`.env.example`、`docker-compose.yml` 与 README 配置表中的引擎变量名和默认值一致；验证方式是 `tests/test_engine_config_consistency.py` 通过
- [x] 2.6 在 `README.md` 配置表补充全部引擎变量、目标平台与“空值表示未配置”的语义；验证方式是逐条复制文档命令执行，输出与表格一致
- [x] 2.7 新增脱敏工具与测试：报告/日志字段白名单拒绝密钥、`Authorization`、完整请求/响应正文、完整 episode 正文与材料正文；验证方式是 `tests/test_engine_redaction.py` 用唯一测试密钥断言输出不含该值

## 3. 引擎依赖隔离与目标容器基线

- [x] 3.1 新增 `app/requirements-engines.txt`，固定 `camel-oasis==0.2.6`、`camel-ai==0.2.90`、`zep-cloud==4.1.0` 及必要的直接依赖；验证方式是文件内所有条目都有精确版本，且不修改 `app/requirements.txt`
- [x] 3.2 新增 `Dockerfile.engine`，基于 `python:3.11-slim-bookworm`，只安装 `app/requirements-engines.txt`，只复制 `app/engines/`、必要的包初始化文件与入口代码，不复制 `.env`、`.git/`、`private-inputs/`、项目材料或历史报告；验证方式是目标 Linux aarch64 上 `docker build -f Dockerfile.engine .` 成功，镜像不暴露端口，镜像内容清单不含敏感路径
- [x] 3.3 在目标 Linux aarch64、Python 3.11 容器中执行依赖解析（如 `pip install --dry-run --report`），保存解析出的完整版本集合、冲突包与是否需要降级 Pydantic；验证方式是解析退出码为 0 或记录明确的冲突约束，报告写入 `runtime/smoke-live/dependency-resolve.json`
- [x] 3.4 在目标容器中执行容器安装（`pip install -r app/requirements-engines.txt`），记录退出码、wheel/源码构建、耗时与镜像大小；验证方式是安装成功或记录失败阶段，失败时不得继续标记基线
- [x] 3.5 在目标容器中执行导入，逐个导入实际模块并打印版本与导入耗时；验证方式是三个候选包及关键传递依赖均可导入，失败时记录模块名与异常类型
- [x] 3.6 在目标容器中执行不访问外部网络的最小运行：构造三个适配器实例、创建一个最小本地仿真环境并关闭、验证图谱/模型适配器可初始化；验证方式是退出码为 0、资源关闭成功、证明未发起真实外部调用
- [x] 3.7 处理 Pydantic 上限冲突：若候选 `camel-ai` 要求低于应用当前 2.14.4 的 Pydantic，必须使用隔离引擎环境；验证方式是 `app/requirements.txt` 不变、`app` 镜像内 Pydantic 版本不变、引擎镜像内解析成功或记录 `dependency_unresolved`
- [x] 3.8 实现并测试基线状态：只有 3.3–3.6 四阶段都成功才允许 `dependencyBaseline.status=verified`；验证方式是 `tests/test_engine_baseline.py` 覆盖全成功、解析失败、安装失败、导入失败、最小运行失败五种状态
- [x] 3.9 确认默认入口不构建引擎镜像：`make up`、默认 `make verify` 与 CI 不启用 `live` profile；验证方式是执行 `make up` 后 `docker compose ps` 没有 `engine` 服务，且 `make verify` 在未安装候选包时仍通过

## 4. 模型结构化接入

- [x] 4.1 在 `app/engines/model.py` 实现 Chat Completions 兼容 HTTP 调用，按 `MODEL_API_BASE_URL`（可含 `/v1`）规范化拼接，保证最终路径只出现一次 `/chat/completions`；使用服务端配置的模型名与密钥，固定 `User-Agent: EchoSim/0.1.0`，设置总超时并禁用会绕过预算的内置重试；验证方式是测试替身与本地 HTTP 服务覆盖含 `/v1` 与不含 `/v1` 的根地址，并断言路径、Bearer、JSON 请求体与正常/降级/重试的 User-Agent
- [x] 4.2 实现首次请求与原创材料提示：请求体包含 `model`、`messages` 与 `response_format={"type":"json_object"}`，消息包含 JSON 契约、`materialId`、唯一运行标识与“只返回 JSON”的约束；验证方式是 `tests/test_engine_model.py` 断言首次请求包含该 `response_format`，且请求体只含本次夹具、不含项目材料或私有样本
- [x] 4.3 复用 `contracts.py` 实现结果校验：必需字段、类型、`materialId` 一致、`facts` 非空、`kind` 枚举、UTF-16 偏移、输入切片等于 `evidenceQuote`、至少一条事实包含运行标识；验证方式是覆盖合法结果、200 但非 JSON、缺字段、类型错误、偏移越界、切片不一致、标识缺失
- [x] 4.4 实现移除参数重试：仅当 400/422 明确表示 `response_format` 不受支持且尚无结果时，移除该参数后最多追加 1 次调用；重试结果执行同一套校验；验证方式是测试替身断言 401/429/5xx/超时/其它 400/422/结果不合格都不触发移除参数重试，移除后成功与仍不合格各自有断言
- [x] 4.5 实现模型预算：实际 HTTP 请求不超过 2 次、总时限不超过 30 秒；408/429/502/503/504/连接超时最多重试 1 次，重试、移除参数重试与 SDK 内置重试共用预算；验证方式是测试替身断言实际请求次数、退避时间、SDK 内部重试被计数，以及 `rate_limited`/`timeout`/`auth_failed` 分类
- [x] 4.6 确保 HTTP 200 但结果不合格时模型项为 `failed`，不得因“能解析 JSON”通过；验证方式是构造 200 + 非法 JSON、200 + 合法 JSON 但字段错误、200 + 来源不匹配三种回归
- [x] 4.7 增加模型报告的脱敏字段：依赖名、阶段、调用次数、耗时、精确 `httpStatus`、`httpStatusClass`、允许列表内的 `providerErrorCode`、`fieldsValid`、`sourceMatches`、`markerMatched`、`fallbackUsed`、`retryUsed` 与原因码；验证方式是报告快照不包含密钥、请求头全集或完整响应，假 Key 回显到 `error.code` 时整份序列化报告不含该值，普通 403 仅无关元数据含 1010 时仍为 `auth_failed`
- [x] 4.8 在 README 补充模型接入的 Chat Completions 路径拼接、`json_object` 首次请求、移除 `response_format` 触发条件、总预算（含 SDK 内置重试）与失败原因表；验证方式是文档示例与实际测试替身请求一致

## 5. 证据图谱接入

- [x] 5.1 在 `app/engines/graph.py` 初始化 `zep-cloud==4.1.0` 客户端，把 `ZEP_ACCEPTANCE_GRAPH_ID` 作为固定 `graph_uuid`，读取可选服务地址；验证方式是单元测试断言未配置时不创建客户端、配置齐备时只使用该固定 `graph_uuid`，不动态创建新图谱
- [x] 5.2 实现 `graph.episode.add` 写入：每次运行生成不同 `runId` 与唯一 `idempotency_key`，episode 包含本次运行标识的唯一事实，保存服务端返回的 episode UUID；验证方式是假客户端断言只写入一条、UUID 被记录、不同运行标识与幂等键不同
- [x] 5.3 实现写入响应丢失处理：超时/连接中断/5xx 后保持原 `idempotency_key`、原 `graph_uuid` 与原请求体；用 `graph.search_episodes` 核对，搜索未命中不得换键或改写请求体；预算内可用同键重试，仍无法确认则 `write_unconfirmed`；验证方式是假客户端覆盖“已受理但响应丢失、首次搜索未命中”“同键重试后命中”“预算耗尽 `write_unconfirmed`”“不产生第二个幂等键”
- [x] 5.4 实现 `graph.search_episodes` 索引等待状态机：`accepted → indexing → searchable`，默认 3 秒间隔、60 秒上限，429/5xx 只继续轮询且不延长截止时间；验证方式是假客户端断言状态序列、轮询次数、截止时间与 `index_timeout`
- [x] 5.5 实现精确命中核对：episode UUID 与 `runId` 同时一致才通过；旧记录与本次记录同时返回时只要本次精确命中也通过；只返回旧记录时继续等待；运行标识出现在错误 UUID 上为 `stale_or_foreign_hit`；验证方式是 `tests/test_engine_graph.py` 覆盖旧记录共存、只返回旧记录、错误 UUID 与精确命中
- [x] 5.6 实现图谱失败分类与有限重试：401/403 不重试，400/422 不重试，写超时/5xx 按 5.3 使用同键重试，检索阶段限流只在截止时间内轮询，SDK 内置重试计入实际调用；验证方式是每种状态都有原因码与调用次数断言
- [x] 5.7 在结束路径关闭图谱客户端并记录关闭结果；验证方式是假客户端断言成功、失败与超时路径都调用关闭，且关闭异常不会掩盖原始失败原因
- [x] 5.8 增加“不得写入用户图谱”的守卫：若配置的图谱标识为空或与产品图谱标识相同则拒绝执行；验证方式是单元测试断言拒绝时真实调用次数为 0、原因码为 `missing_config` 或 `invalid_graph_scope`
- [x] 5.9 在 README 补充固定 `graph_uuid`、`graph.episode.add`、唯一 `idempotency_key`、响应丢失核对、`graph.search_episodes`、旧记录共存、索引等待上限与失败原因；验证方式是文档中的状态名与报告字段一致
- [x] 5.10 增加“服务端已受理、响应丢失、首次搜索未命中”的离线回归入口，断言保持原幂等键与原请求体、不产生第二个逻辑事实、后续精确命中则通过、预算耗尽则 `write_unconfirmed`；验证方式是 `tests/test_engine_graph.py` 与离线检查都覆盖该场景

## 6. 双平台仿真接入

- [x] 6.1 在 `app/engines/simulation.py` 建立平台 A/B 适配器接口，固定稳定别名 `platform_a`/`platform_b`，分别映射原生 `twitter`/`reddit`，并记录 `nativePlatform`、`nativeAction`；验证方式是假引擎断言 `platform_a.nativePlatform=twitter`、`platform_b.nativePlatform=reddit`，别名不会互换
- [x] 6.2 实现每个平台独立的最小环境：独立临时目录、独立本地数据库/进程与一个角色，不共享可变状态；验证方式是 `tests/test_engine_simulation.py` 断言两个环境目录、数据库与运行标识互不相同
- [x] 6.3 实现平台 A 的 `create_post` 与记录读回：居民代表、含运行标识的内容、原生动作 `create_post`、结果字段；验证方式是假引擎只从 `twitter` 记录表返回值，断言动作返回值单独存在时仍不足以通过
- [x] 6.4 实现平台 B 的目标帖与 `create_comment`：先在 `reddit` 本地环境建立含运行标识的目标帖，再由值班志愿者对该帖执行 `create_comment`，读回评论记录与 `targetPostId`；验证方式是假引擎断言评论记录对应本次目标帖、平台 B 的记录不会命中平台 A 的查询
- [x] 6.5 实现动作超时处理：先读回记录，已生效则通过且不重发；明确未生效且预算允许才最多重试 1 次；无法确认则 `action_unconfirmed`；验证方式是三种路径的调用次数与 `recordId` 断言
- [x] 6.6 实现独立判定与失败归属：A/B 各自通过条件、单平台 setup/action/record 失败只影响该平台、B 目标帖创建失败归为 `platform_b_setup`、共享依赖失败归为 `shared_setup`；验证方式是覆盖 A 通过/B 失败、B 通过/A 失败、B 目标帖失败、两个都失败、只有一个环境创建成功
- [x] 6.7 实现 `finally` 清理：停止进程/服务、关闭数据库与 SDK 客户端、删除临时目录，记录 `platform_a_cleanup`/`platform_b_cleanup`；验证方式是模拟清理异常，断言另一个平台仍清理、整体判定失败且行动证据保留
- [x] 6.8 增加“双平台均有实际行动记录才通过”的测试：只有一个平台有记录、两个平台记录相同、记录角色/原生动作/内容不匹配、B 评论指向其它帖子均不得通过；验证方式是 `tests/test_engine_simulation.py` 全部通过
- [x] 6.9 在 README 补充 `platform_a/twitter/create_post`、`platform_b/reddit/目标帖/create_comment`、角色、各自通过条件、`targetPostId`、失败归属与清理方式；验证方式是文档表格与报告字段一致

## 7. `make smoke-live` 编排与报告

- [x] 7.1 实现 `app/engines/smoke.py` 编排：脏工作树检查 → 配置预检 → 模型 → 图谱 → 平台 A → 平台 B → 清理 → 汇总；验证方式是注入假适配器断言执行顺序、前置失败时后续项为 `not_run` 且原因码为 `prerequisite_failed`
- [x] 7.2 实现报告写入：`runtime/smoke-live/last-run.json` 与带 UTC 时间戳的运行记录，包含 `result`、`mode`、`tested_sha`、`dirty`、起止时间、逐项状态、调用计数、依赖基线与脱敏证据；验证方式是 `tests/test_engine_smoke.py` 断言两份报告内容一致、路径正确、JSON 可解析
- [x] 7.3 实现退出码：全部必需项通过为 0；任一必需项 `failed`/`not_run` 为 1；非法调用、默认脏工作树被拒绝或报告无法写出为 2；验证方式是逐项构造失败矩阵并断言退出码
- [x] 7.4 实现真实/替身计数：每个依赖分别记录 `calls.real`/`calls.double`，重试、移除参数重试、SDK 内置重试、写入核对、索引轮询都按实际调用计数；验证方式是模型移除参数重试为 2 次、SDK 内部重试被计数、图谱写入+核对+多次检索分别计数、`mode=live` 时 `calls.double=0`
- [x] 7.5 实现 SHA 与脏工作树约定：报告 `tested_sha` 等于 HEAD；默认在外部调用前拒绝脏工作树，`ALLOW_DIRTY=1` 时允许并记录 `dirty=true`；验证方式是干净、默认脏树拒绝、显式允许脏树三种运行各一次，断言退出码、`dirty` 与签收提示
- [x] 7.6 在 `Makefile` 增加 `smoke-live` 目标与 help 文案，使用 `docker compose --profile live run --rm` 执行引擎命令；验证方式是 `make help` 出现该目标，`make smoke-live` 在缺配置时写出报告并非零退出
- [x] 7.7 验证 Compose 隔离：`engine` 不发布端口、不挂载数据库卷、不挂载仓库根目录或 `.env`、只接收模型/Zep 白名单变量，只挂载 `runtime/smoke-live`；验证方式是 `docker compose --profile live config` 与 `docker compose ps` 检查，并确认容器内看不到 `.env`、`private-inputs/`、项目材料与历史报告
- [x] 7.8 增加缺配置按依赖判定、失效凭据、超时、限流、模型输出不合规、索引超时、单平台失败的编排级测试；验证方式是分别模拟只缺模型、只缺 Zep、两者都缺，断言对应项 `not_run` 而本地 OASIS 仍执行，且每项都断言报告状态、原因码、调用次数与退出码，不把 `not_run` 计为通过
- [x] 7.9 在 README 补充 `make smoke-live` 用法、必需项、逐项状态、退出码、真实/替身计数、报告位置与“离线通过不能替代真实联调签收”；验证方式是逐条执行文档命令并核对输出
- [x] 7.10 检查 `make smoke-live` 日志与报告不含密钥、完整请求/响应、完整 episode、材料正文或私有样本；验证方式是用唯一测试密钥运行并扫描 `runtime/smoke-live/` 与命令输出

## 8. 离线验收与 `CHANGE` 注册

- [x] 8.1 新增 `app/engines/offline.py`：用测试替身驱动 `contracts.py`，覆盖 Chat Completions 路径与 `json_object`、模型合法/非法/移除参数重试/缺配置、固定 `graph_uuid` 受理/索引/命中/超时/响应丢失且首次搜索未命中、`twitter/create_post` 与 `reddit/目标帖/create_comment` 独立判定、单平台失败、清理失败、缺配置按依赖分别判定；验证方式是 `python3 -m app.engines.offline --json` 可直接运行且只依赖标准库
- [x] 8.2 增加 `tests/test_engine_offline.py`，断言离线场景的请求契约、状态转换、原因码、真实调用为 0、替身调用大于 0；验证方式是 `.venv/bin/python -m unittest tests.test_engine_offline` 通过
- [x] 8.3 在 `scripts/verify.py` 注册 `ENGINE_CHANGE_ID = "2026-11-21-connect-engine-providers"`，初始化 `engine_evidence`，在报告中写入 `mode=offline-contract`、逐场景状态、`calls.real=0`、`calls.double` 与缺配置按依赖判定结果；验证方式是 `python3 -m py_compile scripts/verify.py` 通过
- [x] 8.4 在默认 `make verify` 中保持 Change 01–05 全部场景不变，新增内容不得改变 `/health` 200/503/200、`/livez`、容器身份、账户隔离、任务事件、材料冒烟与工作台深链接断言；验证方式是两次默认运行全部通过且报告场景数与既有基线一致
- [x] 8.5 增加 `CHANGE=2026-11-21-connect-engine-providers` 专属场景：运行离线契约检查并把结果写入 `engine_evidence`；验证方式是无真实密钥、无外网时两次运行均退出码 0、`tested_sha` 等于 HEAD、`dirty=false`
- [x] 8.6 增加未知 `CHANGE` 回归：`CHANGE=not-a-change` 仍以退出码 2 结束并写“未注册的变更”；验证方式是运行该命令并检查报告
- [x] 8.7 在 README 验收章节补充 Change 06 离线场景、报告字段、连续两次运行要求与“离线不证明真实联通”；验证方式是文档命令与 `scripts/verify.py` 的实际输出一致
- [x] 8.8 确认 `make verify` 与 Change 06 离线场景不读取 `.env` 中的真实密钥、不安装候选引擎依赖、不发起模型/图谱/OASIS 调用；验证方式是在无密钥环境运行并检查报告 `calls.real=0`
- [x] 8.9 在 `.github/workflows/verify.yml` 保留现有默认 verify job，增加独立 Change 06 离线 job，调用 `make verify CHANGE=2026-11-21-connect-engine-providers`；验证方式是本地用 `act` 等价检查或审阅 YAML，确认两个 job 都调用同一仓库入口且不重复实现验收逻辑
- [x] 8.10 为两份 CI 报告使用不同 artifact 名称，分别断言 `tested_sha`、`dirty=false`、`result=passed`，Change 06 专属检查额外断言 `calls.real=0`；验证方式是构造通过/失败报告各一次，断言缺失 artifact 或任一断言不满足时 job 失败
- [x] 8.11 确认 CI 不注入模型或 Zep 密钥、不执行 `make smoke-live`、不构建 live 引擎镜像；验证方式是审阅 workflow 的 env 与命令，确认没有真实密钥、没有 `smoke-live`、没有 `Dockerfile.engine` 或 live profile

## 9. 工程规则与文档

- [x] 9.1 更新 `AGENTS.md`：新增“引擎接入与真实联调”规则，覆盖配置与密钥边界、引擎只获得所需代码/显式配置/报告目录、原创材料、25 页私有样本禁止外部调用、真实/离线分离、调用计数（含 SDK 内置重试）、依赖基线、固定 `graph_uuid` 与幂等键、双平台原生映射与清理、`ALLOW_DIRTY=1`、CI 双检查与报告脱敏
- [x] 9.2 更新 `README.md`：新增引擎配置表、模型/图谱/双平台接入说明、`make smoke-live` 验收、离线 `CHANGE` 用法、报告路径、人工签收边界与排障；验证方式是逐条执行文档命令
- [x] 9.3 更新 `Makefile` help 与 README 命令表，确保 `make smoke-live`、`make verify CHANGE=…` 的说明一致；验证方式是 `make help` 与文档文本对比
- [x] 9.4 审阅新增代码注释、测试数据、文档、提交说明与 PR 文案，确认只使用 EchoSim 域术语、原创示例与正式包名，不出现外部参考项目名称、链接、源码路径、代码片段、截图、示例数据或出处说明；验证方式是人工全文检索与评审
- [x] 9.5 确认 `docs/acceptance/` 在本变更实施与自动化运行中未被创建、修改或删除；验证方式是 `git status --porcelain docs/acceptance` 为空

## 10. 集成验证与真实联调签收准备

- [x] 10.1 运行 `.venv/bin/python -m unittest discover -s tests`、`cd web && npm test && npm run typecheck && npm run build`，确认既有回归与新增引擎单元测试全部通过
- [x] 10.2 在干净提交上带 `EXPECTED_SHA=$(git rev-parse HEAD)` 连续两次执行默认 `make verify`，确认退出码 0、`tested_sha` 一致、`dirty=false`、Change 01–05 场景完整
- [x] 10.3 在同一 SHA 上连续两次执行 `make verify CHANGE=2026-11-21-connect-engine-providers`，确认退出码 0、`engine_evidence` 一致、真实调用为 0、离线场景全部通过
- [x] 10.4 在无真实密钥环境执行 `make smoke-live`，分别验证只缺模型、只缺 Zep、两者都缺：对应项为 `not_run`/`missing_config`，本地 OASIS 动作仍执行，真实外部调用为 0，总结果非零；再以临时无效凭据覆盖运行，确认 `auth_failed` 且不重试
- [x] 10.5 恢复临时覆盖的有效凭据并填写 `MODEL_API_BASE_URL`、`MODEL_NAME`、`MODEL_API_KEY`、`ZEP_API_KEY`、`ZEP_ACCEPTANCE_GRAPH_ID`（graph_uuid）后重新执行 `make smoke-live`，确认模型结构校验、图谱检索命中和平台 A/B 行动记录全部通过、退出码 0、`mode=live`、`dirty=false`
- [x] 10.6 连续两次执行真实 `make smoke-live`，确认两次运行使用不同运行标识、图谱写入不产生重复事实、两次报告都可通过精确标识核对，并记录两份报告路径
- [x] 10.7 在目标 Linux aarch64、Python 3.11 容器中确认依赖解析、安装、导入、最小运行四阶段证据齐全；若任一阶段失败，记录 `dependencyBaseline.status=unverified`、失败阶段与阻塞项，并停止真实联调，不得宣称基线可用
- [x] 10.8 确认 25 页私有样本没有进入模型请求、图谱 episode、平台动作、日志、报告或 `runtime/`；验证方式是检查请求/报告字段、扫描 `runtime/smoke-live/` 与 `git status`
- [x] 10.9 准备待签收清单（不写入 `docs/acceptance/`）：被测 SHA、`make verify` 与 `make verify CHANGE=…` 报告路径、`make smoke-live` live 报告路径、模型字段/来源证据、图谱 episode UUID 与幂等键及检索等待、平台 A/B 原生平台/原生动作/记录标识/目标帖标识、依赖基线四阶段结果、已知限制
- [x] 10.10 列出需要开发者在本机填写的变量名与占位说明：`MODEL_API_BASE_URL`（API 根地址，可含 `/v1`）、`MODEL_NAME`、`MODEL_API_KEY`、`ZEP_API_KEY`、`ZEP_ACCEPTANCE_GRAPH_ID`（graph_uuid）；不要求或打印真实值，验证方式是文档/交付说明只列变量名与用途
- [x] 10.11 确认脏工作树行为：默认 `make smoke-live` 在外部调用前拒绝并退出，`ALLOW_DIRTY=1` 时允许但报告 `dirty=true`，签收只接受 `dirty=false` 且 `tested_sha` 与候选一致的 live 报告；验证方式是三种运行各一次并检查报告
- [x] 10.12 由验收人在真实凭据与浏览器/终端环境中核对联通结果后，才在 `docs/acceptance/` 写入签收记录；记录必须引用同 SHA、`dirty=false` 的 `mode=live` 报告并注明离线报告不能替代真实联调；`make verify`、CI 与 `make smoke-live` 均不得自动创建或修改该记录

## 本次联调证据

- 被测代码 `85497b6acf798d3a4a2b0bfea1563db9623c547c`，工作树干净。
- 10.4 的回环隔离报告在 `runtime/smoke-live/missing-auth-85497b6-20261121T164636Z/`，真实服务的失效凭据报告为 `runtime/smoke-live/run-20261121T164755Z.json`。
- 10.5 和 10.6 的两份原始报告为 `runtime/smoke-live/run-20261121T164808Z.json`、`runtime/smoke-live/run-20261121T164820Z.json`。每次独立回查均只有一个匹配的 episode，对应同名 `-exact.json`。
