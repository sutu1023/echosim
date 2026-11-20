# Spec Delta

## MODIFIED Requirements

### Requirement: 验收覆盖关键可观察场景

一次成功的验收 SHALL 实际验证启动、健康检查故障与恢复、后端进程不重启、账户隔离用户路径、停库期间的鉴权边界，以及公共控制台与工作台深链接可访问，而不是只做静态检查。布局、语言、截图与浏览器历史行为 MUST 由人工走查记录，不得写入自动化通过结论。

#### Scenario: 报告包含全部验收场景

- **WHEN** `make verify` 成功结束
- **THEN** 报告逐项记录并通过：首页 `/` 可访问且包含 EchoSim、`/health` 返回 200、停止数据库后返回 503、恢复数据库后返回 200、后端进程标识在故障前后不变、账户隔离用户路径通过、控制台与工作台深链接可访问、连续执行可重复
- **AND** 停止数据库到返回 503 的实测耗时被记录且不超过 5 秒

#### Scenario: 隔离用户路径逐步通过

- **WHEN** 验收使用两个真实账号 A 与 B 回放隔离路径
- **THEN** 报告至少包含以下可观察结果：A 登录成功、A 创建项目后刷新列表仍可见且详情返回 200、A 退出后旧会话访问私有接口返回 401、B 的列表与历史均不含 A 的项目、B 访问 A 的项目 ID 与访问不存在 ID 的状态码和响应体相同、A 重新登录后仍能打开原项目
- **AND** 每个结果作为独立场景记录在报告中

#### Scenario: 停库期间的鉴权边界被验证

- **WHEN** 数据库被停止且 A 的会话 Cookie 已捕获
- **THEN** 无 Cookie 的私有请求返回 401，携带 Cookie 的私有请求在 5 秒内返回 503 且响应不清除 Cookie
- **AND** 数据库恢复后同一 Cookie 的请求返回 200
- **AND** 上述结果各自记录为独立场景

#### Scenario: 控制台与工作台深链接可访问

- **WHEN** 验收已创建至少一个项目并读取其项目 ID
- **THEN** 对 `/`、`/projects` 以及 `/projects/<项目 ID>/workbench/evidence-graph`、`roles-environment`、`dual-platform-run`、`report`、`interaction` 的 HTTP GET 均返回 200 与包含 EchoSim 的 HTML 文档
- **AND** `/api/projects` 等 API 路径仍返回 JSON 或既有状态码，没有被 SPA 回退吞成 HTML
- **AND** 这些结果记录为独立的路由可访问性场景

#### Scenario: 自动验收与浏览器走查分离

- **WHEN** 自动化报告记录了控制台与工作台路由可访问
- **THEN** 报告不宣称已验证三种布局、中英文切换、截图或浏览器后退行为
- **AND** 这些结论只写入 `docs/acceptance/` 的人工走查记录，并与同一 Git SHA 对齐
