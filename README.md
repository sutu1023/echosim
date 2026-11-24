# EchoSim

材料驱动的双平台推演工作台。

当前版本提供可启动、可复现的工程骨架，已实现本地账户登录、服务端会话、项目级隔离、材料入库、由独立 worker 执行的证据图谱构建与原文定位，以及从图谱最终快照生成、审阅并冻结角色与环境。双平台推演、报告与案例回放尚未实现。

## 前置条件

- Docker Engine 与 Docker Compose v2（`docker compose version` 可用）
- GNU Make 4.x
- Python 3.11+（`make verify` 使用，且只用标准库）
- Git
- 端口 `127.0.0.1:8080` 可用

本地前端开发额外需要 Node.js 22（见 `.nvmrc`），仅用于 `npm ci && npm run build` 等命令，`make up` 的容器构建自带 Node。

## 快速开始

```bash
git clone <repo-url> echosim
cd echosim
make up
```

`make up` 在缺少 `.env` 时自动从 `.env.example` 生成，然后构建镜像并等待应用与数据库健康。应用启动时会自动应用数据库迁移。

浏览器打开 <http://127.0.0.1:8080/>，会看到公开的中文控制台；进入“我的项目”或工作台仍需要登录。系统不提供自助注册，先在本机创建账户：

```bash
printf '%s\n' 'dev-password-123' | make user EMAIL=a@example.com
```

`make user` 通过标准输入读取密码，避免出现在 shell 历史与进程列表中；密码需 12–128 字符。重复创建同一邮箱会失败，如需重设密码：

```bash
printf '%s\n' 'new-dev-password-123' | \
  docker compose exec -T app python -m app.manage create-user \
    --email a@example.com --password-stdin --update-password
```

更新密码会撤销该用户已有会话。

登录默认进入 `/projects`（我的项目）。可以创建项目、查看项目详情、从“历史”页查看自己项目的最近事件，并打开项目工作台的五个阶段。所有页面使用相对路径请求同源 API。

健康检查：

```bash
curl -i http://127.0.0.1:8080/health
```

数据库可用时返回 `200` 与 `{"status":"ok","database":"ok"}`；数据库不可用时返回 `503` 与 `{"status":"degraded","database":"unavailable"}`。

每次请求都会带 `X-Request-ID` 响应头：合法的请求头会被复用，否则生成随机 ID。探测失败时服务端日志包含同一个 `request_id`，便于关联：

```bash
curl -s -D- -H 'X-Request-ID: accept-123' http://127.0.0.1:8080/health
docker compose logs app | grep 'request_id=accept-123'
```

停止环境：

```bash
make down
```

`make down` 停止并移除容器与网络、释放 `127.0.0.1:8080`，但保留 PostgreSQL 数据卷；之后 `make up` 可直接再次启动（迁移会自动跳过已应用版本）。

## 登录、会话与退出

- 登录成功后服务端在 `sessions` 表创建会话，浏览器收到 `echosim_session` Cookie；令牌本身不落库，数据库只保存其 SHA-256。
- Cookie 带有 `HttpOnly`、`SameSite=Lax`、`Path=/`，**不设置 `Max-Age` 或 `Expires`**，属于浏览器会话 Cookie。
- 刷新页面：浏览器继续携带 Cookie，保持登录。
- 关闭并完全退出浏览器：浏览器丢弃会话 Cookie，重新打开后访问私有页面会跳转登录页，需要重新登录。无痕窗口同理。
- 某些浏览器的“恢复上次会话”可能保留 Cookie；服务端仍以绝对有效期拒绝过期会话，默认 7 天（`SESSION_TTL_SECONDS`）。
- 点击“退出登录”：服务端删除当前会话并清除 Cookie，退出前捕获的 Cookie 再次使用会返回 401。
- 退出只影响当前浏览器会话；同一账户在其他浏览器的会话不受影响。

导出的会话 Cookie 属性示例：

```text
echosim_session=<随机令牌>; HttpOnly; Path=/; SameSite=lax
```

本地 HTTP 不设置 `Secure`；将来在 HTTPS 下部署时把 `SESSION_COOKIE_SECURE=true`。

## 控制台、项目与工作台

| 页面 | 路径 | 访问 | 说明 |
| --- | --- | --- | --- |
| 公开控制台 | `/` | 公开 | 系统状态、五阶段、材料入口、推演问题与“开始”按钮 |
| 登录 | `/login` | 公开 | 邮箱 + 密码；已登录访问会跳 `/projects`；私有页面会带 `next` 跳转到这里 |
| 我的项目 | `/projects` | 私有 | 登录默认落点；我的项目 + 创建表单；`?intent=create` 会聚焦创建表单 |
| 项目详情 | `/projects/<项目 ID>` | 私有 | 仅所有者可见；可进入第一阶段工作台 |
| 工作台阶段 | `/projects/<项目 ID>/workbench/<阶段 slug>` | 私有 | 五个阶段可深链接；共用工具条、布局与语言 |
| 历史 | `/history` | 私有 | 仅聚合自己项目的创建事件，最多 50 条；条目仍跳项目详情 |

工作台阶段 slug 固定为：

| 顺序 | 阶段 | slug |
| --- | --- | --- |
| 1 | 构建证据图谱 | `evidence-graph` |
| 2 | 准备角色与环境 | `roles-environment` |
| 3 | 双平台运行 | `dual-platform-run` |
| 4 | 生成报告 | `report` |
| 5 | 深度互动 | `interaction` |

`/projects/<项目 ID>/workbench` 会重定向到 `evidence-graph`，并保留 `layout` 与 `lang` 查询参数。工作台支持 `layout=graph|split|workbench`（默认 `split`）和 `lang=zh|en`（默认 `zh`）；布局/语言切换使用替换历史记录，刷新可从 URL 恢复。阶段导航、 “前往上一阶段”和无阶段重定向都会显式保留当前查询参数。

“开始”按钮的行为：

- 访客：进入 `/login?next=/projects`，登录后进入我的项目；
- 已登录且没有项目：进入 `/projects?intent=create`，聚焦创建表单；
- 已登录且已有项目：进入最近创建项目的第一阶段工作台。

控制台把健康、会话与项目列表分开显示：系统状态只由 `/health` 决定；会话查询 503 时账户显示“暂时无法确认”并保留 Cookie；只有会话明确 401 才按访客处理。健康检查 200 但项目列表读取失败时，系统状态仍为“可用”，按钮进入 `/projects` 由项目页提示读取失败并重试。

工作台外壳与阶段数据分开：真实项目加载成功后，项目名、阶段高亮、工具条与三种布局可正常操作。第一阶段在材料面板旁提供证据图谱面板：选择已就绪材料、输入情景问题、开始建图，页面按“等待索引 → 增量绘图 → 完成”展示服务端已发布快照；点击节点或材料关系可回到 PDF 物理页或文本原文高亮。第二阶段从该 build 的最终快照生成角色与环境：显示“图谱未完成 / 尚未准备 / 准备中（含分批预览）/ 已就绪待确认 / 已确认 / 失败或取消”六态，支持普通开始、失败后的显式重试、轮数保存、带版本确认冻结与进入第三阶段入口。

所有项目接口先鉴权再判断归属，工作台页面也遵循“身份 → 项目归属 → 阶段合法性”的顺序：

| 场景 | 状态码与响应 |
| --- | --- |
| 未携带 Cookie 请求任意私有接口 | `401` `{"detail":"未登录"}`，且不查询数据库（数据库停机时同样 401） |
| 携带 Cookie，但会话查询遇数据库不可达 | `503` `{"detail":"服务暂时不可用"}`，5 秒内返回，且**不清除** Cookie；恢复后原会话继续可用 |
| 所有者访问自己的项目 | `200` |
| 其他用户访问该项目 / 项目的 ID 不存在 / ID 格式非法 | 均为 `404` `{"detail":"项目不存在"}`，状态码与响应体完全一致 |
| 登录凭据错误 / 邮箱不存在 | 均为 `401` `{"detail":"邮箱或密码不正确"}`，不区分原因 |
| 登录或创建项目字段非法 | `422`，且不写入数据 |
| 跨站 `Origin` 的写请求 | `403`，业务逻辑与数据库写入均不执行 |

