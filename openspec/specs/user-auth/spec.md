# user-auth Specification

## Purpose

为个人创作者提供预置账号与持久会话，明确登录、退出、密码重置和开发体验入口的边界，使作品服务能够可靠识别当前创作者，并在身份失效时拒绝访问私有数据。

## Requirements

### Requirement: A1 Provisioned accounts
系统 SHALL 提供维护者本地创建账号和重置密码命令，无公开注册或找回密码页面；账号名唯一，密码不回显、不记录日志、不明文持久化。密码重置 SHALL 原子地使该账号全部已有会话失效。

#### Scenario: Create and authenticate
- **WHEN** 维护者创建合法账号后用户提交其用户名密码
- **THEN** 登录成功，重复创建同名账号被拒绝且不修改原密码

#### Scenario: Reset password
- **GIVEN** 同一账号有两个有效会话
- **WHEN** 维护者重置密码
- **THEN** 两个旧会话及旧密码均无法继续认证，新密码可登录，作品保留

### Requirement: A2 Persistent bounded sessions
系统 SHALL 允许多个浏览器独立登录，会话从签发时起固定有效七天，不因请求滑动延长。退出只撤销当前会话。服务重启 SHALL 保留未过期会话；无效、撤销或过期会话访问已实现私有 API 返回 401/UNAUTHENTICATED。

#### Scenario: Refresh and restart
- **WHEN** 登录后刷新页面或重启服务，并在七天内访问作品
- **THEN** 仍保持登录且可访问自己的作品

#### Scenario: Expiry boundary
- **WHEN** 当前时间达到签发时间加七天
- **THEN** 会话被拒绝为 401，页面引导重新登录

#### Scenario: Logout one browser
- **GIVEN** 两个浏览器登录同一账号
- **WHEN** 一个浏览器退出
- **THEN** 其会话无法复用，另一个仍有效；重复退出不报服务器错误

### Requirement: A3 Login failure protection
系统 SHALL 对错误用户名与错误密码返回相同的 401/AUTHENTICATION_FAILED 提示，不泄露账号存在性。连续失败 SHALL 限速并返回 429/RATE_LIMITED 与 Retry-After；限速对不存在账号同样生效，且不得因服务重启被绕过。

#### Scenario: Indistinguishable credentials
- **WHEN** 分别提交不存在用户名和存在用户名的错误密码
- **THEN** 状态码、错误码和用户提示一致，且不产生会话

#### Scenario: Rate limit and recovery
- **WHEN** 同一用户名或来源在限速窗口内达到失败阈值后继续尝试
- **THEN** 返回 429 和重试等待时间，窗口结束后允许重试；其他未受限账号及来源仍可登录

### Requirement: A4 Browser session protection
系统 SHALL 通过 HttpOnly、SameSite cookie 传递会话，在 production 使用 Secure；不得将会话存入 localStorage。写接口 SHALL 校验 CSRF，登录与快捷登录同样拒绝跨源请求。除受保护的会话 cookie 签发外，响应正文与日志 SHALL 不泄露密码、cookie、会话凭据或私有正文。

#### Scenario: Cross-site writes
- **WHEN** 外部站点发起登录、快捷登录、退出或创建作品请求
- **THEN** 请求被拒绝为 403/CSRF_REJECTED，不创建会话或作品，不改变已有会话

#### Scenario: Cookie and logs
- **WHEN** 正常登录、访问作品、退出及错误请求被记录
- **THEN** cookie 属性符合环境要求，日志不含注入的密码、令牌或作品标题标记

### Requirement: A5 Development quick login
系统 SHALL 仅在 development 且显式启用时提供体验账号 A/B 按钮和对应快捷登录 API，无需密码；它们使用正常会话与作品授权。成功切换 SHALL 撤销当前会话并签发目标账号会话；账号不存在时安全失败，不自动创建账号。

#### Scenario: Switch between accounts
- **GIVEN** 两个体验账号已预置，且快捷登录已开启
- **WHEN** 点击 A 创建作品后再切换 B
- **THEN** A 的当前会话被撤销，B 看不到 A 的作品；重新登录 A 可看到原作品

#### Scenario: Failed switch
- **WHEN** 切换目标账号缺失或数据库操作失败
- **THEN** 不签发新会话，不撤销现有会话，页面显示可重试错误

#### Scenario: Disabled environment
- **WHEN** 环境为 production/test，或 development 未开启开关，即使客户端直接请求快捷登录
- **THEN** 不显示按钮，快捷登录 API 返回 404，不能获得会话

### Requirement: A6 Login interface
系统 SHALL 提供中文登录页和退出入口，表单失焦与提交时校验，提交期间禁止重复触发；展示加载、错误与会话过期状态，支持键盘和可见焦点。未登录打开作品页面 SHALL 转到登录页，成功登录进入作品列表。

#### Scenario: Login form feedback
- **WHEN** 用户提交空字段、错误凭据或遇到网络失败
- **THEN** 显示适当错误且可以修正或重试，不误示登录成功

#### Scenario: Protected navigation
- **WHEN** 未登录用户直接打开作品详情页面，再成功登录
- **THEN** 先显示登录页，登录后进入自己的作品列表，不提前显示任何作品数据
