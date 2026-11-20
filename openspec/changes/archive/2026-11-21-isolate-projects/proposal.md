# Proposal

## Why

当前 EchoSim 只有可启动骨架与健康检查，任何访客都能看到同一份数据，无法区分“谁的项目”。在引入材料、运行与报告之前，必须先建立本地账户身份与服务端会话，并让项目归属成为后续所有业务资源的唯一隔离边界；否则后续能力都将在错误的数据可见性假设上生长。

## What Changes

- 新增本地账户登录与退出：邮箱 + 密码凭据，凭据仅通过管理命令创建，不开放自助注册。
- 使用服务端会话表 + HttpOnly Cookie 维持登录态；会话记录持久化在 PostgreSQL，重启浏览器或后端进程后只要 Cookie 未过期且会话未被注销就仍然有效。
- 新增项目能力：创建项目、项目列表、项目详情，以及跨项目的“历史”入口（按当前用户的可见范围聚合项目事件）。
- 项目归属校验下沉到服务层（单一 `resolve_owned_project` 入口），页面层、接口层与后续材料/运行/报告能力复用同一实现；未认证、非所有者、项目不存在三类结果有确定的对外响应契约。
- 新增数据库迁移机制与首个迁移（账户、会话、项目、项目历史四张表），以及创建/更新测试账号的管理命令。
- 扩展 `make verify`：用两个真实账号回放“登录 → 建项目 → 退出 → 旧会话 401 → 他人不可见 → 重新登录仍可见”的用户路径，保持报告与 Git SHA 绑定。
- 更新 `README.md`：新增登录方式、测试账号创建命令、会话 Cookie 语义与错误响应表。
- 目前无对外兼容负担，但接口与 Cookie 语义一旦发布即视为契约；**BREAKING** 无。

## Capabilities

### New Capabilities

- `local-account-session`: 本地账户登录、退出、服务端会话与 HttpOnly Cookie 的生命周期语义，包括凭据错误、会话失效、数据库不可达时的对外响应。
- `project-isolation`: 项目创建、列表、详情与历史入口的最小功能集，以及“所有者可见、其他用户与不存在等价”的归属校验契约。

### Modified Capabilities

- `reproducible-verification`: 验收场景从“首页与健康检查”扩展到包含账户隔离用户路径，并要求多账号准备可重复执行。

## Impact

- 后端：新增 `app/migrations/`、`app/manage.py`、`app/models.py`、`app/security.py`、`app/services/{accounts,sessions,projects,history}.py`、`app/routers/{auth,projects,history}.py`；`app/main.py` 注册新路由；`app/config.py` 增加会话与 Cookie 配置项。
- 数据库：PostgreSQL 新增 `users`、`sessions`、`projects`、`project_events` 四张表；现有数据卷通过迁移升级，`make verify` 需可重复执行。
- 前端：新增 `vue-router` 依赖与 `/login`、`/`、`/projects/:id`、`/history` 页面，统一 API 客户端处理 401 与 503。
- 工程：`Makefile` 增加迁移与管理命令入口，`docker-compose.yml` 与 `.env.example` 增加新配置项，`scripts/verify.py` 增加隔离场景，`tests/` 增加鉴权与归属测试。
- 文档：`README.md`、`AGENTS.md`（如新增配置项或验收场景）同步更新。