## 配置

所有配置项都在 `.env`（由 `.env.example` 复制）中，均可省略并使用下列默认值。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `ECHOSIM_PORT` | `8080` | 宿主机发布端口，始终只绑定 `127.0.0.1` |
| `POSTGRES_USER` | `echosim` | 本地 PostgreSQL 用户名 |
| `POSTGRES_PASSWORD` | `echosim_dev_password` | 本地 PostgreSQL 密码（占位值，禁止用于真实环境） |
| `POSTGRES_DB` | `echosim` | 本地 PostgreSQL 数据库名 |
| `HEALTH_DB_TIMEOUT_SECONDS` | `4` | `/health` 数据库探测整体超时（秒），需保证 5 秒内返回 503 |
| `SESSION_TTL_SECONDS` | `604800` | 服务端会话绝对有效期（秒），默认 7 天 |
| `SESSION_COOKIE_SECURE` | `false` | 是否给会话 Cookie 添加 `Secure`；本地 HTTP 保持 `false` |
| `JOB_LEASE_SECONDS` | `6` | 任务租约时长；到期后允许任意可用 worker 原子接手 |
| `JOB_HEARTBEAT_SECONDS` | `2` | worker 执行中的续租间隔，必须小于租约时长 |
| `WORKER_POLL_INTERVAL_SECONDS` | `0.25` | worker 空闲时领取任务的轮询间隔 |
| `TEST_TASK_STEP_DELAY_MS` | `250` | 测试任务每步间隔（毫秒），仅用于连通性自检与放大验收窗口 |
| `DOCUMENT_MAX_BYTES` | `20971520` | 单份材料字节上限（20 MiB），恰好等于上限仍通过 |
| `DOCUMENT_MAX_PAGES` | `2000` | 单份 PDF 页数上限，超过即以“页数过多”失败 |
| `DOCUMENT_CHUNK_TARGET_CHARS` | `1200` | 分块目标长度（字符） |
| `DOCUMENT_CHUNK_OVERLAP_CHARS` | `120` | 同一段落被拆开时的重叠长度（字符） |
| `DOCUMENT_INGEST_DELAY_MS` | `0` | 解析前的可控延迟（毫秒），仅用于验收放大慢解析窗口 |
| `EVIDENCE_GRAPH_MODEL_PROVIDER` | `auto` | 业务建图模型 provider：`auto`、`unconfigured`、`http`、`stub` |
| `EVIDENCE_GRAPH_GRAPH_PROVIDER` | `auto` | 业务建图图谱 provider：`auto`、`unconfigured`、`zep`、`stub` |
| `EVIDENCE_GRAPH_ALLOW_STUB` | `false` | 是否允许显式启用测试替身；默认拒绝 stub |
| `EVIDENCE_GRAPH_MAX_DOCUMENTS` | `5` | 单次业务建图材料上限 |
| `EVIDENCE_GRAPH_MAX_NODES` | `500` | 单次业务建图节点上限 |
| `EVIDENCE_GRAPH_MAX_EDGES` | `800` | 单次业务建图边上限 |
| `EVIDENCE_GRAPH_MAX_SOURCES_PER_ITEM` | `5` | 单个节点或边来源上限 |
| `EVIDENCE_GRAPH_QUESTION_MAX_CHARS` | `500` | 情景问题去除首尾空白后的最大字符数 |
| `EVIDENCE_GRAPH_BATCH_SIZE` | `4` | 模型抽取批次大小 |
| `EVIDENCE_GRAPH_INDEX_TIMEOUT_SECONDS` | `60` | episode 写入后等待索引匹配的预算（秒） |
| `EVIDENCE_GRAPH_PROCESSING_TIMEOUT_SECONDS` | `120` | episode 处理与读回预算（秒） |
| `ENVIRONMENT_MODEL_PROVIDER` | `auto` | 角色画像 provider：`auto`、`unconfigured`、`http`、`stub`；只注入 `worker` |
| `ENVIRONMENT_ALLOW_STUB` | `false` | 是否允许显式启用画像替身；默认拒绝 stub |
| `ENVIRONMENT_DEFAULT_DURATION_HOURS` | `72` | 新准备默认模拟总时长（小时） |
| `ENVIRONMENT_DEFAULT_MINUTES_PER_ROUND` | `60` | 新准备默认每轮分钟数；72×60/60 = 72 轮 |
| `ENVIRONMENT_MAX_MANUAL_ROUNDS` | `1000` | 手动轮数上限 |
| `ENVIRONMENT_PROFILE_BATCH_SIZE` | `2` | 分批画像的角色数；批次按角色键稳定排序 |
| `ENVIRONMENT_MODEL_TIMEOUT_SECONDS` | `30` | 单批画像模型预算（秒），最多 2 次实际 HTTP 请求 |
| `ENVIRONMENT_PROFILE_DELAY_MS` | `0` | 批次之间的可控延迟（毫秒），仅用于验收放大分批预览窗口 |
| `MODEL_API_BASE_URL` | 空 | Chat Completions 兼容 API 根地址，可包含 `/v1`；空表示未配置 |
| `MODEL_API_KEY` | 空 | 模型密钥；只注入 `engine` 与业务 `worker` 服务 |
| `MODEL_NAME` | 空 | 模型名；空表示未配置 |
| `MODEL_TIMEOUT_SECONDS` | `30` | 模型项总时限（秒），含重试与移除 `response_format` 重试 |
| `ZEP_API_KEY` | 空 | Zep 密钥；只注入 `engine` 与业务 `worker` 服务 |
| `ZEP_API_BASE_URL` | 空 | 可选 Zep 服务地址；空表示使用 SDK 默认 |
| `ZEP_ACCEPTANCE_GRAPH_ID` | 空 | 固定验收专用图谱的 `graph_uuid`；空表示未配置，只注入 `engine` |
| `ZEP_ACCEPTANCE_GRAPH_FINGERPRINT` | 空 | 由宿主启动流程从验收图谱 UUID 派生，只注入 `worker`；不要手工填写 |
| `GRAPH_WRITE_TIMEOUT_SECONDS` | `20` | 单次 episode 写入时限（秒） |
| `GRAPH_STATUS_TIMEOUT_SECONDS` | `10` | 写入响应丢失后的单次核对时限（秒） |
| `GRAPH_INDEX_TIMEOUT_SECONDS` | `60` | `graph.search_episodes` 索引等待上限（秒） |
| `GRAPH_POLL_INTERVAL_SECONDS` | `3` | 索引期间检索间隔（秒） |
| `SIMULATION_TIMEOUT_SECONDS` | `60` | 单个 OASIS 平台的动作与读回总时限（秒） |
| `SMOKE_LIVE_TOTAL_TIMEOUT_SECONDS` | `300` | 整次 `make smoke-live` 总时限（秒） |

覆盖端口示例：

```bash
ECHOSIM_PORT=8090 make up
```

`.env` 与 `.env.*`（`.env.example` 除外）已被 `.gitignore` 忽略，禁止提交任何真实凭据。

## 后台任务与事件流

`app` 只处理请求，长任务由独立 `worker` 容器执行；PostgreSQL 是队列与进度的唯一事实来源。当前只提供一个**测试任务**（连通性自检，10 步，不产生推演结论、不改变阶段进入条件）。

### 接口

所有接口都在当前用户自有项目下，先鉴权、再校验项目归属，最后解析任务与游标。

