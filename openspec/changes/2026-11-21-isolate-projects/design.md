# Design

## Context

当前仓库只有 FastAPI 应用外壳、`/health`/`/livez` 路由、SQLAlchemy async engine 与同域静态托管（`app/static.py` 的 SPA 回退），没有任何数据表、迁移机制、路由层、认证或前端路由。`scripts/verify.py` 只用 Python 标准库，通过 Docker Compose 与 HTTP 请求做验收，并强制把报告绑定到 Git SHA。

约束来自 `AGENTS.md` 与既有规范：同域相对路径、仅回环绑定、`/health` 语义不可改变、`make verify` 必须可重复且 CI 与本机执行同一命令、错误响应不得泄露 DSN/凭据/堆栈。

需求见 `specs/local-account-session/spec.md`、`specs/project-isolation/spec.md` 与 `specs/reproducible-verification/spec.md`；动机见 `proposal.md`。

## Goals / Non-Goals

**Goals:**

- 用一张会话表 + 不透明 Cookie 令牌提供可撤销、可持久、可跨进程重启的登录态。
- 用单一服务层入口统一项目归属校验，并让“他人项目”和“不存在项目”在 HTTP 层不可区分。
- 用最小自研 SQL 迁移器引入数据库结构与幂等升级路径，不引入重量级迁移框架。
- 让新增用户路径可被 `make verify` 以真实 HTTP 回放，并继续绑定 SHA。

**Non-Goals:**

- 不实现自助注册、找回密码、邮箱验证、多因素认证、账户锁定或登录限流（后续变更单独引入）。
- 不实现生产级会话安全（HTTPS/`Secure`、反向代理、密钥轮换、分布式会话）。
- 不实现材料、上传、运行、报告等业务能力，只预留其必须复用的归属校验入口。
- 不做迁移回滚（down migration）与 ORM 自动生成；本地数据可直接 `make reset`。
- 不引入前端状态管理库、CSS 框架或端到端浏览器自动化框架。

## Decisions

### 1. 服务端不透明会话令牌，而非 JWT

登录成功时生成 `secrets.token_urlsafe(32)`，数据库只保存 `sha256(token)`；Cookie 携带原始令牌。每次请求按 `token_hash` 查会话并校验 `expires_at`。

- 理由：退出、过期与服务端撤销是本次核心语义，JWT 需要额外黑名单才能做到；不透明令牌实现更小，且数据库泄露不等于会话泄露。
- 备选：JWT（无状态、免查询）——放弃，因为无法“退出即失效”，且需要密钥配置。
- 备选：Starlette `SessionMiddleware`（签名 Cookie 存状态）——放弃，因为会话内容在客户端，退出无法真正撤销。

### 2. Cookie 属性与生命周期：持久 Cookie、绝对 7 天

Cookie 名 `echosim_session`，属性 `HttpOnly; SameSite=Lax; Path=/; Max-Age=<SESSION_TTL_SECONDS>`；本地 HTTP 下不设 `Secure`，由 `SESSION_COOKIE_SECURE` 显式开启。会话有效期从登录时刻起算，绝对 7 天，不做滑动续期。

**关闭并重开浏览器后的行为（显式约定）：**

- 普通窗口：持久 Cookie 在 `Max-Age` 内由浏览器磁盘保存，重开浏览器后仍自动携带，用户保持登录；直到 7 天到期、主动退出或服务端删除会话。
- 无痕/隐私窗口，或用户清除站点数据：浏览器关闭即丢弃 Cookie；重开后无 Cookie，私有接口返回 401，前端跳转登录页。服务端会话记录仍在，直到自然过期，不会因此产生新的访问能力。
- 同一浏览器再次登录会新建会话并删除本次请求携带的旧会话，避免会话固定。

- 理由：验收要求“刷新后仍能看到”并需要可重复验证；持久 Cookie 让“关闭重开”有确定、可说明的行为。
- 备选：会话 Cookie（关闭浏览器即失效）——放弃，行为对用户更差且与“重开仍可见”的验收叙事冲突。

### 3. 密码哈希使用标准库 scrypt

`hashlib.scrypt(password, salt=16B, n=2**14, r=8, p=1, dklen=32)`，存储格式 `scrypt$16384$8$1$<salt_b64>$<hash_b64>`；校验用 `hmac.compare_digest`。邮箱不存在时对一个固定 dummy hash 执行同样计算，减少时间侧信道。

- 理由：不新增运行时依赖即可获得内存困难型哈希；避免为本地骨架引入 `bcrypt`/`argon2` 二进制轮子的构建风险。
- 备选：`passlib`/`argon2-cffi`——留到需要更强参数与算法升级时再评估。
- 备选：明文或 SHA-256 加盐——明确拒绝。

### 4. 轻量 SQL 迁移器，而不是 Alembic

`app/migrations/NNNN_*.sql` 按文件名排序执行；`app/migrate.py` 在事务内取 `pg_advisory_xact_lock`、检查 `schema_migrations(version, applied_at)`、执行未应用文件并记录版本。容器启动命令改为先 `python -m app.migrate` 再 `exec uvicorn`，同时提供 `make migrate` 供手动执行。

