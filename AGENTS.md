# AGENTS.md

本文件是 EchoSim 仓库的单一工程规则来源，面向所有贡献者与自动化代理。README 面向快速上手，本文件面向“必须始终遵守什么”。

## 语言与文档

- 代码注释、提交信息正文、文档与验收记录使用中文（zh-CN）；标识符与命令保持英文。
- OpenSpec 结构标题与 `SHALL`/`MUST` 关键字保持英文。
- 面向使用者的行为变化必须同步更新 `README.md`；工程规则变化必须同步更新本文件。

## 技术栈与版本

- 前端：Vue 3 + TypeScript + Vite，构建期 Node.js 22（见 `.nvmrc`、`web/package.json` 的 `engines`）。
- 后端：Python 3.11 + FastAPI + Uvicorn，SQLAlchemy 2.x async + asyncpg。
- 数据库：PostgreSQL 16（`postgres:16-alpine`）。
- 编排：Docker Engine + Docker Compose v2；命令入口统一走 `make`。
- 依赖：前端提交 `web/package-lock.json` 并用 `npm ci`；后端 `app/requirements*.txt` 使用精确版本。

## 目录约定

| 路径 | 用途 |
| --- | --- |
| `app/` | FastAPI 后端：配置、数据库、健康检查、鉴权、迁移、静态托管 |
| `app/migrations/` | 前向 SQL 迁移，版本记录在 `schema_migrations` |
| `app/services/` | 账户、会话、项目、历史与任务服务；项目归属校验与租约执行的唯一入口 |
| `app/engines/` | 引擎契约、模型/图谱/双平台适配器、离线契约检查与依赖基线 |
| `app/routers/` | HTTP 路由，只做参数校验、依赖注入与异常映射 |
| `app/worker.py` | 独立 worker 进程入口（无 HTTP 端口，只从数据库领取任务） |
| `app/lease_probe.py` | 验收专用租约/终态竞态探针，仅由 `make verify CHANGE=…` 调用 |
| `web/` | Vue 前端源码与构建配置；`web/dist` 为构建产物（不提交） |
| `tests/` | Python 单元测试（`unittest`） |
| `scripts/verify.py` | 可重复验收脚本，仅用 Python 标准库 |
| `scripts/smoke_live.py` | 真实联调的宿主预检、脏工作树拒绝与报告路径 |
| `Dockerfile.engine` | 隔离引擎镜像；只复制引擎代码与依赖，不暴露端口 |
| `app/requirements-engines.txt` | 引擎候选依赖，只安装到隔离引擎镜像 |
| `docs/acceptance/` | 人工验收记录与模板（自动化流程禁止写入） |
| `.github/workflows/` | CI 工作流 |
| `runtime/` | 本地运行产物（报告、日志），已忽略不提交 |
| `openspec/` | OpenSpec 变更与规范 |

## 命令入口

```bash
make up       # 缺 .env 时从 .env.example 生成，然后构建并启动，等待健康
make down     # 停止并移除容器与网络，保留数据卷
make reset    # 破坏性：连同数据卷一起移除
make verify   # 执行可重复验收，写入 runtime/verify/last-run.json
make smoke-live  # 由开发者显式执行真实模型/图谱/双平台联通验收
make migrate  # 在运行中的 app 容器内应用未执行的迁移
make user EMAIL=a@example.com  # 从标准输入读取密码，创建/更新账户
```

- 不要绕开 `make up`/`make down` 手工拼装 `docker compose` 命令作为交付路径。
- 单元测试：`.venv/bin/python -m unittest discover -s tests`（Python 3.11 虚拟环境）。
- 前端回归：`cd web && npm test`（阶段链接查询参数）与 `npm run typecheck`、`npm run build`。

## 同域、回环与健康语义

