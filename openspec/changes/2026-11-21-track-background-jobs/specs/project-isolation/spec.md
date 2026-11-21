# Spec Delta

## ADDED Requirements

### Requirement: 任务与事件流复用统一归属边界

任务开始、列表、状态查询、取消、事件订阅与副作用查询 SHALL 全部先完成身份校验，再通过 `resolve_owned_project` 解析项目归属，最后才解析 `jobId` 与游标。接口层 MUST NOT 自行拼装项目过滤条件；`jobId` 查询 MUST 在“已解析项目”范围内进行，使属于其他项目的 `jobId` 与不存在 `jobId` 对外不可区分。

SSE 端点 MUST 在发送任何事件字节之前完成鉴权、归属与游标校验：失败时 MUST 返回与普通 JSON 接口相同的状态码与 JSON 错误体，MUST NOT 建立 `text/event-stream` 响应或泄露任何事件内容。

#### Scenario: 匿名订阅事件流返回 401

- **WHEN** 未携带会话 Cookie 的客户端请求某个已存在任务的 SSE 端点
- **THEN** 返回 401 与 `{"detail":"未登录"}`，`content-type` 为 JSON
- **AND** 响应中没有事件帧、没有 `id:` 行，也不连接数据库

#### Scenario: 他人项目的任务与事件不可见

- **WHEN** 用户 B 用自己的会话请求 A 的项目任务开始、查询、取消、事件订阅与副作用查询
- **THEN** 所有请求都返回与“项目不存在”相同的 404 状态码与 `{"detail":"项目不存在"}` 响应体
- **AND** 响应中不包含任何 `jobId`、`runId`、事件、步骤序号或“无权访问”提示
- **AND** B 以自己的项目地址携带 A 的 `jobId` 时返回 404 `{"detail":"任务不存在"}`，与随机 `jobId` 表现一致

#### Scenario: 数据库故障时保留会话

- **WHEN** 数据库不可用，携带有效 Cookie 的项目所有者请求任务状态或事件订阅
- **THEN** 在 5 秒内返回 503，且响应不清除会话 Cookie
- **AND** 数据库恢复后同一 Cookie 的同一请求按游标正常续传，不要求重新登录
- **AND** 未携带 Cookie 的同类请求仍返回 401 而不是 503

#### Scenario: 归属校验先于游标校验

- **WHEN** B 用非法游标订阅 A 的项目任务
- **THEN** 返回 404 `{"detail":"项目不存在"}` 而不是 400 游标错误
- **AND** 响应不透露项目或任务是否存在

#### Scenario: 属于自己项目的非法 jobId

- **WHEN** 项目所有者在自己项目下请求格式非法或不存在的 `jobId`
- **THEN** 返回 404 `{"detail":"任务不存在"}`
- **AND** 不返回 500，也不暴露 ID 解析细节
