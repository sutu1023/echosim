# health-readiness Specification

## Purpose

让 `GET /health` 成为可信的数据库就绪信号，使验收与运维能够区分“后端进程仍存活”和“数据库真实可用”，并覆盖故障发生与恢复的完整过程。

## Requirements

### Requirement: 真实数据库探测

`GET /health` SHALL 在每次请求内对配置的 PostgreSQL 执行一次 `SELECT 1`，并依据该查询的真实结果返回状态，不得使用缓存、启动时快照或固定返回值。

#### Scenario: 数据库可用时返回 200

- **WHEN** 数据库可用且请求 `GET /health`
- **THEN** 服务在该请求内执行 `SELECT 1`，成功后返回 200
- **AND** 响应为 JSON，包含 `status` 值为 `ok`、`database` 值为 `ok`，且不包含数据库连接串或凭据

#### Scenario: 每次请求都重新探测

- **WHEN** 连续多次请求 `GET /health`
- **THEN** 每次请求都重新执行数据库查询，数据库状态变化无需等待缓存过期即可反映在响应中

### Requirement: 数据库不可用时的快速降级

数据库停止或不可达时，`GET /health` SHALL 在 5 秒内返回 503，并明确标识数据库不可用，不得返回 200 或无限期挂起。

#### Scenario: 停止数据库后返回 503

- **WHEN** 数据库服务被停止，随后请求 `GET /health`
- **THEN** 服务在 5 秒内返回 503
- **AND** 响应为 JSON，包含 `status` 值为 `degraded`、`database` 值为 `unavailable`
- **AND** 响应不包含数据库连接串、凭据、堆栈或内部主机名

### Requirement: 恢复后无需重启即重新就绪

数据库恢复后，同一后端进程 SHALL 在下一次 `GET /health` 请求中重新返回 200，无需重启、重新部署或人工干预。

#### Scenario: 停库再恢复的完整过程

- **WHEN** 数据库先可用、随后被停止、再被恢复，全过程请求同一个后端进程
- **THEN** 状态依次为 200、503、200
- **AND** 该后端进程的标识（进程 PID 与启动时间）在整个过程中保持不变

### Requirement: 进程存活信号与就绪信号分离

系统 SHALL 提供一个不依赖数据库的进程存活信号，使数据库故障时容器不会被判定为进程死亡或反复重启，同时就绪信号仍如实反映数据库故障。

#### Scenario: 数据库故障不触发进程重启

- **WHEN** 数据库不可用而访问进程存活探针
- **THEN** 存活探针返回 200，后端进程未被重启
- **AND** 同时访问 `GET /health` 仍返回 503，就绪状态不被存活状态掩盖

### Requirement: 健康检查无需鉴权且无副作用

`GET /health` SHALL 允许未认证访问，仅执行只读探测，且不修改数据库或应用状态。

#### Scenario: 未认证读取健康状态

- **WHEN** 未携带任何凭据请求 `GET /health`
- **THEN** 请求被允许并按数据库状态返回 200 或 503
- **AND** 使用 `GET` 以外的方法请求该路径时返回 405
- **AND** 探测过程不写入数据库、不修改任何持久化状态

### Requirement: 健康检查请求可追踪

`GET /health` SHALL 为每次请求确定一个请求 ID：合法的 `X-Request-ID` 请求头 MUST 被复用（长度 1–64，仅允许字母、数字、`.`、`_`、`:`、`-`），缺失或非法时 MUST 生成新的随机 ID。该 ID MUST 通过响应头 `X-Request-ID` 返回，并在数据库探测失败的服务端日志中记录；响应体契约不得因此改变。

#### Scenario: 失败日志可按请求 ID 关联

- **WHEN** 客户端携带 `X-Request-ID: accept-123` 请求 `GET /health` 且数据库不可用
- **THEN** 响应头返回 `X-Request-ID: accept-123`
- **AND** 服务端失败日志包含 `request_id=accept-123`
- **AND** 响应体仍为 `{"status":"degraded","database":"unavailable"}`

#### Scenario: 非法请求 ID 被替换

- **WHEN** 客户端携带包含空白、非法字符或超长的 `X-Request-ID` 请求 `GET /health`
- **THEN** 服务端不复用该值，生成新的随机 ID 并在响应头返回
- **AND** 原始非法值不出现在响应头或日志中

### Requirement: 后台执行进程与存活/就绪语义分离

引入独立 worker 后，`GET /health` 与 `GET /livez` SHALL 继续只反映 `app` 进程与数据库状态：`/health` 仍只执行 `SELECT 1` 并据实返回 200/503，`/livez` 仍不访问数据库并在进程可响应时返回 200。worker 的停止、崩溃、反复重启或被单独停止 MUST NOT 改变这两个端点的状态码与响应体，MUST NOT 导致 `app` 容器重启，MUST NOT 使 `/health` 在数据库可用时变为 503。

worker 不可用的可观察结果 MUST 通过任务状态（`queued` 且 `attempts > 0` 或租约过期）体现，而不是通过健康检查体现。

#### Scenario: 只停止 worker 时健康语义不变

- **WHEN** 数据库与 `app` 正常运行，仅停止 `worker`
- **THEN** `GET /health` 仍返回 200 `{"status":"ok","database":"ok"}`
- **AND** `GET /livez` 仍返回 200，`app` 容器 ID、PID、启动时间与重启次数保持不变
- **AND** 进行中的任务显示为等待接手，而不是让 `/health` 降级

#### Scenario: 数据库故障时 worker 与 app 行为各自一致

- **WHEN** 数据库被停止，`app` 与 `worker` 同时无法访问数据库
- **THEN** `/health` 在 5 秒内返回 503，`/livez` 仍返回 200，`app` 未被重启
- **AND** 数据库恢复后同一 `app` 进程的下一次 `/health` 返回 200，worker 也能重新领取任务

#### Scenario: worker 重启不产生就绪抖动

- **WHEN** worker 因异常被 Compose 重启多次，而数据库始终可用
- **THEN** 期间任意时刻请求 `/health` 都返回 200
- **AND** 响应体不包含 worker 状态、内部主机名、DSN 或凭据