- 首页与 `/health` 必须同源（同一协议、主机、端口），前端请求一律使用相对路径，禁止硬编码 `localhost`、后端端口或绝对 URL。
- 服务只发布到回环地址 `127.0.0.1:${ECHOSIM_PORT:-8080}`，不得默认暴露到局域网或公网。
- `GET /livez` 是存活信号：不访问数据库，进程可响应即 200，容器健康检查使用它。
- `GET /health` 是就绪信号：每次请求都**真实执行 `SELECT 1`**（不得缓存、不得固定返回），数据库可用返回 200 `{"status":"ok","database":"ok"}`，不可用或超时在 5 秒内返回 503 `{"status":"degraded","database":"unavailable"}`。
- 数据库故障不得导致后端进程重启；恢复后同一进程的下一次 `/health` 必须重新返回 200。
- 健康检查不得回显 DSN、凭据、堆栈或内部主机名；日志只记录异常类型。
- 每次 `/health` 请求必须确定请求 ID：合法 `X-Request-ID`（1–64 位字母、数字、`.`、`_`、`:`、`-`）复用，缺失或非法时生成随机值；响应头回显该 ID，探测失败日志记录 `request_id`，响应体契约保持不变。
- 探测路径只读：除 `SELECT 1` 外不得执行任何写操作或修改状态。

## 后台任务与事件流

- PostgreSQL 是任务队列、进度与事件的唯一事实来源；`app` 不得在请求内执行长任务，长任务只能由独立 `worker` 进程执行。
- 任务状态只有 `queued`、`running`、`succeeded`、`failed`、`canceled` 五种；`succeeded`/`failed`/`canceled` 为终态，只能以先提交事务的结果为准。
- 只有一份“做到第几步”的权威来源：`run_steps` 与 `jobs.next_step`，二者必须与该步事件在同一事务提交。
- 每次领取必须生成新的 `lease_token`；所有进度、续租、取消与终态写入必须同时限定 `jobId` 与该令牌。租约有效性必须在**锁定任务行之后**用独立的 `clock_timestamp()` 语句判断；不得使用 `now()`（事务开始时间），否则锁等待跨过到期点会让过期写入通过。旧令牌的续租与写入必须被拒绝。
- 不依赖“归还扫描”进程：只有一个 worker 且它停止时，持久状态可以保持 `running`，由查询按 `lease_expires_at <= now()` 返回 `awaitingTakeover`；领取语句必须能原子接手“`queued` 或租约已过期的 `running`”。
- 同一项目同一操作的活动任务最多一个；两个并发开始必须得到一个 202 与一个 200，且只新增一对 job/run（冲突路径整体回滚，不得留下孤立 run）。
- 事件必须先与业务变更同事务落库再推送；`seq` 在同一 run 内从 1 连续递增，`(run_id, seq)` 唯一，终态事件计入序号，SSE 心跳与 `stream-end` 不计入。
- SSE `id` 固定为 `<runId>:<seq>`；游标是“已完整处理的最后一个连续 seq”，合法范围 `0..当前最大 seq`，`Last-Event-ID` 优先于 `after`，非法游标返回 400 JSON。
- SSE 必须在发送任何事件字节前完成鉴权、项目归属、任务归属与游标校验；失败返回 JSON，不得建立事件流。
- 任意事件流实现都不得在 `app` 常驻进程中引入第二条数据库事实来源；通知/轮询只作为唤醒手段。
- worker 复用同一镜像但无 HTTP 端口：Compose 必须覆盖（禁用）镜像继承的 `/livez` 探针，改用 worker 自己的存活探针；不得因此改变 `app` 的探针。
- worker 停止、崩溃或重启不得改变 `/health`、`/livez` 语义与 `app` 容器身份；任务可观察性通过 `awaitingTakeover`、`attempts` 与 worker 日志表达。
- 任务与事件流接口必须复用 `app/services/projects.py` 的 `resolve_owned_project`；他人项目、不存在项目与非法 ID 对外表现一致。
- 测试任务必须明确标注为连通性自检，不得写入阶段业务数据、不得让阶段显示完成；材料入库、真实推演与案例回放属于后续变更。

