# EchoSim

材料驱动的双平台推演工作台。

当前版本提供可启动、可复现的工程骨架，并实现了本地账户登录、服务端会话、项目级隔离，以及公开控制台与按项目/阶段深链接的工作台外壳：每个用户只能看到自己的项目与历史。材料入库、后台任务、真实推演、报告与案例回放尚未实现。

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

工作台外壳与阶段数据分开：真实项目加载成功后，项目名、阶段高亮、工具条与三种布局可正常操作；阶段内容可以同时显示“还没有材料”或缺少前置步骤。当前版本没有阶段数据 API，产品页面不会展示示例节点、运行日志或报告。

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

worker 复用同一镜像但没有 HTTP 端口，因此 Compose 用“心跳文件 15 秒内更新”的探针**替换**镜像继承的 `/livez` 探针；`app` 的探针保持不变。worker 不对外发布端口。

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

`make verify` 会构建并启动环境，依次验证：

- Change 01 保留场景：首页包含 `EchoSim`、`/health` 返回 200、停止数据库后 5 秒内返回 503 且 `/livez` 仍为 200、恢复后重新返回 200、后端容器身份（ID/PID/启动时间/重启次数）全程不变。
- 账户与隔离场景：幂等准备两个验收账号 A/B；A 登录并创建唯一命名项目、列表与详情可见；仅凭显式注入的 Cookie（服务端会话测试）可访问；停库期间无 Cookie 返回 401、有 Cookie 返回 503 且不清除 Cookie；恢复后原 Cookie 仍可用；A 退出后旧 Cookie 返回 401；B 的列表与历史不含 A 的项目；B 访问 A 的项目 ID 与随机 UUID 得到相同的 404；A 重新登录后仍能打开原项目。
- 控制台与工作台路由场景：`/`、`/projects` 与五条工作台阶段深链接均返回 200 且包含 `EchoSim`；`/api/projects` 仍返回 JSON 与既有状态码，没有被 SPA 回退吞掉。
- 查询参数回归：`web/tests/stage-links.test.mjs` 用 `node --test` 断言阶段链接显式保留 `layout` 与 `lang`，覆盖“报告 `graph/en` → 深度互动 → 后退回报告”的 URL 构造。
- 任务底座场景（默认执行）：开始返回 202 与 `jobId`/`runId`、确定性任务到 `succeeded` 且事件 1–10、匿名任务接口全部 401、B 的任务与事件与不存在项目同为 404、停库期间带 Cookie 返回 503 且不清 Cookie、只停 worker 时 `/health` 与 `/livez` 仍为 200 且 `app` 容器身份不变。
- 任务变更专属场景（`make verify CHANGE=2026-11-21-track-background-jobs`）：真实 PostgreSQL 下两个任务互不影响、并发开始只新增一对 job/run、读到第 4 条后断开并以游标补到第 10 条（两段序号分别记录）、`Last-Event-ID` 优先、终态 `after=lastSeq` 立即结束、worker 中断后接手、旧租约令牌续租与写第 6 步被拒、运行中取消（3 步后 `seq=4`）、受控失败（`failAtStep=3` 持久化后仍生效）、终态后新任务、取消与最后一步竞态只留一个终态。

> HTTP 200 加 `EchoSim` HTML 只证明 SPA 入口可达。阶段内容、布局、语言、截图与浏览器后退行为以实际浏览器结果为准。

结果写入 `runtime/verify/last-run.json`，结束时恢复数据库运行。该命令可连续执行多次：账号用 `--update-password` 幂等准备，每次项目名唯一，不依赖上一次的残留状态。

验收规则：

- 工作树存在未忽略改动时默认拒绝执行。提交后再验收，或显式使用 `ALLOW_DIRTY=1 make verify`（报告会记录 `dirty: true`，不能作为干净克隆证据）。
- 指定期望 SHA 时校验失败即退出：

```bash
EXPECTED_SHA=$(git rev-parse HEAD) make verify
EXPECTED_SHA=$(git rev-parse HEAD) make verify CHANGE=2026-11-21-track-background-jobs
```

未注册的 `CHANGE` 会以退出码 2 结束，并在报告里写明原因。

- 机器可读报告字段包括 `result`、`tested_sha`、`dirty`、`started_at`/`finished_at`、逐场景 `scenarios[]`、`acceptance_accounts`、`isolation_project` 与 `app` 容器身份。
- CI 成功与失败运行都会上传 `verify-report-<sha>`；报告缺失会让工作流失败。可用手动触发执行受控失败演练：

```bash
gh workflow run verify --ref <branch> -f expected_sha=0000000000000000000000000000000000000000
```

  该运行会失败，并上传记录 SHA 不一致原因的失败报告，用于证明失败路径可观测。
- `scripts/verify.py` 对未预期异常也会写入 `verify-crash` 失败报告，保证 CI 有产物可下载。

CI 在干净检出上执行同一命令，并断言报告中的 `tested_sha` 等于触发提交 SHA；工作流名称 `verify`，报告作为构建产物上传。

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

人工验收记录放在 [`docs/acceptance/`](docs/acceptance/README.md)，与被 CI 上传的 `runtime/verify/` 产物严格分离；Pi 验收技能见 [`.pi/skills/echosim-acceptance/SKILL.md`](.pi/skills/echosim-acceptance/SKILL.md)。

## 目录约定

```
app/                 FastAPI 后端（配置、数据库、健康检查、鉴权、迁移、静态托管）
app/migrations/      SQL 迁移（前向执行）
app/services/        账户、会话、项目、历史与任务服务（含统一归属校验、worker 执行层）
app/worker.py        worker 进程入口（无 HTTP 端口）
app/lease_probe.py   验收专用租约/竞态探针（仅 make verify 调用）
app/routers/         HTTP 路由
web/                 Vue 3 + TypeScript + Vite 前端
tests/               Python 单元测试（unittest）
scripts/verify.py    验收脚本（Python 标准库）
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

本阶段不实现自助注册、找回密码、邮箱验证、多因素认证、登录限流与账户锁定；也不实现回放、上传、推演引擎、生产部署、nginx 反向代理或多环境编排。
