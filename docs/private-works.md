# 本地账号与作品

本轮交付私有作品创建、列表与详情。材料、故事准备、推演、章节尚未开放。正常会话固定七天，退出只结束当前会话；密码重置撤销该账号全部旧会话。

## 启动与迁移

`make configure` 仅首次创建本地环境，不覆盖已有 `.env`。已有配置添加 `PUBLIC_ORIGIN=http://localhost:8081`，端口改动时同步修改。`make up` 显式运行 Compose 的一次性 migrate 服务，然后启动 API；迁移失败不会放行业务进程。公网生产要求 HTTPS origin，Render 模板提供 preDeployCommand，但未部署验证。

独立执行迁移：`docker compose run --rm migrate`。每个编号 SQL 在事务与数据库锁下执行并登记版本；重复运行无副作用。缺 schema 时 API 启动拒绝。回退旧应用保留新增表，后续修复新增迁移，不删除作品或开发卷。

## 账号命令

- 创建：`docker compose exec api python -m echosim.manage create alice`
- 重置：`docker compose exec api python -m echosim.manage reset alice`
- 两条命令在终端隐藏读取密码；自动化可经受控标准输入传入，禁止放在 argv 或提交到仓库。
- 用户名 3–64 位 ASCII 字母、数字、下划线/短横线，转小写；密码 12–128 个 Unicode 码点，不去除空白。重复创建不会覆盖现有账号。

## 一键体验

1. `.env` 中设置 `DEV_QUICK_LOGIN_ENABLED=true`，环境保持 `APP_ENV=development`。
2. `docker compose up -d api` 应用开关；`docker compose exec api python -m echosim.manage demo` 幂等初始化 experience-a/b，不覆盖既有密码和作品。
3. 打开 `/login`，点击“体验账号 A”或“体验账号 B”；作品页提供“切换体验账号”。成功切换撤销当前会话，失败保留原会话。
4. 体验账号初始密码随机生成且不回显；如需验证密码登录，用 reset 命令设置。快捷登录不校验密码，重置只使旧会话失效，不关闭快捷入口。关闭开关并重建 API 后恢复仅密码登录。

production/test 环境不注册快捷接口，即使开关为 true。来源不匹配会拒绝写操作，访问时须使用 PUBLIC_ORIGIN 的主机和端口（localhost 与 127.0.0.1 不混用）。不信任转发头作为限速来源，目前使用连接对端地址；反向代理后的用户可能共享来源限额。

## API 与边界

- POST `/api/v1/sessions` 用户名密码登录；GET/DELETE `/api/v1/sessions/current` 读取身份/退出。
- GET `/api/v1/capabilities` 只提供快捷入口启用状态；POST `/api/v1/dev-sessions` 仅接受 A/B。
- POST `/api/v1/works`：JSON 仅 title；UUID `Idempotency-Key` 必填。同账号同键同规范化标题返回同一作品和 201，不同内容 409。幂等记录与作品同事务持久保存。
- GET `/api/v1/works`：limit 默认 20、1–100，cursor 为上一页返回的 next_cursor；按 created_at/id 倒序，items/next_cursor 响应。未知筛选、重复参数或无效游标均 422。
- GET `/api/v1/works/{id}`：只返回当前账号的作品；他人和不存在作品同 404，未登录 401。
- 所有写操作验证准确 Origin；已有会话写操作另需 `X-CSRF-Token`。登录成功或 current 响应取得 CSRF 值，不返回会话令牌。JSON 请求限 4096 字节。
- 密码错误每用户名 15 分钟 5 次、每连接来源 15 分钟 30 次；达到后返回 429/Retry-After。窗口状态保存在数据库，重启不清除。
- 标题首尾空白去除后 1–100 个 Unicode 码点，含 BOM 空白，前后端一致；允许重名。超时重试保留请求键；修改标题后“以新标题创建”是独立请求，旧请求可能已成功。

## 验证

`make check` 运行离线测试、构建、依赖方向和规约校验。`make smoke` 先运行 `scripts/private_integration.py` 的独立 PostgreSQL 合同测试，再进行隔离 Compose 启动/停库/恢复/非 root/退出回归；仅清理自己创建的数据库和卷。`make audit` 审计锁定依赖。

真实结果记录于 docs/verification/2026-11-13-private-works.md。公网、备份恢复和创作质量不属于此轮已验证能力。