- 理由：本次只有一份初始 schema，Alembic 的 async 配置与自动生成成本高于收益；纯 SQL 迁移可审查、可重复。
- 备选：Alembic——放弃；若后续 schema 变更频繁可再迁移过去，`schema_migrations` 可映射为基线版本。
- 备选：应用启动时 `create_all()`——拒绝，无法表达索引、约束演进，也无法审计已应用版本。

### 5. 数据模型

| 表 | 关键列 | 约束与索引 |
| --- | --- | --- |
| `users` | `id uuid pk`、`email text`、`display_name text null`、`password_hash text`、`created_at timestamptz` | `unique (email)`，邮箱写入前统一 `strip().lower()` |
| `sessions` | `id uuid pk`、`user_id uuid fk users on delete cascade`、`token_hash text`、`created_at`、`expires_at` | `unique (token_hash)`、`index (user_id)`、`index (expires_at)` |
| `projects` | `id uuid pk`、`owner_id uuid fk users on delete cascade`、`name varchar(80)`、`description varchar(500) null`、`created_at`、`updated_at` | `index (owner_id, created_at desc)` |
| `project_events` | `id uuid pk`、`project_id uuid fk projects on delete cascade`、`actor_user_id uuid fk users`、`type varchar(32)`、`created_at` | `index (project_id, created_at desc)`，本次只写入 `project.created` |

UUID 由数据库 `gen_random_uuid()` 生成（PostgreSQL 16 内置）。过期会话在鉴权失败时尽力删除，不做后台清理任务。

### 6. HTTP 契约

所有响应为 JSON；错误统一 `{"detail": "<中文文案>"}`。私有接口先鉴权、后归属解析、最后业务。

| 方法与路径 | 成功 | 失败 |
| --- | --- | --- |
| `POST /api/auth/login` | 200 `{"user":{id,email,display_name}}` + `Set-Cookie` | 401 `邮箱或密码不正确`；422 字段非法；503 `服务暂时不可用` |
| `GET /api/auth/session` | 200 `{"user":{...}}` | 401 `未登录`；503 |
| `POST /api/auth/logout` | 204（无会话也 204） | 503（保留 Cookie 以便重试） |
| `GET /api/projects` | 200 `{"projects":[...]}` | 401；503 |
| `POST /api/projects` | 201 `{"project":{...}}` | 401；403 来源非法；422；503 |
| `GET /api/projects/{project_id}` | 200 `{"project":{...}}` | 401；404 `项目不存在`；503 |
| `GET /api/history` | 200 `{"events":[{id,project_id,project_name,type,occurred_at}]}`，最多 50 条 | 401；503 |

输入校验：登录体邮箱 ≤254 且需基本邮箱形状、密码 1–128 字符，否则 422；项目名去除首尾空白后 1–80 字符、描述 ≤500 字符，否则 422 且不落库；`project_id` 按字符串接收后尝试 `UUID()`，解析失败按“不存在”处理，返回 404 而非 422。

### 7. 归属校验的单一入口

`app/services/projects.py` 暴露 `resolve_owned_project(db, actor, project_id) -> Project`，内部固定执行 `WHERE projects.id = :id AND owner_id = :actor_id`，对所有失败（无行、非法 ID）抛出同一个 `ProjectNotFound`。路由层只做异常到 404 的映射。

- 列表与历史在 SQL 查询层按 `owner_id` 过滤，不取回他人数据后再筛选。
- 后续材料、运行、报告服务必须调用该入口（或其扩展 `resolve_owned_child(parent_project, ...)`），不得自行拼 `owner_id` 条件；代码评审与测试以此为准。
- 审计：服务端日志记录 `request_id`、`actor_id`、`project_id` 与失败原因（`not_found` / `not_owner`），但对外响应完全一致，便于排障又不泄露存在性。

### 8. 鉴权依赖与错误映射

`app/services/sessions.py` 提供 `load_session(token)`：令牌缺失/格式非法/查无记录/已过期 → `Unauthenticated`；数据库异常 → `ServiceUnavailable`。路由依赖统一捕获并映射为 401/503；401 响应附带 `Set-Cookie` 清除失效 Cookie。数据库异常只记录异常类型与 `request_id`，不记录 DSN。

### 9. 会话型写请求的来源校验

新增 HTTP 中间件：方法非 `GET/HEAD/OPTIONS` 且路径以 `/api/` 开头且带 `Origin` 头时，要求 `Origin` 的 scheme+host+port 与请求 `Host` 一致，否则 403，且在任何业务代码之前返回。

- 理由：Cookie 鉴权天然面临 CSRF；`SameSite=Lax` 已拦截多数跨站表单，Origin 校验覆盖同站子域与旧浏览器差异。
- 备选：CSRF token——更强但需要额外的 token 下发、存储与前端配合，与本地单域部署不成比例。
- 不携带 `Origin` 的非浏览器客户端不受影响，但仍必须持有有效会话。

### 10. 前端：Vue Router + 轻量 API 客户端