## 材料入库与来源定位

- 材料是项目私有数据：原件保存在 Compose 卷，存储路径只能由项目与材料标识派生，数据库只记录 SHA-256 与相对路径；不得用用户文件名拼接路径。
- 材料正文、原件内容与真实存储路径不得写入日志、验收报告或错误响应；失败日志只记录 `documentId`、原因码与异常类型。
- 单文件上限固定为 20 MiB（20971520 字节），恰好等于上限必须通过；空文件、超限与不支持的类型在上传请求内同步拒绝，不得留下材料记录或残片。
- 解析必须由独立 `worker` 执行，`runs.kind = document.ingest`、`jobs.operation_key = document-ingest:v1:<documentId>`，与连通性测试任务 `test.simulation` 区分；任务列表接口不得把解析任务混入“测试任务”面板。
- PDF 必须逐页读取，物理页码从 1 开始且等于页对象顺序；空白页计入总页数并允许零个分块；不得先拼成全文再推算页码。页眉、页脚与打印路径只从分块文本移除，分块仍绑定原页。
- Markdown/TXT 优先 UTF-8（含 BOM），其次带 BOM 的 UTF-16 与可可靠识别的 GB18030；无法可靠解码时必须失败并说明原因，不得用替换字符勉强生成分块。
- 原始段落位置必须在换行归一与空白清理之前记录；`sourceStart`/`sourceEnd` 以 UTF-16 代码单元计数、区间左闭右开，使浏览器能直接切片。文本清洗不得删除正文中的路径与 URL。
- 每个分块必须独立携带物理页码或段落区间与原文偏移；允许少量重叠，但重叠不得合并相邻块的定位区间。材料正文中的命令与提示一律按普通文本处理，不得执行或据此改变解析行为。
- 同一项目相同内容只能保留一套有效材料与分块，去重由数据库部分唯一索引裁决；失败材料不阻止重新上传。
- 解析失败必须保存解析时生效的结构化详情（如 PDF 实际页数与页数上限）；配置变化后不得改写历史失败原因，页面也不得用当前配置覆盖历史值。修复前无可信上限的旧记录只显示不含上限数字的原因。
- 分块与 `ready` 必须在同一事务发布，失败材料不得留下任何分块；worker 中断接手只能重算，不得产生重复分块。长步骤必须在工作线程中执行并持续续租，不得阻塞事件循环。
- 取消解析必须在取消事务内同时写 job `canceled`、一条 `canceled` 终态事件与材料 `failed`（原因 `canceled`）；完成先提交时材料必须保持 `ready`。
- 材料上传、列表、详情、分块、原文视图与原件接口必须复用 `resolve_owned_project`；匿名立即 401 且不访问数据库，他人项目与他人材料对外不可区分（`材料不存在`）。
- 材料深链接是完整 URL（含 `document`、`layout`、`lang`）；匿名打开必须先登录并回到原地址，不得丢弃查询参数。
- 25 页私有样本只用于项目作者在浏览器中的人工验收，必须放在仓库外或已忽略的 `private-inputs/`，不得提交、复制到 `runtime/` 或进入 CI 产物；自动化与测试数据不得引用其文件名、路径或正文。

## 证据图谱构建与来源定位