| 方法 | 路径 | 说明 | 成功 | 失败 |
| --- | --- | --- | --- | --- |
| POST | `/api/projects/{projectId}/jobs` | 开始测试任务，请求体 `{"operation":"test-simulation","stage":"<阶段 slug>","failAtStep":null}` | 新建 `202`；同一项目同一操作已有活动任务时 `200` + `deduplicated:true`，返回同一 `jobId`/`runId` | 401 / 404 / 422 / 503 |
| GET | `/api/projects/{projectId}/jobs` | 最近 20 个任务 | `200 {"jobs":[…]}` | 401 / 404 / 503 |
| GET | `/api/projects/{projectId}/jobs/{jobId}` | 任务详情（含 `attempts`、`stepsApplied`、`awaitingTakeover`） | `200` | 401 / 404 项目或任务不存在 / 503 |
| POST | `/api/projects/{projectId}/jobs/{jobId}/cancel` | 取消；`queued` 立即生效，`running` 在下一步边界生效 | `200 {job, cancelApplied}`，终态任务 `cancelApplied:false` | 401 / 404 / 503 |
| GET | `/api/projects/{projectId}/jobs/{jobId}/events` | 事件流。默认 SSE；`format=json` 返回同游标的补发结果 | `200` | 400 游标非法 / 401 / 404 / 503 |
| GET | `/api/projects/{projectId}/jobs/{jobId}/effects` | 测试副作用计数（总数、去重步骤号） | `200` | 401 / 404 / 503 |

### 状态与租约

| 起点 | 触发 | 终点 | 同时发生 |
| --- | --- | --- | --- |
| （无） | 开始，且无活动任务 | `queued` | 插入 `runs` + `jobs`，不写事件 |
| （无） | 开始，但已有活动任务 | 不变 | 返回既有 `jobId`/`runId`，不新增 job/run |
| `queued` | worker 领取 | `running` | `attempts+1`，生成新租约令牌 |
| `running` | 第 1–9 步提交 | `running` | 副作用 + `progress` 事件 + `next_step` |
| `running` | 第 10 步提交 | `succeeded` | 副作用 + `completed` 事件（同一事务） |
| `running` | 受控失败于第 N 步 | `failed` | `failed` 事件 `seq=N`，不写第 N 步副作用 |
| `running` | 取消 | `canceled` | `canceled` 事件 `seq = 最后已提交 seq + 1` |
| `running` | 租约过期 | 仍为 `running` | 查询返回 `awaitingTakeover:true`；下一次领取原子接手 |
| 终态 | 任何请求 | 不变 | 取消返回现状；允许开始新任务（新 `jobId`/`runId`，`seq` 从 1） |

- 所有进度、续租与终态写入都限定 `jobId` 与本次领取的租约令牌；租约有效性在**锁定任务行之后**用独立的 `clock_timestamp()` 读取实际时钟判断（`now()` 是事务开始时间，锁等待跨过到期点时不得据此续租或写入）。旧 worker 恢复后续租和写入都会被拒绝。
- 只有一个 worker 且它停止时，没有进程会改写任务行；租约到期后由查询暴露 `awaitingTakeover`，重启 worker 即可接手。
- “做到第几步”的唯一依据是 `run_steps` 与 `jobs.next_step`，两者与事件在同一事务提交。

### 事件与 SSE

事件字段固定为 `runId`、`seq`、`type`、`stage`、`simulatedAt`、`payload`；`seq` 在同一 run 内从 1 递增。确定性测试任务共 10 条业务事件：`seq=1..9` 为 `progress`，`seq=10` 为 `completed`（终态事件计入），心跳与 `stream-end` 不计入。

```text
retry: 1000

id: <runId>:4
event: run-event
data: {"runId":"…","seq":4,"type":"progress","stage":"dual-platform-run","simulatedAt":"2026-11-21T09:03:00Z","payload":{"step":4,"stepsApplied":4,"totalSteps":10}}

: keep-alive

event: stream-end
data: {"runId":"…","lastSeq":10,"terminal":true}
```

- 游标 = 客户端已完整处理的最后一个连续 `seq`。无游标视为 0；合法范围 `0..当前最大 seq`：`after=4`（最大为 4）继续等待第 5 条，`after=5` 返回 `400 {"detail":"游标无效"}`。
- `Last-Event-ID: <runId>:7` 优先于 `after`；跨 run 或格式非法的游标返回 400，且鉴权与归属校验始终先于游标校验。
- 终态任务用 `after=<lastSeq>` 订阅也会先补发（可能为空）再立即收到 `stream-end` 并关闭，不会挂起。
- 事件先落库再推送；断线期间事件照常写入，重连按游标补齐。前端用一个 reducer 按 `(runId, seq)` 去重、以连续游标重连，遇到缺口先暂存并提示重新同步。
- 面板在断线时优先显示“正在从第 N 条重连”，状态轮询失败不会覆盖该提示；网络恢复并成功读取后自动清除错误。首次任务列表读取失败时提供“重试读取”按钮，只重发 `GET /jobs` 并恢复已有任务、日志与副作用计数。

### worker

```bash
docker compose logs -f worker        # 领取、续期、接手、终态日志（含 jobId/attempt）
docker compose stop worker           # 只停执行，不影响 /health 与 /livez
docker compose start worker          # 租约到期后自动接手未完成任务
```

`worker` 由 Dockerfile 的 `worker-graph` 目标构建为 `echosim-worker-graph:dev`，只比 app 多安装 `zep-cloud==4.1.0` 与必要直接依赖；`app` 由 `app` 目标构建为 `echosim-app:dev`，不包含 Zep 或 camel。worker 没有 HTTP 端口，因此 Compose 用“心跳文件 15 秒内更新”的探针替换继承探针；`app` 的 `/livez` 探针保持不变。worker 不对外发布端口。

## 材料入库与来源定位

材料属于项目私有数据：原件保存在 Compose 卷 `echosim-documents`，数据库只记录 SHA-256 与相对路径；解析由独立 `worker` 执行。清洗后的文本只用于阅读摘录，查看来源时以原件为准：PDF 使用上传的原始字节，文本使用按解析编码解码后的未清洗原文。

### 接口

先鉴权，再经 `resolve_owned_project` 校验项目归属，最后解析材料标识；匿名请求不访问数据库，他人项目与他人材料对外不可区分。

| 方法 | 路径 | 说明 | 成功 | 失败 |
| --- | --- | --- | --- | --- |
| POST | `/api/projects/{projectId}/documents` | multipart 字段 `file`；PDF、Markdown 或 TXT，单文件 ≤ 20 MiB | 新建 `201`；同项目同内容 `200` + `deduplicated:true`，返回同一 `documentId` | `400` 空文件 / `401` / `404` / `413` 超限 / `415` 类型 / `503` |
| GET | `/api/projects/{projectId}/documents` | 项目材料列表，附带当前 `limits.maxBytes` 与 `limits.maxPages` | `200 {"documents":[…],"limits":{…}}` | `401` / `404` / `503` |
| GET | `/api/projects/{projectId}/documents/{documentId}` | 材料详情：状态、页数、分块数、失败原因、`jobId`/`runId` | `200` | `404` 材料不存在 |
| GET | `/api/projects/{projectId}/documents/{documentId}/chunks?after=<序号>&limit=<≤50>` | 摘录分页（按分块序号升序） | `200 {chunks,total,ready}` | `401` / `404` / `422` / `503` |
| GET | `/api/projects/{projectId}/documents/{documentId}/source` | 文本原文视图（UTF-8、未清洗，用于段落定位） | `200 text/plain` | `409` PDF 材料 / `404` |
| GET | `/api/projects/{projectId}/documents/{documentId}/file` | 原件字节；PDF `application/pdf`、文本 `text/plain`，带 `nosniff` | `200` | `401` / `404` / `503` |

上传成功响应另含 `jobId`、`runId`，可用于查询或取消解析任务；已就绪材料的重复上传不带任务标识。

### 状态、解析与定位

