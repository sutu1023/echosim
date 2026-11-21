# Spec Delta

## ADDED Requirements

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
