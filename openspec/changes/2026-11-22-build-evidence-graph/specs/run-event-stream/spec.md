# Spec Delta

## ADDED Requirements

### Requirement: 建图事件与已发布快照版本同事务推进

业务建图 SHALL 复用现有 run 事件流。每个建图阶段事件 MUST 在写入建图业务状态与步骤推进的同一事务内提交，事件载荷 MUST 包含 `buildId`、阶段、`acceptedEpisodes`、`confirmedEpisodes`、`plannedEpisodes` 与可选的 `snapshotRevision`。事件 MUST NOT 携带整张图的节点或边；节点、边与来源只通过受鉴权与归属校验的已发布快照接口读取。

当一个事件引用新的快照版本时，该快照 MUST 已在该事件提交前发布，或与事件在同一发布事务内提交；浏览器收到事件后按 `buildId` 与 `snapshotRevision` 读取快照。第 6 步 `publish` 准备或校验的候选快照 MUST NOT 出现在事件或快照接口中；只有发布事务提交后，完成事件才能引用最终快照。重连或刷新时 MUST 先按游标补齐事件，再读取当前最新已发布快照；MUST NOT 根据事件载荷在浏览器本地重建整张图。

快照读取 MUST 在任何事件字节发送前完成鉴权、项目归属、build 归属与版本合法性校验。匿名请求返回 401；他人项目与不存在项目返回相同 404；非法或未来版本 MUST 返回 JSON 错误，不得建立事件流或返回部分快照。客户端 MUST 用 `buildId` 绑定当前展示；与当前 build 不匹配的迟到事件或快照响应 MUST 被丢弃。

#### Scenario: 进度事件后可按版本读取已发布快照

- **WHEN** 建图进入增量绘图并提交一条带 `snapshotRevision=2` 的进度事件
- **THEN** 客户端收到该事件后可按版本读取 revision 2 的节点、边与计数
- **AND** 事件载荷不包含整张图的节点或边
- **AND** 快照中的计数与画面来自同一版本

#### Scenario: 候选快照不出现在事件中

- **WHEN** 第 6 步已准备候选快照但发布事务尚未提交
- **THEN** 事件不引用该候选快照，快照接口也不能读取它
- **AND** 客户端仍只看到最近一次已发布快照

#### Scenario: 重连后事件补齐并读取最新已发布快照

- **WHEN** 客户端在收到 revision 2 后断线，随后用 `Last-Event-ID` 重连
- **THEN** 服务端补齐游标之后的已提交事件，包括终态事件（若已提交）
- **AND** 客户端读取当前最新已发布快照，而不是从事件载荷重建图或停留在旧版本
- **AND** 已显示的事件不重复，快照版本不回退

#### Scenario: 终态事件与最终已发布快照一致

- **WHEN** 建图完成并提交 `completed` 事件
- **THEN** 该事件与最终快照、`phase=ready`、`canContinue=true` 和任务 `succeeded` 在同一发布事务内提交
- **AND** 客户端收到终态后读取到的快照与事件引用的版本一致
- **AND** 不存在“任务已完成但最终快照尚不可读”的窗口

#### Scenario: 失败或取消不引用完成快照

- **WHEN** 建图提交 `failed` 或 `canceled` 终态事件
- **THEN** 事件不引用未发布的候选快照，`canContinue=false`
- **AND** 最近一次已发布的部分快照仍可按版本读取，但不得标记为本次完成

#### Scenario: 迟到的旧 build 响应被丢弃

- **WHEN** 同一项目第二次 build 已开始后，第一次 build 的快照响应才到达
- **THEN** 客户端因 `buildId` 不匹配丢弃该响应
- **AND** 当前画面、计数与 `phase` 仍属于第二次 build
- **AND** 不把第一次的完成图显示为第二次的结果

#### Scenario: 快照读取先校验再返回

- **WHEN** 匿名用户或他人项目请求建图快照，或请求一个不存在的版本
- **THEN** 匿名请求返回 401，他人项目返回 404 `项目不存在`
- **AND** 非法版本返回 JSON 错误，不返回节点、边、来源或部分事件流