- 材料状态只有 `queued`、`parsing`、`ready`、`failed` 四种；失败原因包括 `empty_file`、`file_too_large`、`unsupported_type`、`decode_failed`、`no_text_layer`、`original_missing`、`too_many_pages`、`parse_error` 与 `canceled`。
- 上传在请求内完成大小、类型与空文件校验；解析是后台任务，`runs.kind = document.ingest`，不会出现在“测试任务”面板的列表里。
- PDF 逐页读取，物理页码从 1 开始且等于页对象顺序；空白页计入总页数并产生 0 个分块。重复页眉、页脚与打印路径只从分块文本中移除，页码不变。
- Markdown/TXT 优先 UTF-8（含 BOM），其次带 BOM 的 UTF-16 与可可靠识别的 GB18030；无法可靠解码时材料失败并说明原因。正文中的路径与 URL 不会被当作页眉清理。
- 每个分块都带 `pageNo`（PDF）或 `paragraphStart`/`paragraphEnd`（文本）以及 `sourceStart`/`sourceEnd`。偏移单位是 **UTF-16 代码单元**、区间左闭右开，浏览器可直接用它切片与高亮。
- 同一项目内相同内容只保留一套有效材料与分块；失败材料不阻止重新上传。分块与 `ready` 在同一事务内发布，中断接手不会留下半成品或重复分块。
- 失败的页数超限记录保存解析时的实际页数与上限；之后修改 `DOCUMENT_MAX_PAGES` 只影响新上传与上传提示，历史材料仍按解析时的上限说明原因，修复前没有该信息的旧记录不显示上限数字。
- 取消解析在取消事务内同时写入 job `canceled`、终态事件与材料失败；worker 停止时取消排队任务也立即生效。
- 材料深链接就是完整地址：`/projects/<项目 ID>/workbench/evidence-graph?layout=split&lang=zh&document=<材料 ID>`，刷新或粘贴后回到同一材料，`layout` 与 `lang` 保持不变。

## 证据图谱构建

证据图谱由独立 `worker` 执行，`app` 只在请求内创建任务并读取已发布快照，不持有模型或 Zep 凭据。默认启动、默认 `make verify` 与 CI 不发起真实模型或图谱调用；只有显式配置并在工作台显式开始建图或执行 `make smoke-live` 才使用真实 provider。

建图采用与参考项目一致的主链路：模型根据整份报告与问题规划本体，worker 从已入库分块重建 500 字/50 字重叠的建图全文并把**原文分块**作为 episode 写入本次 build 的独立图谱，等待索引后按本次 episode UUID 分页读回全部节点、边与事实，再把关系关联到 episode 与支持原句。模型不再预抽取 JSON 清单，也不登记“首轮最多 24 个主体”的白名单。

### 使用流程

1. 在项目第一阶段上传并等待至少一份材料变为 `ready`。
2. 在证据图谱面板选择材料（单次最多 5 份），输入 1–500 字符情景问题，点击“开始建图”。
3. 请求立即返回 `buildId`、`jobId`、`runId`；worker 按 `bind → plan → extract → write → readback → publish` 六步执行：`plan` 输出实体/关系类型，`extract` 重建建图全文并设置本体，`write` 写入原文 episode，`readback` 分页读回节点/边/事实并定位支持原句。
4. 页面在 `waiting_index` 显示已受理/已确认数量；第 5 步按已处理 episode 批次读回，某一批稳定且来源可核对后立即发布 `complete=false` 部分快照并进入 `drawing`，其余批次继续处理，后续版本继续更新画面；第 6 步全部完成后在同一事务发布最终快照、`phase=ready`、`canContinue=true`、完成事件与任务成功。
5. 点击节点查看该主体参与的全部材料关系；跨页/跨 episode 的同一规范化名称合并为一个节点，节点身份依据只保留代表性来源。点击关系查看“起点—动作—终点”、来源性质与支持该说法的原句；只有媒体/机构名单、没有动作的引文不会作为关系来源，建议或撤销表述不会挂到已发生的正式决定下。PDF 来源打开 `/file#page=<物理页>`；文本来源打开 `/source` 并按 UTF-16 偏移高亮未清洗原文。
6. 关系来源性质分为媒体报道、当事方主张、报告建议、机构正式决定与其他材料陈述；“某方称”不会显示为已证实事实。平台上的用户行动读成“用户在平台上行动”，被转述的不实说法显示“报告所称/未证实”，作者建议显示“报告建议”。完成后“进入角色与环境”使用服务端 `canContinue`；未完成、失败或来源不完整时禁用。

默认读取项目当前活动 build；没有活动 build 时读取最近 build。带 `?build=<buildId>` 可打开旧 build，`layout`、`lang` 与 `document` 保留。第二次建图仍在等待时，页面不会显示第一次完成图；`buildId` 不匹配的迟到响应会被丢弃。

### 接口

| 方法 | 路径 | 说明 | 成功响应 | 失败 |
| --- | --- | --- | --- | --- |
| POST | `/api/projects/{projectId}/evidence-graph/builds` | 开始建图；材料去重、非空、全部 `ready` 且最多 5 份 | `202` 新建 / `200` 复用活动 build 与 `buildId`/`jobId`/`runId` | `401` / `404` / `409` 材料未就绪 / `422` 参数或材料数 / `503` |
| GET | `/api/projects/{projectId}/evidence-graph` | 默认活动/最近 build 与最近已发布快照；`?build=` 指定旧 build | `200 {"build":…,"snapshot":…}` | `401` / `404` / `503` |
| GET | `/api/projects/{projectId}/evidence-graph/builds/{buildId}` | 指定 build 与已发布快照 | `200` | `401` / `404` |
| GET | `/api/projects/{projectId}/evidence-graph/builds/{buildId}/snapshots/{revision}` | 指定已发布快照版本 | `200` | `400` 版本非法或超出当前范围 / `404` |
| GET | `/api/projects/{projectId}/jobs/{jobId}/events` | 复用任务 SSE；建图事件含 `buildId`、`phase`、受理/确认/计划 episode 数与 `snapshotRevision` | `text/event-stream` | `401` / `404` / `400` 游标 |

### 状态、上限与原因

- `phase` 固定为 `preparing`、`waiting_index`、`drawing`、`ready`、`failed`、`canceled`；任务状态仍只有 `queued`、`running`、`succeeded`、`failed`、`canceled`。
- 写入受理、episode 匹配、`processed` 与节点/边读回分开记录；可搜索不等于处理完成，读回不稳定或没有可用边时继续等待并按预算失败，不假完成。
- 规模上限：单次 5 份材料、500 个节点、800 条边、每项 5 条来源。超限在发布前以 `graph_too_large` 或 `too_many_sources` 失败，不发布截断结果。
- 常见失败码：`missing_config`、`document_not_ready`、`invalid_graph_scope`、`invalid_model_output`、`source_mismatch`、`ambiguous_source`、`source_unavailable`、`index_timeout`、`processing_timeout`、`graph_incomplete`、`insufficient_graph`、`write_unconfirmed`。失败只保留通用中文说明与原因码，不返回材料正文、密钥或完整外部响应。
- 同一项目同一操作最多一个活动任务；`graph.create` 绑定使用持久化幂等键与稳定参数，响应丢失或 worker 接手都只保存一个 `graph_id`。真实 provider 使用独立的真实图谱绑定；旧 stub 绑定与旧快照不被覆盖，真实 build 不会复用 stub 图谱。
- 同一引文在材料中出现多次且模型偏移不能唯一消歧时返回 `ambiguous_source`，绝不取第一次匹配；既有入库材料无需重新上传即可定位。

### 离线走查

默认配置使用 `auto`，凭据缺失时在外部调用前失败。需要离线演示时，在本地 `.env` 显式设置：

```bash
EVIDENCE_GRAPH_MODEL_PROVIDER=stub
EVIDENCE_GRAPH_GRAPH_PROVIDER=stub
EVIDENCE_GRAPH_ALLOW_STUB=true
```

随后 `make up`，在工作台选择原创材料并开始建图。该模式下模型与图谱调用次数记为 `double`，真实调用为 0；私有材料与演示截图不得提交到仓库。

## 角色与环境准备

角色与环境只从第二阶段指定的 build 读取：`buildId`、`snapshotId` 与推演问题在开始准备时固定，角色节点、角色来源与冻结选择都引用同一 `snapshotId`。准备由独立 `worker` 执行，`app` 只负责开始、读取与确认，不在 HTTP 请求内调用模型。

### 使用流程

