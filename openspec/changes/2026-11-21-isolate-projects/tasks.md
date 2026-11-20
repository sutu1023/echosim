# Tasks

## 1. 数据库迁移与结构

- [x] 1.1 新增 `app/migrations/0001_identity_and_projects.sql`，创建 `users`、`sessions`、`projects`、`project_events` 四张表及设计文档中的唯一约束与索引（含 `email` 唯一、`sessions.token_hash` 唯一、`projects(owner_id, created_at desc)`、`project_events(project_id, created_at desc)`），用 `docker compose exec -T db psql -U echosim -d echosim -c '\d+'` 确认表、外键与索引齐全
- [x] 1.2 实现 `app/migrate.py`：按文件名排序执行未应用的 SQL，单迁移单事务，使用 `pg_advisory_xact_lock` 串行化，写入 `schema_migrations(version, applied_at)`，命令退出码 0/1 可判定；连续执行两次 `python -m app.migrate` 第二次不重复执行且退出码仍为 0
- [x] 1.3 编写迁移文件解析与版本选择的单元测试（临时目录 + 伪造执行器），覆盖“已应用跳过”“新文件按序执行”“SQL 失败回滚并不记录版本”，运行 `.venv/bin/python -m unittest tests.test_migrate -v` 通过
- [x] 1.4 更新 `Dockerfile` 启动命令为“先迁移再 `exec uvicorn`”，`Makefile` 新增 `migrate` 目标，并在 README 说明迁移前向且无 down 的策略；执行 `make up` 后 `docker compose exec -T app python -m app.migrate` 输出“无待应用迁移”，`/health` 仍返回 200

## 2. 账户管理命令与配置

- [x] 2.1 实现 `app/security.py` 的 scrypt 哈希与校验（参数写入哈希前缀、`hmac.compare_digest`、dummy hash 兜底），单元测试覆盖“同一密码两次哈希不同且均可校验”“错误密码失败”“篡改哈希前缀返回失败”，运行 `python3 -m unittest tests.test_security` 通过
- [x] 2.2 实现 `app/manage.py create-user`：邮箱规范化与格式/长度校验、密码 12–128 字符、重复邮箱默认失败、`--update-password` 更新密码并使该用户既有会话失效、支持 `--password-stdin` 且不回显密码；用真实数据库验证创建后可登录、重复创建退出码为 1、弱密码被拒绝且无新行
- [x] 2.3 新增配置项 `SESSION_TTL_SECONDS`（默认 604800，服务端会话绝对上限）与 `SESSION_COOKIE_SECURE`（默认 false），同步更新 `app/config.py`、`.env.example`、`docker-compose.yml` 与 README 配置表；`docker compose config` 无错误，`python3 -c "from app.config import settings; print(settings.session_ttl_seconds)"` 输出 604800
- [x] 2.4 新增 `make user EMAIL=...`（stdin 传密码，`docker compose exec -T app python -m app.manage create-user --email ... --password-stdin`），按 README 命令创建账号并用 `curl` 登录成功；确认命令输出与 `docker compose logs app` 均不含密码明文

## 3. 服务端鉴权与会话

