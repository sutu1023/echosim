# Spec Delta

## ADDED Requirements

### Requirement: 验收专用图谱只服务显式联调

`ZEP_ACCEPTANCE_GRAPH_ID` SHALL 只用于 `make smoke-live` 的固定验收图谱。业务建图 MUST 使用项目自己的图谱绑定，MUST NOT 读取、写入、复用或把验收专用图谱作为项目图谱；业务 worker 的进程环境 MUST NOT 包含 `ZEP_ACCEPTANCE_GRAPH_ID`。

业务项目图谱 MUST 通过 `graph.create` 的项目专用幂等键创建，并保存服务端返回的 `Graph.uuid_`；MUST NOT 把验收图谱标识当作项目图谱，也 MUST NOT 在业务建图中创建新的验收图谱。配置的验收图谱标识与项目绑定图谱相同时，业务建图 MUST 在发起任何 episode 写入前以 `invalid_graph_scope` 失败。

联调验收 MUST 继续使用该固定图谱与唯一运行标识，MUST NOT 把业务项目图谱写入联调报告或把联调 episode 写入项目图谱。

#### Scenario: 业务建图不触碰验收专用图谱

- **WHEN** 项目所有者发起业务建图并完成
- **THEN** 所有图谱创建、episode 写入与检索都发生在该项目自己的图谱
- **AND** 验收专用图谱的读取、写入与检索次数均为 0
- **AND** 业务 worker 环境中不存在 `ZEP_ACCEPTANCE_GRAPH_ID`

#### Scenario: 项目图谱由幂等创建得到

- **WHEN** 项目首次建图创建图谱
- **THEN** 使用项目专用 `idempotency_key` 与稳定 `name`/`description`/`time_zone` 调用 `graph.create`
- **AND** 保存服务端返回的 `Graph.uuid_` 作为项目绑定
- **AND** 不使用验收图谱标识作为创建结果

#### Scenario: 验收图谱被误配为项目图谱时拒绝

- **WHEN** 项目绑定图谱与 `ZEP_ACCEPTANCE_GRAPH_ID` 相同
- **THEN** 建图在写入 episode 前以 `invalid_graph_scope` 失败
- **AND** 不写入 episode，不生成节点、边或快照

#### Scenario: 联调仍只使用固定验收图谱

- **WHEN** 开发者显式执行 `make smoke-live`
- **THEN** 图谱项仍写入并检索配置的固定验收图谱
- **AND** 不读取或修改任何项目图谱，不要求业务建图先完成
- **AND** 联调报告与业务建图记录相互独立