1. 在第一阶段完成建图，点击“进入角色与环境”；地址保留 `layout`、`lang` 与 `build`。直接打开没有 `build` 的地址时，服务端按“活动 build 优先、否则最近 build”返回，页面用替换历史记录把 `buildId` 固定到地址。
2. 第二阶段显示推演问题、最终快照与开始入口。开始准备返回 `202`；同一 build 已有 `preparing` 或 `ready` 准备时返回 `200` 与同一 `preparationId`/`jobId`/`runId`。失败或取消后普通开始返回 `409 preparation_retry_required`，只有显式“重试准备”才创建新的准备与任务。
3. worker 按 `bind → select → profile → compose → publish` 五步执行。`profile` 按稳定批次生成画像，每批通过校验后即可在详情中作为 `previewRoles` 展开；SSE 只携带准备/快照标识、批次标识、角色标识与计数。第一批可见时确认与第三阶段入口仍不可用。
4. 第 5 步 `publish` 在单个事务内发布最终角色、平台映射、初始话题、首发话题、激活顺序、`phase=ready`、完成事件与 `succeeded`。发布前只能看到已完成批次预览，不能看到平台、话题、顺序或冻结选择。
5. 页面显示服务端 `autoRounds` 与生效 `rounds`。默认 72 小时、每轮 60 分钟，自动轮数为 72；可切换手动保存 40、3 等值，刷新后从服务端读回。零时长、非法轮数与不能整除分别返回 `duration_invalid`、`rounds_invalid`、`rounds_not_integral`。
6. 确认按钮携带用户最后读到的 `settingsRevision`。服务端在同一事务内核对版本、重新校验就绪条件、轮数与来源后创建冻结选择；版本不一致返回 `409 settings_conflict` 且不创建冻结记录。相同版本重复确认返回同一 `selectionId`；确认后修改轮数返回 `409 preparation_frozen`。
7. 轮数表单有未保存修改或非法输入时，确认按钮禁用并说明原因；保存成功后从服务端读回轮数与 `settingsRevision` 才允许确认。
8. 确认后“进入双平台运行”可用，并把 `build`、`preparation` 与当前 `layout`、`lang` 一起带过去。第三阶段只读取服务端持久化的 `frozenSelection`，显示“准备已确认，运行功能待接入”；直接刷新该地址也读取同一冻结选择。实际双平台运行属于后续变更。

### 角色来源与材料事实

角色候选直接读取已发布快照的 `nodeType`、`label`、节点来源与关系来源：

- 同时识别中文显示类型（`个人`、`群体`、`高校`、`机构`、`媒体机构` 等）与旧英文 PascalCase（`*Person`、`*Organization`、`*University` 等）；`summary=null` 不影响筛选。
- 媒体机构与平台渠道分开：`媒体机构`/`新闻机构`/`MediaOutlet` 可以发言；`平台`/`社交媒体`/`传播平台`/`Platform`/`Channel` 是环境渠道，不成为角色。
- 关系方向只提供上下文：只出现在关系终点的高校仍可入选；有出边的事件、待核实说法与建议仍被排除。
- `其他主体` 等未知类型只有在节点来源原句明确描述为人物/群体/组织/机构/媒体机构时才纳入。
- 没有合格主体时开始请求返回 `422 no_role_candidates`，不创建准备、job 或 run。

未知类型的角色判定绑定当前主体及其所在从句：“某大学聘请林记者担任讲师”不会把林记者判成高校，而按名称中的记者身份归为个人；表达话题只认当前主体自己的动作，“王同学参加咨询会，主办方表示……”不会把主办方表态算给王同学。证据不足时保留未知或“材料涉及的话题”。角色详情同时显示该角色的 `simulation.topics` 并标为模拟设定。英文界面把角色类型与 PDF 页码标签翻译为英文，角色显示名、项目名与材料名保持原值。

角色详情把材料实际支持的内容分层显示：

| 展示 | 依据 |
| --- | --- |
| 材料提及 | 节点来源引文，只证明材料提到该主体 |
| 背景 | 关系支持原句明确描述该主体；否则显示“材料未说明” |
| 该角色表达/主张 | 关系起点是该角色，且支持原句能读出表达、主张或回应 |
| 材料涉及的话题 | 角色只是关系一端，原句只能证明材料把角色与话题关联 |
| 模拟设定 | 模型或替身补充的动机、立场、活跃时段、发言频率、响应延迟、影响力权重与关注点 |

来源定位复用证据图谱语义：PDF 返回物理页码与原件 `#page=<pageNo>`；文本返回 UTF-16 偏移与原文视图，并将 `sourceStart`/`sourceEnd` 带入材料深链接自动高亮。关系来源保留快照已有的 `claimNature`；旧边为空时保留未知并显示“来源性质未知”，不默认成其他材料陈述。`author_recommendation` 只作为环境背景，不作为角色表达或关注话题。

### 环境、轮数与冻结

- 平台使用稳定别名：`platform_a → twitter`、`platform_b → reddit`。这是平台映射；Change 06 的 `create_post`/`create_comment` 只是连通性探针动作，不会写入冻结选择或工作台作为唯一允许动作。
- 初始话题至少包含 build 的推演问题；材料话题带来源与 `claimNature`（旧来源为空时保留未知）；替身或模型补充的话题标记为模拟设定。
- 激活顺序覆盖全部角色且不重复；首发角色是顺序前缀，并通过 `firstSpeakerTopics` 与具体初始话题一一对应。
- 冻结选择保存 `selectionId`、`preparationId`、`projectId`、`buildId`、`snapshotId`、`question`、`settingsRevision`、轮数、完整角色（提及/背景/表达话题/关联话题/模拟设定）、平台映射、初始话题的来源性质、首发角色与话题对应和激活顺序。之后的新 build 不会改变该冻结选择。
- 分批画像返回的 `initialTopics` 会与材料话题、角色模拟话题合并进入最终配置与冻结选择；只出现在批次初始话题中的合法话题也不会丢失，模拟来源保留未知来源性质。
- 同一 build 的 `preparing`/`ready` 准备最多一个；不同 build 的操作键不同，可各自准备。准备任务不会出现在“测试任务”面板或副作用计数中。
- worker 停止或租约过期时，查询返回 `awaitingTakeover`；新 worker 接手保持 `preparationId`/`jobId`/`runId`，复用已 `preview_ready_at` 的批次，不重复生成已提交角色。失败或取消后已完成批次只读显示为“未完成预览”，`canContinue=false`，没有平台、话题、顺序或冻结选择。

### 接口

| 方法 | 路径 | 说明 | 成功 | 失败 |
| --- | --- | --- | --- | --- |
| GET | `/api/projects/{projectId}/environment?build=<buildId>` | build、最终快照、推演问题与当前准备上下文 | `200` | 401 / 404 项目或图谱 / 503 |
| POST | `/api/projects/{projectId}/environment/preparations?build=<buildId>` | 普通开始 | `202` 新建 / `200` 复用 `preparing`/`ready` | 401 / 404 / 409 `graph_not_ready`/`preparation_retry_required` / 422 `no_role_candidates` / 503 |
| POST | `/api/projects/{projectId}/environment/preparations/{id}/retry` | 失败或取消后的显式重试 | `202` | 401 / 404 `准备不存在` / 409 `preparation_not_retryable` / 422 `no_role_candidates` |
| GET | `/api/projects/{projectId}/environment/preparations/{id}` | 准备详情、分批预览、最终角色、来源、平台、话题、顺序、轮数与冻结选择 | `200` | 401 / 404 / 503 |
| PATCH | `/api/projects/{projectId}/environment/preparations/{id}/configuration` | 保存轮数，携带 `settingsRevision` | `200` | 401 / 404 / 409 `settings_conflict`/`preparation_frozen`/`preparation_not_editable` / 422 配置原因 / 503 |
| POST | `/api/projects/{projectId}/environment/preparations/{id}/confirm` | 携带 `{settingsRevision}` 确认冻结；同版本幂等 | `200` | 401 / 404 / 409 `preparation_not_ready`/`settings_conflict` / 503 |

所有接口先鉴权、再项目归属、再 build/准备归属；匿名立即 401 且不访问准备数据，他人项目与不存在项目统一 `404 {"detail":"项目不存在"}`，他人/不存在 build 与准备分别为 `图谱不存在`、`准备不存在`。错误响应只含中文说明、稳定原因码与可恢复动作。

### 离线与私有边界