- 建图任务由独立 `worker` 领取执行，`app` 只负责开始、读取已发布快照与事件流；不得在 HTTP 请求内调用模型或 Zep。
- 一次 build 固定六步：`bind`、`plan`、`extract`、`write`、`readback`、`publish`。所有步骤必须复用锁定任务行、数据库实际时钟、当前租约令牌、步骤唯一约束与事件事务；`run_steps` 与 `jobs.next_step` 是步骤权威。
- 真实 provider 的新 build MUST 使用本次 build 的独立图谱，不得复用旧 build 的 episode 或图谱对象；旧 build 的 `graph_id`、快照与来源保持不变。图谱创建与本体设置必须使用 UUIDv4 格式的幂等键；响应丢失时用同一键确认，不得换键重写。
- 第 2 步只由模型根据整份报告与问题规划实体/关系类型；实体类型 `name` 使用英文 PascalCase，关系类型 `name` 使用英文 UPPER_SNAKE_CASE，中文显示名放 `displayName`。不得登记“首轮最多 24 个主体”的白名单，也不得要求后续抽取围着固定名单。
- 第 3 步必须从现有入库分块重建建图全文，保留段落、标题与跨页衔接，去掉打印页眉页脚；按 500 字、50 字重叠切分，并为每个建图分块保存物理页码、UTF-16 偏移、来源分块序号与段落区间。该步不得改写 `document_chunks`，也不得要求用户重新上传。建图分块数量超过规模上限时明确失败。
- 第 4 步必须把建图分块的原文作为 episode 写入，不得写入模型预抽取的 JSON 清单；`write_outcome=in_flight` 的 episode 只能检索确认，不得重新 `add`。写入受理、episode 匹配、`processed` 与节点/边读回是四个独立事实；索引预算与处理预算分别计时，迟到响应不得推进 episode 状态。
- 第 5 步必须按已处理 episode 批次增量读回：某一批稳定且来源可核对后立即发布 `complete=false` 的部分快照，其余批次继续处理；不得等待全部 episode 完成后才发布第一份可视图，也不得由前端定时器或人为拆分制造进度。第 6 步必须在同一事务发布最终快照、`phase=ready`、`can_continue=true`、完成事件与 `succeeded`。候选快照在发布前不得出现在事件或读取接口。
- 第 5 步必须按本次 build 的 episode UUID 分页读回全部节点、边与事实描述，不得在读回阶段按固定数量截断，也不得要求命中模型抽取清单才能发布。节点按 NFKC 规范化名称归并，边按起点、终点、关系类型与规范化事实去重；自环、旧 episode 与没有本次 episode 归属的对象不得进入快照。
- 快照必须完整物化；节点、边、来源与计数挂同一 `snapshot_id`，业务读取只返回 `published=true` 的版本。最终快照与最近部分快照保留，更旧的部分快照可清理。
- 来源映射必须遵循建图分块的逐字符映射：文本使用解码后完整原文的 UTF-16 偏移，PDF 使用物理页原始抽取文本与从 1 开始的页码；清洗文本只用于查找引文。跨页或跨入库分块的句子不得强行合并来源区间；既有入库材料不得要求重新上传。
- 每条读回关系必须先关联到贡献它的 episode，再在该 episode 的建图分块中定位支持句；支持句必须同时出现起点主体、动作与对象，允许相邻句、明确指代和有依据的简称。只有媒体名单、机构名单或实体名称而没有动作的句子不得作为关系来源；无法定位支持句的关系不得发布。
- 同一主体跨页/跨 episode 必须按规范化名称归并为一个节点并合并参与关系；不得因重复出现拆节点、丢关系或让整图失败。读回重复边必须去重，关系来源按关系分别保留代表性原句。
- 每条材料关系必须标注来源性质（媒体报道、当事方主张、机构正式决定、其他材料陈述）；非媒体来源的指控/回应/维权等必须归为当事方主张，原文“某方称”不得显示为已证实事实。
- 每条关系来源必须同时支持起点主体、动作与对象；只有媒体名称、机构名单或实体名称而无动作的引文不得作为关系来源。建议、撤销、纠正或呼吁不得挂到表示已发生动作的正式决定边下；没有合格来源的关系不得发布。
- 规模上限固定为单次 5 份材料、500 个节点、800 条边、每项 5 条来源；超限明确失败，不得截断发布。
- 读取接口默认选择活动 build，其次最近 build；`?build=<id>` 打开旧 build。前端必须用 `buildId` 守卫丢弃迟到响应，第二次建图不得显示第一次完成图。
- `canContinue` 只由服务端根据最终已发布快照判定；前端不得自行推断可继续。
- 默认 `make up`、默认 `make verify` 与 CI 不得触发业务建图真实外呼；离线走查必须显式启用 `EVIDENCE_GRAPH_ALLOW_STUB=true` 与 stub provider。私有 25 页报告与原创演示材料只留在仓库外，不得进入夹具、日志或报告。