- [x] 3.1 实现 `app/services/sessions.py`：令牌生成与 SHA-256 存储、登录建会话、按令牌加载并校验 7 天绝对有效期、退出删除当前会话、登录时删除请求携带的旧会话（防会话固定）、过期行惰性清理；单元测试用可注入的会话存储覆盖有效/过期/未知/已删除四类结果，运行测试通过
- [x] 3.2 实现会话 Cookie 读写：`echosim_session`、`HttpOnly`、`SameSite=Lax`、`Path=/`，**不写 `Max-Age` 与 `Expires`**，按配置决定 `Secure`；单元测试断言 `Set-Cookie` 属性齐全且不含 `Max-Age`/`Expires`、响应体不含令牌，运行测试通过
- [x] 3.3 实现 `app/routers/auth.py` 的登录、会话查询、退出接口与请求体校验（邮箱 ≤254、密码 1–128），登录 401 对“未知邮箱”和“错误密码”返回字节相同的响应体；用 TestClient 覆盖 200/401/204/422 与退出幂等，运行测试通过
- [x] 3.4 实现鉴权依赖的严格顺序与错误映射：无 Cookie → 立即 401 且不执行任何数据库查询；Cookie 非法/未知/过期 → 401 并清除 Cookie；Cookie 存在但会话查询失败 → 503 且不清理 Cookie；数据库异常只记录异常类型与 `request_id`。用 mock 断言“无 Cookie 时数据库调用次数为 0”“503 响应无 `Set-Cookie`”
- [x] 3.5 实现 `Origin` 来源校验中间件（非 GET/HEAD/OPTIONS 的 `/api/*` 请求，`Origin` 存在且与 `Host` 不同源时 403，且业务代码未执行），单元测试覆盖同源通过、跨源 403 且无写入、无 `Origin` 时继续鉴权
- [x] 3.6 在 `app/main.py` 注册鉴权路由与中间件，确认 `/api/*` 位于静态回退之前、`/health`/`/livez` 行为不变；运行既有 `tests.test_health`、`tests.test_static` 全部通过，`curl -i http://127.0.0.1:8080/api/projects` 返回 401 且不返回 HTML

## 4. 项目与历史的服务层和接口

- [x] 4.1 实现 `app/services/projects.py`：`create_project`（名称 `strip` 后 1–80、描述 ≤500、同事务写入 `project.created` 事件）、`list_owned_projects`（按 `owner_id` 过滤、`created_at desc`）、`resolve_owned_project`（单一入口，他人/不存在/非法 ID 统一抛 `ProjectNotFound`）；单元测试用伪造会话断言列表查询含所有者条件、三类失败抛同一异常
- [x] 4.2 实现 `app/services/history.py`：按当前用户自有项目连接查询事件、倒序、上限 50、返回项目名称；单元测试断言他人项目事件不出现在 SQL 条件允许的结果集，且匿名路径不可达
- [x] 4.3 实现 `app/routers/projects.py` 与 `app/routers/history.py`：创建 201、列表 200、详情 200/404、历史 200，非法 UUID 直接按 404 处理；TestClient + 服务层 mock 覆盖“匿名 401”“所有者 200”“他人 404”“不存在 404”“非法 ID 404”矩阵，并断言两个 404 响应体字节相同
- [x] 4.4 为创建接口补齐 422 校验与“不落库”断言：空名、纯空白名、81 字符名、501 字符描述、非字符串字段均 422 且项目与事件数量不变，运行测试通过
- [x] 4.5 在容器内用两个真实账号做 HTTP 验证（`curl`）：A 创建项目后 B 的列表与历史均不含该项目，B 访问 A 的 ID 与随机 UUID 的状态码与正文完全一致；浏览器层面的可见性由 7.3 的人工走查覆盖

## 5. 前端登录与项目页面