默认 `make up`、默认 `make verify` 与 CI 不发起真实画像调用。需要离线演示时，在本地 `.env` 显式设置：

```bash
ENVIRONMENT_MODEL_PROVIDER=stub
ENVIRONMENT_ALLOW_STUB=true
```

画像调用次数在 provider 边界逐准备持久化；默认与 Change 08 验收的 `calls.real` 必须为 0，替身次数大于 0。作者的私有材料只在私有项目中人工走查，私有 PDF、含正文截图、真实画像与模型原始输出留在仓库外，不进入自动化夹具、日志、报告或 CI 产物。

## 引擎联通验收

本版本的引擎联通只做服务端接入与真实联通证据，不接入正式推演、角色准备或工作台页面。真实调用只由开发者显式执行：

```bash
make smoke-live
```

`make smoke-live` 默认在工作树存在未提交改动时拒绝执行；本地调试可显式使用 `ALLOW_DIRTY=1 make smoke-live`，报告会记录 `dirty=true`，但不能作为签收证据。该命令不会自动创建或复制 `.env`；`.env.example` 中的 `replace-with-*`、`*.example.invalid` 与全零 UUID 会按“未配置”处理，不会触发外部调用。

真实联调使用隔离的 `engine` 镜像与 `live` Compose profile；`make up`、默认 `make verify` 和 CI 都不会构建或启动它。引擎镜像只包含 `app/engines/`、显式配置与 `runtime/smoke-live` 报告目录，不挂载整仓、`.env`、私有材料或历史报告。

### 配置与真实调用

真实联调需要在本机 `.env` 填写以下变量（只列变量名，不要求提交真实值）：

- `MODEL_API_BASE_URL`、`MODEL_NAME`、`MODEL_API_KEY`：Chat Completions 兼容接口。根地址可包含 `/v1`，拼接后只出现一次 `/chat/completions`。
- `ZEP_API_KEY`、`ZEP_ACCEPTANCE_GRAPH_ID`：固定验收专用图谱，后者必须是 `graph_uuid`。

明显占位值（`replace-with-*`、`*.example.invalid`、全零 UUID）会被视为未配置，避免误触发外部调用。

业务建图配置只注入 `worker`：`EVIDENCE_GRAPH_MODEL_PROVIDER=auto` 与 `EVIDENCE_GRAPH_GRAPH_PROVIDER=auto` 只在模型/Zep 凭据完整时解析为真实 provider；占位凭据解析为 `unconfigured`。显式 `stub` 必须同时设置 `EVIDENCE_GRAPH_ALLOW_STUB=true`，否则在发起外部调用前拒绝。

`make up` 会先调用 `docker compose config` 取得 Compose 最终解析后的 `engine` 验收图谱 ID（shell 覆盖优先于 `.env`），再派生规范化 UUID 指纹 `ZEP_ACCEPTANCE_GRAPH_FINGERPRINT`，只通过命令环境传给业务 `worker`；不生成含凭据的额外配置文件。原始 `ZEP_ACCEPTANCE_GRAPH_ID` 仍只注入 `engine` 联调服务，业务 worker 不接收原始值；未配置验收图谱时指纹为空，默认业务图谱不受影响。

模型首次请求使用 `model`、`messages` 与 `response_format={"type":"json_object"}`，并固定发送 `User-Agent: EchoSim/0.1.0`；正常调用、移除参数重试与网络重试都使用同一 User-Agent。字段、类型、UTF-16 偏移、原文引用与运行标识由 EchoSim 校验。只有上游 400/422 明确指出不支持 `response_format` 时才移除参数重试一次；所有重试与 SDK 内置重试共用 30 秒、最多 2 次实际 HTTP 请求的预算。

使用 OpenCode Go 的订阅端点时，模型客户端以本次运行标识设置 `x-opencode-session`，同次重试复用该标识。该端点的 `deepseek-v4.1-flash` 结构化提取使用 `reasoning_effort=none`。请求按原文行给出 UTF-16 定位，响应仍逐条与原文核对。

模型项报告保留精确 `httpStatus` 与兼容字段 `httpStatusClass`；HTTP 402 归类为 `billing_required`，403 且明确 `error.code`/`error_code` 为 1010 时归类为 `edge_blocked`，401 与普通 403 保持 `auth_failed`。这些错误均只调用一次。`providerErrorCode` 只记录允许列表内的 `1010` 与 `insufficient_quota`；未知值或被回显的字符串留空。普通 403 只在无关元数据中出现 1010 时仍为 `auth_failed`。报告不保存密钥、请求头全集或原始响应正文。

图谱使用 `graph.episode.add` 写入固定 `graph_uuid`，每次运行生成不同 `runId` 与唯一 `idempotency_key`；同一次逻辑写入的重试复用原键、原图谱与原请求体。保存服务端返回的 episode UUID，再用 `graph.search_episodes` 核对 UUID、运行标识与唯一事实。写入响应丢失时，搜索未命中不能作为换键重写的依据；预算内仍无法确认则报告 `write_unconfirmed`。

OASIS 双平台使用稳定别名 `platform_a`/`platform_b`：前者对应原生 `twitter` 并执行 `create_post`；后者对应原生 `reddit`，先建立本次目标帖，再执行 `create_comment`。报告保留原生平台、原生动作、角色、记录标识与目标帖标识；两个平台分别判定，只有两个平台都有实际行动记录，双平台项才通过。

报告写入 `runtime/smoke-live/last-run.json`，并保留带时间戳的运行记录。每项标记 `passed`、`failed` 或 `not_run`，真实调用与测试替身分别计数；任一必需项未通过时命令以非零状态退出。缺模型配置只阻断模型项，缺 Zep 配置只阻断图谱项；本地 OASIS 动作继续执行，但总结果仍因必需项缺失而失败。

离线回归不读取真实密钥、不安装候选引擎依赖、不访问外部服务：

```bash
make verify CHANGE=2026-11-21-connect-engine-providers
```

该命令把离线契约结果写入 `runtime/verify/last-run.json` 的 `engine_evidence`，并记录 `mode=offline-contract`、`calls.real=0` 与测试替身次数。离线通过不能替代真实联调或人工签收。

### 依赖基线

候选组合为 `camel-oasis==0.2.6`、`camel-ai==0.2.90`、`zep-cloud==4.1.0`，目标为 Linux aarch64、Python 3.11。只有在目标容器中完成依赖解析、容器安装、导入与最小运行四阶段，`dependencyBaseline.status` 才为 `verified`。候选 `camel-ai` 的 Pydantic 上限低于应用当前版本，因此引擎依赖安装在隔离镜像中，不修改 `app`/`worker` 的依赖。

### 失败处理

- 缺配置：按依赖分别标记 `not_run`/`missing_config`，不发起对应调用；其它可独立运行的项继续执行。
- 失效凭据：返回 `auth_failed`，不重试；恢复凭据后可重新运行。
- 超时与限流：在各自总时限与最多 1 次重试预算内结束；SDK 内置重试也计入实际调用。
- 模型输出不合规：`invalid_output`、`source_mismatch` 或 `marker_missing`，HTTP 200 不算成功。
- 索引等待超时：保留已受理标识，图谱项以 `index_timeout` 失败。
- 单个平台失败：只影响该平台与双平台总判定，另一个平台的证据仍保留。

## 数据库迁移

迁移文件位于 `app/migrations/*.sql`，按文件名顺序执行，版本记录在 `schema_migrations`。容器启动时先执行 `python -m app.migrate` 再启动 Uvicorn，因此 `make up` 会自动升级已有数据卷。

手动应用：

```bash
make migrate
```

迁移只前向执行，不提供回滚脚本；本地需要彻底重建时执行 `make reset`（破坏性，会删除数据卷）。多进程并发启动通过 PostgreSQL advisory lock 串行化，当前为单副本部署。

## 验收

```bash
make verify
```