## 引擎接入与真实联调

- `.env.example` 的 `replace-with-*`、`*.example.invalid` 与全零 UUID 必须按“未配置”处理；`make smoke-live` 不得自动复制 `.env`，避免占位值触发外部调用。
- 真实模型、图谱与 OASIS 调用只能由开发者显式执行 `make smoke-live` 触发；`make up`、默认 `make verify` 与 CI 不得执行真实联调、不得注入模型/Zep 密钥、不得构建 live 引擎镜像。
- `make smoke-live` 沿用 `ALLOW_DIRTY=1` 约定：默认在发起外部调用前拒绝脏工作树；显式允许时报告 `dirty=true`，但签收只接受 `dirty=false`、`tested_sha` 与候选一致的 live 报告。
- 真实调用只使用运行时生成的原创微型夹具；MUST NOT 读取项目材料、`private-inputs/` 或 25 页私有样本，MUST NOT 把其文件名、路径或正文写入请求、日志或报告。
- 模型使用 Chat Completions 兼容接口；`MODEL_API_BASE_URL` 是根地址（可含 `/v1`），拼接后只出现一次 `/chat/completions`。首次请求使用 `model`、`messages`、`response_format={"type":"json_object"}` 与 `User-Agent: EchoSim/0.1.0`；正常调用、移除参数重试与网络重试 MUST 使用同一 User-Agent，不得回退到 `Python-urllib/*`。字段、类型、UTF-16 偏移、原文引用与运行标识由 EchoSim 校验。
- 只有上游 400/422 明确指出不支持 `response_format` 时才移除该参数重试一次；所有重试、移除参数重试与 SDK 内置重试共用 30 秒、最多 2 次实际 HTTP 请求预算。HTTP 200 或“能解析 JSON”不构成成功。
- 模型报告 MUST 保留精确 `httpStatus` 与兼容字段 `httpStatusClass`；402 归类为 `billing_required`，403 且明确 `error.code`/`error_code` 为 1010 时归类为 `edge_blocked`，401 与普通 403 保留 `auth_failed`。上述错误均只调用一次。`providerErrorCode` 只记录允许列表内的 `1010` 与 `insufficient_quota`；未知值或被回显的任意字符串留空。普通 403 只在无关元数据中出现 1010 时仍为 `auth_failed`。报告不得保存密钥、请求头全集或原始响应正文。
- 图谱使用预先配置的固定验收专用图谱，`ZEP_ACCEPTANCE_GRAPH_ID` MUST 是 `graph_uuid`。每次运行生成不同 `runId` 与唯一 `idempotency_key`；同一次逻辑写入的所有重试 MUST 复用原键、原图谱与原请求体，不得因超时、连接中断或搜索未命中而换键或改写请求体。
- 图谱写入使用 `graph.episode.add`，保存服务端返回的 episode UUID；检索使用 `graph.search_episodes`，同时核对 UUID、`runId` 与唯一事实。固定图谱允许旧记录共存，本次记录精确命中即可通过；只返回旧记录不得冒充本次结果。
- 写入响应丢失时，搜索未命中 MUST NOT 作为换键重写的依据；预算内仍无法确认 MUST 判定 `write_unconfirmed`。离线契约必须覆盖“服务端已受理、响应丢失、首次搜索未命中”。
- OASIS 双平台使用稳定别名：`platform_a` 对应原生 `twitter` 并执行 `create_post`；`platform_b` 对应原生 `reddit`，先建立本次目标帖，再执行 `create_comment`。报告 MUST 保留原生平台、原生动作、角色、记录标识与目标帖标识。
- 两个平台 MUST 使用独立临时目录、数据库/进程与运行标识；动作结果 MUST 从各自引擎记录读回，不能只用动作调用返回值。两个平台分别判定，只有两个平台都有实际行动记录，双平台项才通过；清理失败 MUST 记录并导致运行失败。
- 缺配置 MUST 按依赖分别判定：缺模型配置只阻断模型项，缺 Zep 配置只阻断图谱项；本地 OASIS 动作不依赖这些外部密钥，仍应继续执行。总结果因必需项缺失而非零。
- 引擎镜像 MUST 只包含 `app/engines/`、必要包初始化文件与引擎依赖；MUST NOT 复制或挂载整仓、`.env`、`.git/`、`private-inputs/`、项目材料或历史报告。运行时只把 `runtime/smoke-live` 挂载为可写报告目录，只注入模型/Zep 白名单变量与引擎超时配置。
- 引擎依赖 MUST 使用独立 `app/requirements-engines.txt` 与 `Dockerfile.engine`；候选 `camel-ai` 的 Pydantic 上限不得通过降级 `app`/`worker` 依赖来迁就。只有目标 Linux aarch64、Python 3.11 容器完成“解析 → 安装 → 导入 → 最小运行”四阶段，才允许把组合记录为 `verified`。
- 日志与报告只记录依赖名称、阶段、调用次数、耗时、HTTP 状态类别、原因码、运行标识、episode UUID、幂等键、平台键、原生平台、原生动作、目标帖标识与脱敏结果；MUST NOT 记录密钥、`Authorization`、完整请求/响应正文、完整 episode 正文、材料正文或私有样本。
- `make verify CHANGE=2026-11-21-connect-engine-providers` MUST 在无真实密钥、无外部网络、不安装候选引擎依赖时完成，并把离线契约结果写入报告 `engine_evidence`（`mode=offline-contract`、`calls.real=0`）。
- CI MUST 保留默认 `make verify`，并增加独立的 Change 06 离线检查；两份报告使用不同 artifact 名称，分别断言 `tested_sha`、`dirty=false`、`result=passed`，专属检查额外断言 `calls.real=0`。
- 真实联通结论只来自同 SHA、`mode=live`、`dirty=false` 且必需项全部 `passed` 的 `make smoke-live` 报告，并仍需人工签收；离线通过不得替代真实联调或人工记录。