引入 `vue-router@4`（精确版本），路由：`/login`、`/`（列表 + 创建表单）、`/projects/:id`、`/history`。全局前置守卫先调用 `GET /api/auth/session` 缓存结果；401 跳转 `/login?next=<path>`，登录成功后回到原路径。API 客户端统一处理：401 清状态并跳登录；403/422 展示后端 `detail`；503 显示“服务暂时不可用，请稍后重试”并保留当前页面。

现有 `app/static.py` 的 catch-all 已能把未知路径回退到 `index.html`，无需新增后端路由；`/api/*` 必须在静态路由之前注册，避免被回退吞掉。

### 11. 管理命令

`python -m app.manage create-user --email <email> (--password-stdin | --password <pw>) [--display-name <name>] [--update-password]`。

- 校验邮箱、密码（12–128 字符）；重复邮箱且未指定 `--update-password` 时退出码 1 并提示；指定后重设密码并使该用户已有会话失效。
- 密码只从参数或 stdin 读取，不回显、不写日志；输出仅包含邮箱与结果。
- `make user EMAIL=...` 包装为 `docker compose exec -T app python -m app.manage create-user --email ... --password-stdin`，便于本机与验收脚本调用；脚本使用 `--update-password` 保证幂等。

### 12. 验收脚本扩展

`scripts/verify.py` 保持标准库实现，新增 `CookieJar` + `urllib` 的会话客户端：

1. 通过 `docker compose exec -T app python -m app.manage create-user ... --update-password` 幂等准备 `accept-a@echosim.local`、`accept-b@echosim.local`。
2. 场景：A 登录 → 创建唯一命名项目 → 列表包含 → 详情 200 → 仅携带 Cookie 的新客户端仍 200（模拟重开浏览器）→ 退出 → 旧 Cookie 复放 401。
3. 场景：B 登录 → 列表与历史均不含 A 项目名 → B 请求 A 项目 ID 与随机 UUID，断言状态码与响应体一致。
4. 场景：A 重新登录 → 详情 200 且名称一致。
5. 数据库降级场景复用既有停库流程，额外断言登录与项目接口在停库时返回 503 且不挂起。

每个步骤写入 `scenarios[]`，`tested_sha`/`dirty` 逻辑不变；账号名带固定域后缀，项目名带时间戳/随机后缀以避免重复执行冲突。

### 13. 测试层次

- `unittest`（无外部依赖）：密码哈希与校验、令牌哈希、Cookie 属性、请求体校验、服务层 404/401 映射（用 `AsyncMock` 替换服务函数）、中间件 Origin 校验。
- 集成（真实 PostgreSQL）：由 `make verify` 覆盖登录/隔离/降级全链路；不额外引入 pytest 与测试容器。
- 需要数据库的单元测试通过 `TEST_DATABASE_URL` 才运行（`unittest.skipUnless`），默认跳过后端 CI 不受影响。

## Risks / Trade-offs

- [持久 Cookie 在共享电脑上延长暴露窗口] → 7 天绝对有效期、退出即撤销、README 明确说明；无痕窗口行为写清。
- [本地 HTTP 不设 `Secure`，Cookie 可被同网段嗅探] → 服务仅绑定 `127.0.0.1`；配置项 `SESSION_COOKIE_SECURE` 为将来 HTTPS 预留，README 标注差异。
- [SQL 迁移器无 down 与自动生成，schema 演进需手写] → 迁移文件必须可重复执行且带事务；本地回滚以 `make reset` 为准，并在 README 说明。
- [自研 scrypt 参数固定，未来需要算法升级] → 哈希格式自带参数前缀，可在登录成功时透明重哈希；参数写入设计文档。
- [“他人项目 = 404” 降低排障可见性] → 服务端按 `request_id` 记录 `not_found`/`not_owner` 审计日志，对外仍等价。
- [verify 运行时间增加、账号/项目残留] → 账号准备幂等、项目名唯一、场景失败也写报告；必要时把隔离场景拆成独立子命令但仍由同一 SHA 报告汇总。
- [会话表无限增长] → 鉴权时惰性删除过期行，`expires_at` 有索引；后续可加定时清理。
- [迁移在容器启动时执行，多副本并发升级风险] → 使用 `pg_advisory_xact_lock` 串行化；当前单副本部署。

## Migration Plan

1. 合并后 `make up`：容器启动先执行 `python -m app.migrate`，为已有数据卷创建四张表；数据库不可达时迁移失败、容器退出并由 compose 重启，`/livez` 与 `/health` 语义不变。
2. 首次需要账号时执行 `make user EMAIL=...`（stdin 输入密码）或文档中的 `docker compose exec` 命令。
3. 回滚：镜像回退到 `v0.0.1-skeleton` 即可恢复旧行为，新表保留但不再被访问；本地如需彻底清理执行 `make reset`（破坏性）。迁移本身前向执行，不提供 down。
4. 验收：提交后执行 `make verify`，再用 `docs/acceptance/` 记录人工登录/隔离/退出路径，报告与 CI 产物按 SHA 对齐。

## Open Questions

无。登录限流、账户锁定与密码重置属于后续变更，不影响本次规格与任务拆分。