`make verify` 使用独立的 Compose 项目、回环端口与数据卷运行，不会停止或改写本地 8080 工作台。`VERIFY_COMPOSE_PROJECT` 只接受 `echosim-verify` 或其带安全后缀的隔离项目；启动前先校验项目名并获取独占锁，再清理本项目容器与数据卷。两个验收进程不能共用同一隔离项目，误指开发栈会在任何停止、删卷或重建命令前退出码 2 拒绝。Change 07/08 场景显式使用离线 stub provider，不读取真实模型/Zep 凭据。连续执行两次以上时可用 `VERIFY_REPORT_LABEL` 保留命名报告；报告仍绑定同一 `tested_sha` 且 `dirty=false`。离线通过不替代人工签收。

`make verify` 会构建并启动环境，依次验证：

- Change 01 保留场景：首页包含 `EchoSim`、`/health` 返回 200、停止数据库后 5 秒内返回 503 且 `/livez` 仍为 200、恢复后重新返回 200、后端容器身份（ID/PID/启动时间/重启次数）全程不变。
- 账户与隔离场景：幂等准备两个验收账号 A/B；A 登录并创建唯一命名项目、列表与详情可见；仅凭显式注入的 Cookie（服务端会话测试）可访问；停库期间无 Cookie 返回 401、有 Cookie 返回 503 且不清除 Cookie；恢复后原 Cookie 仍可用；A 退出后旧 Cookie 返回 401；B 的列表与历史不含 A 的项目；B 访问 A 的项目 ID 与随机 UUID 得到相同的 404；A 重新登录后仍能打开原项目。
- 控制台与工作台路由场景：`/`、`/projects` 与五条工作台阶段深链接均返回 200 且包含 `EchoSim`；`/api/projects` 仍返回 JSON 与既有状态码，没有被 SPA 回退吞掉。
- 查询参数回归：`web/tests/stage-links.test.mjs` 用 `node --test` 断言阶段链接显式保留 `layout` 与 `lang`，覆盖“报告 `graph/en` → 深度互动 → 后退回报告”的 URL 构造。
- 任务底座场景（默认执行）：开始返回 202 与 `jobId`/`runId`、确定性任务到 `succeeded` 且事件 1–10、匿名任务接口全部 401、B 的任务与事件与不存在项目同为 404、停库期间带 Cookie 返回 503 且不清 Cookie、只停 worker 时 `/health` 与 `/livez` 仍为 200 且 `app` 容器身份不变。
- 材料冒烟场景（默认执行，因为 CI 运行这个入口）：上传自制小 TXT，worker 解析到 `ready`，材料列表可见且至少一个分块，匿名读取列表返回 401；连续两次执行都要通过。
- 材料变更专属场景（`make verify CHANGE=2026-11-21-ingest-seed-documents`）：三页 PDF 的总页数 3、第 2 页零分块、第 3 页 `pageNo=3` 与 `#page=3` 来源链接、原件哈希与页对象结构；PDF 页眉/页脚/打印路径清理；文本换行与空白归一、合法路径保留、emoji 偏移与 GB18030 原件/原文；恰好 20 MiB 通过、20 MiB + 1 与空文件被拒且不新增材料、无文字层失败；页数上限边界；重复上传只留一套；匿名 401 与他人 404；worker 停止时取消排队任务；慢解析续租与中断接手。
- 任务变更专属场景（`make verify CHANGE=2026-11-21-track-background-jobs`）：真实 PostgreSQL 下两个任务互不影响、并发开始只新增一对 job/run、读到第 4 条后断开并以游标补到第 10 条（两段序号分别记录）、`Last-Event-ID` 优先、终态 `after=lastSeq` 立即结束、worker 中断后接手、旧租约令牌续租与写第 6 步被拒、运行中取消（3 步后 `seq=4`）、受控失败（`failAtStep=3` 持久化后仍生效）、终态后新任务、取消与最后一步竞态只留一个终态。
- 建图变更专属场景（`make verify CHANGE=2026-11-22-build-evidence-graph`）：原创材料上传与就绪、开始 202/重复 200、未就绪 409、六份/空问题 422、匿名 401、他人 404；真实 worker 六步与 `waiting_index → drawing → ready` 阶段、增量部分快照与最终快照、PDF 第 3 页与文本 UTF-16 来源；图谱绑定响应丢失/未提交/接手、失败原因码、超限不发布、发布/取消竞态、第二次建图隔离；结束后 worker provider 恢复，`graph_evidence.calls.real=0`、`calls.double>0`。
- 角色与环境专属场景（`make verify CHANGE=2026-11-24-prepare-agent-environment`）：原创 TXT 建图后开始准备 202/重复 200、五步事件 1–N、`ready` 与 72 轮；中文快照角色筛选与事件/待核实说法/平台三个反例；材料提及/背景/表达/关联/模拟设定分层、旧 `claimNature` 空值保留未知、文本 UTF-16 来源；默认 72→手动 40→3 刷新读回；A 用旧 revision 确认收到 `settings_conflict` 且冻结记录为 0，重读确认 3 轮并验证重复确认返回同一 `selectionId`；两批预览、停止 worker 后 `awaitingTakeover`、接手 `attempts>1` 且第一批复用、PDF 第 3 页来源；无角色 422 不建记录、模型不可用 failed、普通开始 409 与显式重试 202；匿名 401、他人/不存在资源 404、停库 503 保留 Cookie；换原创材料后角色变化且旧冻结选择仍绑定旧 `snapshotId`；报告 `environment_evidence.calls.real=0`、`calls.double>0`。

> HTTP 200 加 `EchoSim` HTML 只证明 SPA 入口可达。阶段内容、布局、语言、截图与浏览器后退行为以实际浏览器结果为准。

结果写入 `runtime/verify/last-run.json`，结束时恢复数据库运行。该命令可连续执行多次：账号用 `--update-password` 幂等准备，每次项目名唯一，不依赖上一次的残留状态。

验收规则：

- 工作树存在未忽略改动时默认拒绝执行。提交后再验收，或显式使用 `ALLOW_DIRTY=1 make verify`（报告会记录 `dirty: true`，不能作为干净克隆证据）。
- 指定期望 SHA 时校验失败即退出：

```bash
EXPECTED_SHA=$(git rev-parse HEAD) make verify
EXPECTED_SHA=$(git rev-parse HEAD) make verify CHANGE=2026-11-21-track-background-jobs
EXPECTED_SHA=$(git rev-parse HEAD) make verify CHANGE=2026-11-21-ingest-seed-documents
EXPECTED_SHA=$(git rev-parse HEAD) make verify CHANGE=2026-11-24-prepare-agent-environment
```

未注册的 `CHANGE` 会以退出码 2 结束，并在报告里写明原因。

- 机器可读报告字段包括 `result`、`tested_sha`、`dirty`、`started_at`/`finished_at`、逐场景 `scenarios[]`、`acceptance_accounts`、`isolation_project`、`app` 容器身份、`job_evidence` 与材料场景的 `document_evidence`。建图场景另写 `graph_evidence`：`mode`、`calls.real`/`calls.double`（从 provider 边界逐 build 持久化并汇总，不由计划数或快照大小推算）、`callScope`（覆盖的 buildId 与逐 build 实测值）、阶段序列、部分与最终快照版本/节点/边计数、来源页码与 UTF-16 偏移、绑定尝试与置位结果、失败原因码、发布/取消竞态、第二次 build 身份、日志/报告脱敏结果与 worker provider 恢复结果；不写材料正文、密钥或完整 provider 响应。
- CI 成功与失败运行都会上传 `verify-report-<sha>`；报告缺失会让工作流失败。可用手动触发执行受控失败演练：

```bash
gh workflow run verify --ref <branch> -f expected_sha=0000000000000000000000000000000000000000
```

  该运行会失败，并上传记录 SHA 不一致原因的失败报告，用于证明失败路径可观测。
- `scripts/verify.py` 对未预期异常也会写入 `verify-crash` 失败报告，保证 CI 有产物可下载。

CI 在干净检出上执行同一命令，并断言报告中的 `tested_sha` 等于触发提交 SHA；工作流名称 `verify`，报告作为构建产物上传。CI 同时提供独立的 Change 06 离线检查、Change 07 建图检查与 Change 08 角色准备检查，各自使用不同 artifact 名称；Change 08 检查额外断言 `environment_evidence.calls.real=0` 与 worker provider 已恢复。CI 不注入模型或 Zep 密钥、不执行 `make smoke-live`、不构建 live 引擎镜像。