## 控制台与工作台地址

- `/` 是公开中文控制台；`/projects` 是私有“我的项目”列表与登录默认落点；`/projects/:id` 是私有项目详情；`/projects/:id/workbench/:stage` 是私有工作台阶段。不得把 `/` 重新用作项目列表或强制登录入口。
- 五个阶段 slug 固定为 `evidence-graph`、`roles-environment`、`dual-platform-run`、`report`、`interaction`；控制台、工作台导航与空态链接必须从同一阶段注册表派生，不得各自硬编码顺序。
- 私有工作台访问顺序固定为“身份 → 项目归属 → 阶段合法性”：匿名先跳登录并保留完整 `next`；他人项目、不存在项目与非法项目 ID 统一“项目不存在”；只有所有者才看到“阶段不存在”。
- 阶段导航、缺少前置条件时的上一阶段链接、无阶段重定向与非法阶段页的有效阶段入口必须显式携带当前 `layout` 与 `lang`；布局/语言切换使用 `replace` 保留阶段，阶段导航与返回控制台使用 `push`。
- 控制台系统状态只依据 `/health`；账户状态只依据会话查询；项目列表失败不得改写健康状态。只有会话明确 401 才按访客处理，会话 503 必须显示“暂时无法确认”、保留 Cookie 并提供重试。
- 工作台外壳就绪与阶段业务结果分开：项目 200 后项目名、阶段高亮、工具条与布局可操作，阶段内容可为“还没有材料/缺少前置条件”；产品页面只展示真实 API 数据，测试夹具不得进入产品。
- `make verify` 中的 `EchoSim` HTML 200 只证明 SPA 入口可达；布局、语言、截图与浏览器后退必须由人工走查记录，不能写成自动化通过结论。
- 人工验收记录只在浏览器签收后写入 `docs/acceptance/`；签收前只准备截图与待签收清单，不归档、不合并。

