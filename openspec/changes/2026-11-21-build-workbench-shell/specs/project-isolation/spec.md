# Spec Delta

## MODIFIED Requirements

### Requirement: 页面入口与可见性

前端 SHALL 提供公开控制台（`/`）、登录页（`/login`）、私有项目列表页（`/projects`，含创建表单）、项目详情页（`/projects/:projectId`）、历史页（`/history`）与工作台阶段页（`/projects/:projectId/workbench/:stage`）。未登录访问任一私有页面 MUST 跳转登录页，并在 `next` 中保留完整原地址（含查询参数），登录成功后返回该地址；已登录访问登录页 MUST 跳转 `/projects`；登录成功但没有 `next` 时 MUST 进入 `/projects`；`/` MUST 对访客开放且不展示任何账户的项目列表。工作台阶段页 MUST 复用项目归属校验；他人项目、不存在项目与非法项目 ID 对外都显示相同的“项目不存在”（英文界面为对应的同一提示）。

#### Scenario: 访客打开控制台

- **WHEN** 未登录用户打开 `/`
- **THEN** 页面停留在公开控制台，不跳转登录页
- **AND** 页面不显示任何账户的项目列表或历史数据

#### Scenario: 未登录访问私有页面被重定向

- **WHEN** 未登录用户在地址栏直接访问 `/projects`、`/projects/<项目 ID>`、`/history` 或 `/projects/<项目 ID>/workbench/<阶段>`
- **THEN** 页面跳转到 `/login`，`next` 保留包含查询参数的完整原地址
- **AND** 登录成功后回到原路径，而不是固定回到控制台或项目列表

#### Scenario: 登录后返回原地址

- **WHEN** 未登录用户从 `/projects/<A 的项目 ID>/workbench/report?layout=graph&lang=en` 被重定向登录并完成登录
- **THEN** 页面回到该工作台阶段的完整地址
- **AND** 当前项目、阶段、布局与语言与重定向前一致

#### Scenario: 登录默认落点

- **WHEN** 用户在 `/login` 直接登录且没有 `next` 参数
- **THEN** 登录成功后进入 `/projects`
- **AND** 不再把 `/` 作为登录默认落点

#### Scenario: 已登录访问登录页

- **WHEN** 已登录用户在地址栏打开 `/login`
- **THEN** 页面跳转 `/projects`
- **AND** 不要求重新输入凭据

#### Scenario: 已登录浏览项目路径

- **WHEN** A 登录后打开 `/projects`、创建项目、打开 `/projects/<A 的项目 ID>`、打开 `/history` 或打开 `/projects/<A 的项目 ID>/workbench/evidence-graph`
- **THEN** 页面展示的数据全部来自 A 自有项目
- **AND** 直接刷新项目详情或工作台阶段地址后仍展示同一项目与阶段

#### Scenario: 详情与历史链接保持可达

- **WHEN** A 从 `/projects` 打开项目详情，或从 `/history` 打开某条历史记录
- **THEN** 列表与历史条目都导航到 `/projects/<A 的项目 ID>`
- **AND** 项目详情提供进入第一阶段工作台的链接，并把“返回项目列表”指向 `/projects`

#### Scenario: 关闭浏览器重开后需要重新登录

- **WHEN** A 登录后完全关闭浏览器并重新打开，然后访问 `/projects` 或 `/projects/<A 的项目 ID>`
- **THEN** 由于会话 Cookie 不持久化，浏览器不再携带 Cookie
- **AND** 前端跳转登录页，登录成功后才能继续访问原路径

#### Scenario: 他人项目与不存在项目的页面表现一致

- **WHEN** B 在地址栏输入 A 的项目详情地址或 A 的项目工作台地址，随后输入一个随机不存在的项目 ID
- **THEN** 页面展示与访问不存在 ID 相同的“项目不存在”提示
- **AND** 页面不展示 A 的项目名称、创建时间、阶段内容或“无权访问”提示