- [x] 5.1 安装并锁定 `vue-router@4` 精确版本，配置 `/login`、`/`、`/projects/:id`、`/history` 路由与 `history` 模式；`npm ci && npm run build && npm run typecheck` 全部通过，构建产物包含四条路由对应的入口脚本
- [x] 5.2 实现 `web/src/api.ts`：相对路径请求、`credentials: 'same-origin'`、JSON 解析、401 清状态并跳转 `/login?next=`、403/422 展示 `detail`、503 展示“服务暂时不可用，请稍后重试”；类型检查覆盖错误分支，`rg "localhost|http://|:8000" web/src` 无匹配
- [x] 5.3 实现登录页：邮箱/密码表单、`autocomplete` 属性、无效凭据提示“邮箱或密码不正确”、登录成功回到 `next` 或项目列表；`npm run typecheck` 通过，并用 `curl` 验证 401 与成功登录两条 API 路径
- [x] 5.4 实现项目列表页与创建表单：加载时请求会话与列表、创建成功后刷新列表、名称校验错误就地展示；`npm run build` 通过，API 层验证创建 201 与空名称 422，刷新页面后的列表表现由 7.3 人工走查覆盖
- [x] 5.5 实现项目详情页与历史页：详情展示名称/描述/创建时间并提供历史入口，404 展示与“不存在 ID”相同的“项目不存在”文案（不显示“无权访问”），历史页列出事件并可跳转详情；构建通过，API 层已验证他人/随机 ID 的 404 一致，页面表现由 7.3 人工走查覆盖
- [x] 5.6 实现未登录访问私有页面跳转登录、登录后回到原路径，以及退出按钮调用退出接口后回到登录页；构建通过，刷新仍登录、退出后回退不显示数据、关闭重开浏览器后跳转登录页由 7.3 的人工走查覆盖

## 6. 回归测试与验收脚本

- [x] 6.1 扩展 `scripts/verify.py`：用 `docker compose exec -T app python -m app.manage create-user ... --update-password` 幂等准备两个验收账号，失败时写入独立失败场景并以非零退出码结束；连续执行两次准备步骤均成功
- [x] 6.2 在 `verify.py` 中实现隔离路径场景并写入 `scenarios[]`：A 登录 → 创建唯一命名项目 → 列表包含 → 详情 200 → 停库前保存 Cookie → A 退出 → 旧 Cookie 私有接口 401 → A 重新登录后详情 200 → B 列表与历史不含该项目 → B 访问 A 项目 ID 与随机 UUID 状态码/响应体一致；同时实现“显式注入 Cookie 的全新客户端”服务端会话测试，并在场景名/报告中标注它不是浏览器重开测试
- [x] 6.3 在 `verify.py` 的停库窗口追加鉴权边界断言：无 Cookie 的项目列表返回 401 且耗时不因数据库不可用而增加；携带 A 的 Cookie 返回 503、5 秒内返回、响应不清除 Cookie；恢复后同一 Cookie 返回 200。保留 Change 01 的 503/恢复/进程身份不重启与 SHA 断言
- [x] 6.4 全量单元测试：`.venv/bin/python -m unittest discover -s tests -v` 全部通过，且需数据库的用例在未设置 `TEST_DATABASE_URL` 时显式 skip 并打印原因
- [ ] 6.5 提交后在干净工作树上连续执行两次 `make verify`，两次退出码均为 0、`runtime/verify/last-run.json` 的 `tested_sha` 等于 `HEAD`、`dirty` 为 `false`；第二次不受第一次账号与项目残留影响，两次报告都包含隔离路径、停库鉴权边界与 Change 01 全部场景

## 7. 文档、规格同步与人工走查准备

- [x] 7.1 更新 `README.md`：登录与退出说明、`make user` 用法、会话 Cookie 语义（无 `Max-Age`/`Expires`，刷新保持登录，关闭重开需重新登录，服务端 7 天上限）、401/403/404/422/503 响应表、新配置项与新接口；逐条复制命令执行确认无失效引用
- [x] 7.2 更新 `AGENTS.md`：补充鉴权与归属校验规则（无 Cookie 先 401、服务层单一入口、401/404 等价契约、Cookie 属性、迁移与管理命令入口），确认与 `openspec/specs/` 行为一致
- [x] 7.3 运行 `openspec validate 2026-11-21-isolate-projects --strict` 通过；在 README 或验收说明中给出浏览器走查步骤（刷新保持登录、退出后旧会话失效、关闭重开需重新登录、B 与不存在项目页面一致），但不在本变更中写入 `docs/acceptance/` 记录
- [x] 7.4 检查非目标未被顺手实现（无自助注册、无材料/运行接口、无 JWT、无前端状态库），用 `rg -i "register|signup|jwt|pinia" app web/src` 确认无意外产物