## 身份、会话与项目隔离

- 账户只能通过 `python -m app.manage create-user`（或 `make user`）创建，不提供自助注册接口；密码只从参数或 stdin 读取，禁止写入日志、报告或提交到仓库。
- 登录成功后服务端在 `sessions` 表创建会话，数据库只存令牌的 SHA-256；Cookie 名为 `echosim_session`，必须带 `HttpOnly`、`SameSite=Lax`、`Path=/`，且**不得设置 `Max-Age` 或 `Expires`**（浏览器会话 Cookie）。
- 服务端会话绝对有效期由 `SESSION_TTL_SECONDS` 控制，默认 7 天；刷新页面与重启后端不得丢失登录态，关闭并重开浏览器必须要求重新登录。
- 私有接口先鉴权、后归属判断：请求未携带会话 Cookie 时立即返回 401 且不得访问数据库，数据库停机时同样返回 401。
- 携带 Cookie 但会话查询因数据库不可达失败时返回 503，且响应不得清除 Cookie；数据库恢复后同一未过期会话必须继续可用。
- 退出登录必须删除服务端会话并清除 Cookie，使旧 Cookie 再次使用返回 401；退出接口对无效会话保持 204 幂等。
- 项目归属校验只能通过 `app/services/projects.py` 的 `resolve_owned_project` 完成；他人项目、不存在的项目和非法 ID 必须产生相同的对外 404 `{"detail":"项目不存在"}`，不得返回“无权访问”。
- 列表与历史必须在查询层按 `owner_id` 过滤，不得取回他人数据后再筛选；后续材料、运行与报告能力必须复用同一归属入口。
- 使用 Cookie 鉴权的非 GET 写请求若携带 `Origin`，必须与请求 `Host` 同源，否则在业务逻辑前返回 403。
- 数据库迁移只前向执行，新增迁移必须写入 `app/migrations/NNNN_*.sql` 并可在已有数据卷上重复安全启动；不得用 `create_all()` 替代迁移。
- 新增配置项必须同时更新 `.env.example`、`docker-compose.yml`、README 配置表，并在 `app/config.py` 给出安全默认值。

## 凭据与配置

- 禁止提交任何真实凭据、密钥或令牌；`.env`、`.env.*`（`.env.example` 除外）已被忽略。
- `.env.example` 只能包含明显的开发占位值，并注明用途。
- 新增配置项必须同时更新 `.env.example`、`docker-compose.yml` 默认值与 README 配置表。

## 验收与 SHA 规则