### 浏览器人工走查

以下步骤由验收人在浏览器中完成，结果写入 `docs/acceptance/`，不写入机器报告。签收前只准备截图与待签收清单，不提前创建正式验收记录。

1. 在 `1280×720` 与 `1440×900` 打开 `/`，确认左右栏、五阶段、材料/问题与“开始”按钮可见可操作，保存两张原创截图。
2. 未登录访问 `/projects` 应跳转 `/login?next=/projects`；登录后回到 `/projects`。创建项目并打开详情，刷新后仍保持登录与同一项目。
3. 从项目详情进入 `/projects/<项目 ID>/workbench/report?layout=graph&lang=en`，再点击阶段导航进入深度互动：地址应为 `interaction` 且仍是 `graph/en`；刷新后仍在深度互动；浏览器后退一次应回到同一项目的英文图谱布局报告页。
4. 在同一非首页阶段，中英文各切换「图谱」「双栏」「工作台」，核对区域与当前阶段；返回控制台后再后退，应回到离开前的完整工作台地址。
5. 直接打开缺少材料或前置阶段的深链接，核对“还没有材料/缺少前置条件”和继续链接；再访问非法阶段，确认只有项目所有者看到“阶段不存在”，匿名先登录，他人项目显示相同“项目不存在”。
6. 停止数据库后打开工作台，确认显示“服务暂时不可用”且不清 Cookie；恢复数据库后点击重试回到外壳就绪。缺少前置条件与 503 的文案必须可区分。
7. 用 B 账号确认项目列表与历史均没有 A 的项目；访问 A 的项目详情与工作台，页面应与随机不存在 ID 一样显示“项目不存在”。
8. 保留 Change 02 回归：刷新保持登录、关闭重开浏览器需重新登录、退出后旧会话失效、重新登录仍能打开原项目。
9. 在工作台任一阶段的“测试任务”面板开始任务：日志应先出现 1–4，随后在浏览器 DevTools 中断网（或停掉网络），确认出现“连接已中断，正在从第 N 条重连”；恢复网络后从网络恢复时刻起 10 秒内补齐到 10 条且无重复（记录两段实际序号）。
10. 任务执行中 `docker compose stop worker`：确认面板在租约到期后出现“执行进程已中断，任务等待接手”，界面不显示失败或取消；`docker compose start worker` 后 10 秒内继续出事件并到终态，`attempts` 变大而日志仍为 1–10。
11. 分别触发受控失败（`failAtStep=3`）与运行中取消，确认面板分别显示“测试任务失败”和“已取消（第 N 步之后）”，终态来自持久化事件；切换阶段、布局与语言后开始/取消任务，确认地址中的项目、阶段、`layout`、`lang` 均未改变。
12. 材料入口：访客在控制台点击材料入口应先登录（`next=/projects`），登录后进入项目列表；已登录用户直接进入项目列表，控制台本身不出现项目名、材料列表或上传控件。
13. 上传自制三页 PDF（第 1、3 页有正文、第 2 页空白）：列表显示总页数 3；第 2 页没有摘录；从第 3 页摘录点击“查看原件”，浏览器 PDF 阅读器应停在第 3 页（记录实际落页）。
14. 上传含 emoji 与路径的 TXT：从摘录“查看原文”应高亮对应段落且不偏移；刷新或粘贴带 `document` 的完整地址回到同一材料；未登录打开该地址登录后回到原地址；用 B 打开应显示“项目不存在”；切换材料后 `layout` 与 `lang` 保留。
15. 上传恰好 20 MiB 的 TXT 应成功并最终就绪；20 MiB + 1、空文件、无文字层 PDF 与超过页数上限的 PDF 应分别给出可读原因；同一文件重复上传只出现一份材料。
16. 用项目作者的 Mac 浏览器上传 25 页私有样本：核对总页数，并从第 1、3 页摘录跳回原件确认实际落页；样本与其正文不得提交、不得出现在 `runtime/` 或 CI 产物中。

人工验收记录放在 [`docs/acceptance/`](docs/acceptance/README.md)，与被 CI 上传的 `runtime/verify/` 产物严格分离；Pi 验收技能见 [`.pi/skills/echosim-acceptance/SKILL.md`](.pi/skills/echosim-acceptance/SKILL.md)。

## 目录约定

```
app/                 FastAPI 后端（配置、数据库、健康检查、鉴权、迁移、静态托管）
app/migrations/      SQL 迁移（前向执行）
app/services/        账户、会话、项目、历史与任务服务（含统一归属校验、worker 执行层）
app/requirements-worker-graph.txt  worker 专用 Zep 客户端依赖
app/worker.py        worker 进程入口（无 HTTP 端口）
app/lease_probe.py   验收专用租约/竞态探针（仅 make verify 调用）
app/routers/         HTTP 路由
web/src/environment/ 第二阶段纯状态、来源链接与环境类型
web/                 Vue 3 + TypeScript + Vite 前端
tests/               Python 单元测试（unittest）
scripts/verify.py    验收脚本（Python 标准库）
scripts/smoke_live.py 真实联调的宿主预检与报告拒绝路径
app/engines/         引擎契约、模型/图谱/双平台适配器、离线检查与依赖基线
docs/acceptance/     人工验收记录与模板
.github/workflows/   CI 工作流
runtime/verify/      make verify 生成的报告（已忽略，不提交）
```

## 排障

**`make up` 报端口被占用**：确认 `127.0.0.1:8080` 未被其他进程使用。

```bash
ss -ltn 'sport = :8080'
```

释放占用或换端口（`ECHOSIM_PORT=8090 make up`）。

**改了 `POSTGRES_PASSWORD` 后数据库认证失败**：PostgreSQL 只在初始化空数据卷时应用密码，旧数据卷会保留旧凭据。确认可以丢弃本地数据后重建：

```bash
make reset
make up
```

`make reset` 等价于 `docker compose down -v`，会删除数据卷，属于破坏性操作。

**迁移失败导致 app 反复重启**：查看日志中的迁移错误类型，确认数据库已就绪、SQL 文件完整：

```bash
docker compose logs app
docker compose exec -T app python -m app.migrate
```

**登录返回 401**：确认邮箱已在容器内创建（`make user`），且密码与创建时一致；错误邮箱与错误密码的响应刻意相同。若刚执行过 `--update-password`，旧会话会失效，需要重新登录。

**任务一直停在排队/等待接手**：确认 worker 容器状态与日志；worker 停止或崩溃时任务不会被伪造为成功。

```bash
docker compose ps worker
docker compose logs worker --tail=50
```

**`/health` 返回 503**：查看数据库容器状态与日志。

```bash
docker compose ps
docker compose logs db
```

应用进程本身可用时 `/livez` 仍返回 200；503 表示数据库不可用而非进程死亡。此时未登录请求仍返回 401，已登录请求返回 503 且保留 Cookie，数据库恢复后无需重新登录。

**构建需要重新拉取依赖**：`make up` 会重新构建镜像，首次运行需要访问 npm 与 PyPI。

**单元测试**：

```bash
.venv/bin/python -m unittest discover -s tests
```

（先执行 `uv venv --python 3.11 .venv && uv pip install -r app/requirements-dev.txt`，或使用任意 Python 3.11 虚拟环境。）

## 工程规则

贡献与自动化约定见 [`AGENTS.md`](AGENTS.md)。

## 非目标

本阶段不实现自助注册、找回密码、邮箱验证、多因素认证、登录限流与账户锁定；也不实现双平台正式推演编排、报告生成、深度互动、生产部署、nginx 反向代理或多环境编排。角色与环境只做快照绑定、画像、轮数与冻结选择，不执行平台动作，也不提供人工编辑角色或跨项目复用。证据图谱的新 build 使用独立图谱以隔离旧 build 数据；不提供图算法、额外模型实体消歧、图谱编辑、跨项目共享或配额计费。引擎联通验收只证明一次真实调用与证据可核对，不构成产品推演能力。