- 验收结论必须绑定被测 Git SHA；`runtime/verify/last-run.json` 的 `tested_sha` 必须等于执行时的 `HEAD`。
- `make verify` 必须保留 Change 01 的故障恢复与进程身份场景，并包含 A/B 双账号隔离路径：登录、创建、列表、详情、停库鉴权边界、退出失效、他人不可见、重新登录仍可见；准备账号与命名项目必须幂等，连续两次执行都要通过。
- `make verify` 还必须验证 `/`、`/projects` 与五条工作台阶段深链接返回 `EchoSim` HTML，并断言 `/api/*` 未被 SPA 回退吞掉；阶段链接的 `layout`/`lang` 保留由 `cd web && npm test` 覆盖。
- `make verify` 必须包含任务底座场景：开始 202、状态查询、确定性任务成功且事件 1–10、匿名 401、他人 404、停库 503 保留 Cookie、只停 worker 时 `/health`/`/livez` 不变。
- 默认 `make verify` 必须包含轻量材料回归：自制小 TXT 上传后由 worker 解析到 `ready`、列表可见且至少一个分块、匿名读取列表返回 401；同一环境连续两次执行都必须通过。
- `make verify CHANGE=2026-11-21-ingest-seed-documents` 必须在默认场景之上追加：三页 PDF 的总页数、空白页零分块、第 3 页 `pageNo` 与 `#page=3` 来源、原件哈希与页对象结构；PDF 页眉页脚与打印路径清理；文本换行/空白归一、合法路径保留、emoji 偏移与 GB18030 原件/原文；20 MiB 边界、空文件、无文字层与页数上限；重复上传只留一套；匿名 401 与他人 404；worker 停止时取消排队任务；慢解析续租与中断接手。
- `make verify CHANGE=2026-11-21-track-background-jobs` 必须在同一 SHA 上追加：真实 SSE 两段序号（1–4 与 5–10）、`Last-Event-ID` 优先、终态重连立即结束、并发开始只新增一对 job/run、worker 中断接手、真实 PostgreSQL 锁等待跨过期点与旧令牌拒绝回归、取消（含 `canceled seq=4`）、受控失败与取消/完成竞态。未注册的 `CHANGE` 必须以退出码 2 结束。
- `make verify CHANGE=2026-11-22-build-evidence-graph` 必须在同一 SHA 上追加：原创材料开始/重复/未就绪/六份/空问题/匿名/他人边界；真实 worker 的阶段序列、增量部分快照与最终快照、PDF 第 3 页与文本 UTF-16 来源；绑定响应丢失、未提交、接手；失败原因码与超限不发布；发布/取消竞态；第二次建图隔离；日志/报告脱敏与 worker provider 恢复。未注册的 `CHANGE` 必须以退出码 2 结束。
- `make verify` 必须使用独立的 Compose 项目、回环端口与数据卷，不得停止或改写本地 8080 工作台；Change 07 默认使用离线 stub provider，不读取真实模型/Zep 凭据。`graph_evidence.calls.real` 必须为 0、`calls.double` 必须大于 0；计数必须来自 provider 调用边界的逐 build 持久化值，并在 `callScope` 中列出覆盖的 buildId，不得用计划数、节点数或快照大小推算。
- 报告必须在 `job_evidence` 中记录 `jobId`、`runId`、两段序号、接手前后 `attempts`、副作用计数与旧令牌结果；在 `document_evidence` 中记录材料标识、解析任务标识、页数、分块数、第 3 页页码与来源、原件哈希、重复上传与取消/接手结果；页面提示、布局、语言、断线时序与 PDF 浏览器实际落页仍只写入 `docs/acceptance/`。
- 工作树存在未忽略改动时 `make verify` 默认拒绝执行；仅在本地调试时可用 `ALLOW_DIRTY=1`，此时报告记录 `dirty: true`，**不得**作为干净克隆证据。
- CI 必须调用与本地相同的 `make verify`，并断言报告 `tested_sha` 等于触发 SHA；不得在 CI 中重复实现验收逻辑。
- CI 成功与失败运行都必须上传报告，产物缺失即视为配置错误；使用 `gh workflow run verify -f expected_sha=<错误 SHA>` 执行受控失败演练，验证失败路径与失败报告。
- 人工验收记录只写 `docs/acceptance/`，`make verify` 与 CI 不得创建、修改或删除该目录内容。
- 干净克隆、CI 产物与人工记录必须能通过同一个 SHA 对齐；报告与运行链接写入记录的“证据”字段。

## 提交与评审

- 使用 Conventional Commits：`feat:`、`fix:`、`ci:`、`docs:`、`chore:`、`test:`、`refactor:`。
- 每次提交保持单一意图；验收前先提交，避免脏工作树导致结论不可追溯。
- 修改健康检查语义、端口绑定、验收脚本或 CI 断言时，必须同时更新对应的 OpenSpec 规范与本文件相关条目。
- 不得删除或弱化既有测试来让流水线通过；失败时修复实现或明确记录阻塞项。
