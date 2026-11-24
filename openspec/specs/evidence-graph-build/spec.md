# evidence-graph-build Specification

## Purpose
让项目所有者用本项目已就绪材料和情景问题发起一次可观察、可接手、可复核的业务建图，并把结果保存为项目自己的图谱与同版本快照，而不是把模型调用塞进 HTTP 请求、复用验收专用图谱或把“写入已受理”误报为“图谱完成”。

## Requirements

### Requirement: 业务建图入口与输入边界

系统 SHALL 提供仅限项目所有者调用的业务建图开始接口，接收本项目已解析就绪的材料标识列表与一个情景问题。问题 MUST 去除首尾空白后长度在 1–500 字符之间；材料列表 MUST 非空、去重，且每份材料都属于当前项目并处于 `ready` 状态。首版单次建图材料数 MUST 不超过 5 份；超过上限 MUST 返回 422 与明确原因 `too_many_documents`，不得静默截断。

开始请求 MUST 只创建持久化建图任务与 run 并立即返回 HTTP 202，不得在请求内调用模型、Zep 或执行任何建图步骤。空问题、纯空白问题、空材料列表、非法材料标识、重复材料标识或超过问题长度上限 MUST 返回 422，且不得留下 job、run 或 build 记录。材料尚未就绪 MUST 返回 409 与稳定原因标识 `document_not_ready`，且不得开始建图。材料不存在或属于其他项目 MUST 返回与不存在材料相同的 404 `材料不存在`。未携带会话 Cookie MUST 在任何数据库查询前返回 401。

同一项目已有 `queued` 或 `running` 的建图任务时，再次开始 MUST 返回 HTTP 200 与既有 `buildId`、`jobId`、`runId`，并标记为重复开始；两个并发开始请求 MUST 得到一个 202 与一个 200，且只新增一对 job/run 与一个 build。终态后再次开始 MUST 产生新的 `buildId`、`jobId` 与 `runId`，并复用同一项目图谱。

#### Scenario: 所有者用就绪材料发起建图

- **WHEN** 项目所有者选择本项目两份已就绪材料、输入一个非空情景问题并提交开始
- **THEN** 响应状态码为 202，响应体包含 `buildId`、`jobId`、`runId`、`status=queued` 与所选材料标识
- **AND** 请求返回时尚未发生任何模型或 Zep 调用，建图步骤只由 worker 执行
- **AND** 随后查询 build 可看到状态从 `queued` 变为 `running` 或已进入终态

#### Scenario: 空问题不产生任务

- **WHEN** 项目所有者提交空字符串、纯空白或超过 500 字符的问题
- **THEN** 响应状态码为 422，并说明问题不能为空或超长
- **AND** 数据库不新增 build、job 或 run，且没有模型或 Zep 调用

#### Scenario: 材料未就绪时拒绝开始

- **WHEN** 项目所有者选择一份仍在 `queued`、`parsing` 或 `failed` 状态的材料并提交
- **THEN** 响应状态码为 409，包含 `document_not_ready` 原因标识与中文说明
- **AND** 不创建 build、job 或 run，不发起任何外部调用

#### Scenario: 超过材料数量上限时明确拒绝

- **WHEN** 项目所有者选择 6 份已就绪材料并提交
- **THEN** 响应状态码为 422，包含 `too_many_documents` 与“单次最多 5 份材料”的说明
- **AND** 不创建 build、job 或 run，不选择其中任意 5 份继续执行

#### Scenario: 跨项目材料与不存在材料不可区分

- **WHEN** 用户 B 把用户 A 项目的材料标识放进自己项目的建图请求
- **THEN** 响应状态码为 404，响应体为 `{"detail":"材料不存在"}`
- **AND** 与提交一个随机不存在的材料标识得到完全相同的状态码与响应体
- **AND** 不泄露该材料是否存在、名称、页数或所属项目

#### Scenario: 匿名请求立即拒绝

- **WHEN** 未携带会话 Cookie 的请求调用建图开始接口
- **THEN** 响应状态码为 401 `{"detail":"未登录"}`
- **AND** 即使数据库不可用也不查询数据库，且不创建任何记录

#### Scenario: 快速双击只产生一次建图

- **WHEN** 项目所有者对同一项目连续点击两次开始，或两个并发请求同时到达
- **THEN** 恰好一个请求返回 202，另一个返回 200，且两者指向同一 `buildId`、`jobId`、`runId`
- **AND** 持久化记录中该项目的活动建图任务恰好为 1 个，`builds`、`jobs`、`runs` 各只新增 1 行
- **AND** 不产生孤立 run、重复 episode 或第二个建图任务

#### Scenario: 终态后再次开始产生新 build

- **WHEN** 前一个建图任务已 `succeeded`、`failed` 或 `canceled`，项目所有者再次开始
- **THEN** 返回新的 `buildId`、`jobId`、`runId` 与新的步骤序号空间
- **AND** 复用同一项目图谱，旧 build 的状态、快照与来源保持不变

### Requirement: 项目图谱绑定使用 graph.create 幂等键

系统 SHALL 为每个项目持久化唯一图谱绑定。首次建图时，MUST 在调用外部服务前提交一条绑定记录，包含项目专用创建幂等键、稳定的 `name`、`description` 与 `time_zone` 请求参数；绑定记录 MUST 先于外部调用可见。调用 MUST 使用 `zep-cloud==4.1.0` 的 `graph.create(name=..., description=..., time_zone=..., idempotency_key=...)` 实际签名，MUST NOT 向 `graph.create` 传客户端指定的图谱 UUID 或通用 metadata 字段。成功后 MUST 保存服务端返回的 `Graph.uuid_` 到该绑定记录。

绑定记录已有 `graph_id` 时，后续 build MUST 直接使用该标识，MUST NOT 再次创建图谱。绑定记录存在但 `graph_id` 为空时，MUST 使用原幂等键、原 `name`、原 `description` 与原 `time_zone` 重试创建。创建响应丢失、响应已返回但数据库尚未提交、worker 在保存 `Graph.uuid_` 前崩溃时，接手者 MUST 复用同一绑定记录与同一幂等键，最终只绑定一个图谱。

`graph.create` 返回的 UUID 为空 MUST 以 `invalid_graph_response` 失败；返回的 UUID 与已保存的 `graph_id` 不一致 MUST 以 `invalid_graph_scope` 失败，MUST NOT 覆盖已保存绑定。业务 worker MUST 只通过当前项目的绑定记录取得图谱标识，MUST NOT 接受请求、查询参数或模型输出中的图谱标识。绑定记录中的图谱标识 MUST NOT 出现在面向浏览器的响应中。

业务 worker 的进程环境 MUST NOT 包含 `ZEP_ACCEPTANCE_GRAPH_ID`；绑定图谱标识与验收专用图谱标识相同时 MUST 在写入 episode 前以 `invalid_graph_scope` 失败。`ZEP_ACCEPTANCE_GRAPH_ID` MUST 只注入 `engine` 联调环境。

本期 MUST NOT 提供删除项目图谱、删除 build 或删除已发布快照的入口；每次新 build MUST 追加到同一项目图谱并生成独立快照，旧 build 的快照与来源保持可查询。

#### Scenario: 首次创建先持久化幂等键与稳定参数

- **WHEN** 项目第一次发起业务建图且尚无绑定记录
- **THEN** 系统先提交包含项目专用 UUID 幂等键、稳定 `name`、`description` 与 `time_zone` 的绑定记录
- **AND** 随后以完全相同参数和幂等键调用 `graph.create`，不传客户端图谱 UUID 或 metadata
- **AND** 成功后把返回的 `Graph.uuid_` 保存到绑定记录

#### Scenario: 创建响应丢失后只绑定一个图谱

- **WHEN** `graph.create` 已被服务端受理但客户端丢失响应
- **THEN** 接手者使用同一绑定记录、同一幂等键与同一组请求参数重试
- **AND** 服务端返回同一图谱，绑定记录最终只保存一个 `graph_id`
- **AND** 不生成第二个图谱，不生成第二个创建幂等键

#### Scenario: 拿到响应但数据库未提交时 worker 崩溃

- **WHEN** worker 已收到 `Graph.uuid_`，但在保存绑定前崩溃
- **THEN** 绑定记录仍为未绑定状态，接手者使用原幂等键与原请求参数重试创建
- **AND** 最终绑定记录只保存一个 `graph_id`，与首次响应返回的图谱一致
- **AND** 不产生第二个图谱或第二个项目绑定

#### Scenario: 已有绑定不重复创建

- **WHEN** 项目已有 `graph_id` 的绑定记录
- **THEN** 后续 build 直接使用该标识，不调用 `graph.create`
- **AND** 只在该项目图谱中写入新 build 的 episode
- **AND** 新 build 生成独立快照，本期不提供删除项目图谱或 build 的入口

#### Scenario: 创建结果异常时拒绝覆盖

- **WHEN** `graph.create` 返回空 UUID，或返回 UUID 与绑定记录已有 `graph_id` 不一致
- **THEN** 分别以 `invalid_graph_response` 或 `invalid_graph_scope` 失败
- **AND** 不覆盖已有绑定，不写入 episode，不生成节点、边或快照

#### Scenario: 只使用当前项目的绑定

- **WHEN** 建图任务加载图谱绑定时
- **THEN** 只接受 `project_id` 与当前 build 所属项目一致的绑定记录
- **AND** 不使用请求、模型输出或其它项目的图谱标识
- **AND** 绑定缺失或项目不匹配时在写入前失败，不回退到验收图谱或其它项目图谱

#### Scenario: 验收专用图谱不参与业务建图

- **WHEN** 业务 worker 执行建图任务
- **THEN** 其环境不包含 `ZEP_ACCEPTANCE_GRAPH_ID`，业务只使用当前项目绑定记录中的图谱
- **AND** 若绑定图谱与验收图谱相同则在写入前以 `invalid_graph_scope` 失败
- **AND** `make smoke-live` 仍只使用验收专用图谱，业务项目图谱不因联调被修改

### Requirement: 业务 worker 调用边界与依赖加载

系统 SHALL 只在独立 `worker` 进程内调用业务模型与 Zep；`app` 进程 MUST NOT 执行建图步骤、MUST NOT 接收模型或 Zep 密钥，也 MUST NOT 因建图请求发起外部网络调用。模型调用 MUST 使用服务端配置的 Chat Completions 兼容地址、模型名与密钥；Zep 客户端依赖 MUST 按需延迟加载，缺少客户端依赖时 MUST 在发起外部调用前以 `dependency_unavailable` 失败。

业务建图的模型输入 MUST 来自本次建图所选中的、本项目已就绪材料与用户问题；MUST NOT 使用仓库内固定夹具、验收专用材料或私有样本冒充业务输入。自动化验收与 CI MUST 使用运行时生成的原创夹具与测试替身，且该替身 MUST 只替换外部调用，不替换数据库、队列、租约、事件或快照。

建图 provider 未配置时 MUST 在发起任何外部调用前进入失败终态并记录 `missing_config`。测试替身 MUST 只能由显式测试配置启用；默认启动、默认 `make verify` 与 CI MUST 保持零真实外呼。

#### Scenario: 开始请求不触发外部调用

- **WHEN** 项目所有者提交建图开始请求并立即检查外部调用计数
- **THEN** 开始请求只写入 build、job 与 run，模型与 Zep 的真实调用次数均为 0
- **AND** 外部调用只在 worker 领取任务之后发生

#### Scenario: app 进程没有模型或 Zep 密钥

- **WHEN** 审阅 Compose 配置与进程环境
- **THEN** `app` 服务不包含 `MODEL_API_KEY`、`ZEP_API_KEY` 或 `ZEP_ACCEPTANCE_GRAPH_ID`
- **AND** 只有执行建图的 `worker` 接收业务模型与业务 Zep 配置，只有 `engine` 联调服务接收验收图谱标识

#### Scenario: 业务建图读取本次选中的项目材料

- **WHEN** 项目所有者选择两份本项目已就绪材料并输入问题
- **THEN** worker 的建图输入只包含这两份材料的分块、定位信息与用户问题
- **AND** 不读取仓库内固定夹具、`private-inputs/`、验收专用材料或其它项目材料

#### Scenario: 自动化使用原创夹具与替身

- **WHEN** Change 07 自动化验收或 CI 执行建图场景
- **THEN** 输入由运行时生成的原创小材料组成，外部模型与 Zep 由测试替身替代
- **AND** 数据库、队列、租约与事件仍使用真实实现
- **AND** 报告记录真实调用次数为 0

#### Scenario: 缺少 Zep 客户端依赖时不发起调用

- **WHEN** worker 环境没有安装 Zep 客户端依赖而任务被领取
- **THEN** 建图任务以 `dependency_unavailable` 失败，且真实外部调用次数为 0
- **AND** 不生成任何节点、边、快照或“完成”状态

#### Scenario: 默认配置下点击建图不产生真实外呼

- **WHEN** 使用 `.env.example` 的占位值启动默认环境并尝试建图
- **THEN** provider 解析为未配置，任务在外部调用前以 `missing_config` 失败
- **AND** 报告与日志记录真实调用次数为 0，不出现伪造的图谱结果

### Requirement: 建图复用队列、租约与六步原子发布

业务建图 MUST 以新的 run 类型 `evidence.graph.build` 进入现有 PostgreSQL 队列，由独立 worker 领取；同一项目同一建图操作的活动任务最多一个。领取、续租、旧令牌拒绝、中断接手与终态唯一性 MUST 复用现有任务语义：接手 MUST 保持同一 `jobId`、`runId` 与 `buildId`，递增 `attempts`，从第一个未提交步骤继续。

一次 build MUST 恰好包含六步：

| 步 | 步骤键 | 主要动作 | 外部调用 |
| --- | --- | --- | --- |
| 1 | `bind` | 校验输入并确保本项目图谱绑定 | 首次可能需要 `graph.create` |
| 2 | `plan` | 模型生成实体类型、关系类型与抽取批次 | 模型 |
| 3 | `extract` | 从本次材料分块抽取节点、关系与来源 | 模型 |
| 4 | `write` | 持久化 episode 内容与幂等键并写入 Zep | Zep `episode.add` |
| 5 | `readback` | 检索匹配、等待处理、分页读回节点/边 | Zep 检索/列表 |
| 6 | `publish` | 准备或校验候选快照，并在发布事务中原子发布 | 否 |

每个步骤 MUST 在单个事务内完成“锁定任务行 → 用数据库实际时钟验证租约 → 写入步骤副作用 → 写入建图业务状态 → 追加事件 → 推进步号”。`run_steps` 与 `jobs.next_step` MUST 继续是“做到第几步”的唯一权威来源。第 6 步可以准备或校验候选快照，但在发布事务提交前候选快照 MUST NOT 对外可见；只有第 6 步发布事务成功后，外部才能看到最终快照、`ready`、`canContinue`、完成事件与任务 `succeeded`。

#### Scenario: 建图任务按六步完成

- **WHEN** worker 领取一个建图任务并正常执行
- **THEN** `run_steps` 依次记录 `bind`、`plan`、`extract`、`write`、`readback`、`publish` 六步
- **AND** 任务状态序列为 `queued` → `running` → `succeeded`
- **AND** 完成事件、最终快照、`ready`、`canContinue` 与 `succeeded` 在同一发布事务内提交

#### Scenario: 重复点击不新增第二个建图任务

- **WHEN** 建图任务仍在 `queued` 或 `running` 时再次提交相同项目建图
- **THEN** 返回既有 `buildId`、`jobId`、`runId`，不新增 job、run、build 或 episode
- **AND** 现有任务的步骤、租约与事件不受影响

#### Scenario: worker 中断后接手不更换身份

- **WHEN** 建图任务已提交若干步骤后唯一 worker 被停止，租约到期后 worker 恢复
- **THEN** 任务仍为 `running` 并显示 `awaitingTakeover=true`，`attempts` 在接手后大于 1
- **AND** 接手使用同一 `jobId`、`runId`、`buildId`，从下一个未提交步骤继续
- **AND** 已提交步骤、已确认 episode 与已发布快照不被重写或重复追加

#### Scenario: 旧租约不能写入建图状态

- **WHEN** 旧 worker 的租约已过期而新 worker 已接手并推进步骤
- **THEN** 旧令牌的续租与建图写入影响 0 行，不产生新快照、节点、边或事件
- **AND** 任务状态、步骤号、快照版本与 episode 计数保持接手者的结果

#### Scenario: 发布前崩溃不暴露候选快照

- **WHEN** worker 在第 6 步准备或校验候选快照后、发布事务提交前崩溃
- **THEN** 外部接口仍只看到最近一次已发布快照，候选快照不可见
- **AND** `phase` 不是 `ready`，`canContinue=false`
- **AND** 接手者可以重新校验候选快照并完成发布

#### Scenario: 建图任务不进入测试任务面板

- **WHEN** 同一项目同时存在材料解析任务、测试任务与建图任务
- **THEN** 测试任务列表只返回 `test.simulation` 任务，不出现建图任务的 `jobId`、问题或节点数
- **AND** 建图进度只在证据图谱面板与建图接口中显示

### Requirement: 写入受理、episode 匹配、处理完成与节点和边读回

系统 SHALL 分别记录并区分以下证据：

- 写入受理：`graph.episode.add` 返回非空 `AddEpisodeResult.episode.uuid_`，该 UUID 已持久化到本次 build 的 episode 记录。
- episode 匹配：`graph.search_episodes` 返回的 episode UUID、本次 `buildId` 标记与持久化的内容哈希同时一致。写入响应丢失、起初没有 episode UUID 时，MUST 用本次 `buildId` 标记与内容哈希在检索结果中恢复 UUID，MUST NOT 生成新键重写。
- 处理完成：本次 build 的全部计划 episode 都满足匹配，且其 `Episode.processed` 为 true；MUST NOT 仅凭“可搜索”判定处理完成。
- 节点和边读回：使用本次 build 已匹配、已处理 episode 的 UUID 集合作为过滤条件，调用 `graph.node.list` 与 `graph.edge.list` 分页读取实际节点与边；每页 MUST 按游标继续直到没有下一页，结果 MUST 按 `uuid_` 去重。读回集合 MUST 在两次相邻读取中保持稳定，且至少包含一个节点与一条边。

读回的节点与边 MUST 只来自本次 build 的 episode 集合；同一图谱中旧 build 的节点、边与 episode MUST 被排除。每条展示的材料关系 MUST 能关联到本次抽取清单中的关系与至少一条通过校验的材料来源；无法唯一映射的边 MUST NOT 被展示为材料关系。节点/边数量 MUST 来自同一份读回集合，不得超过 200 个节点、400 条边或每项 5 条来源。

#### Scenario: 写入受理后保存 episode UUID

- **WHEN** `graph.episode.add` 返回 `episode.uuid_`
- **THEN** 该 UUID 与本次 episode 标识、幂等键和内容哈希一起持久化
- **AND** build 记录至少一个 `acceptedEpisodes`，但不因此进入完成状态

#### Scenario: episode 可搜索但还没有可用边

- **WHEN** 本次 episode 已被检索匹配并 `processed=true`，但按 episode 集合读回的边为空
- **THEN** build 保持等待/增量状态，不发布最终快照，不标记 `ready`
- **AND** 在配置的处理预算内继续读回；预算耗尽后以 `processing_timeout` 或 `graph_incomplete` 失败

#### Scenario: 同批返回旧记录与正确记录

- **WHEN** 检索同时返回旧 build 记录与本次 build 的正确记录
- **THEN** 只有 UUID、`buildId` 标记与内容哈希都匹配的正确记录进入已匹配集合
- **AND** 旧记录不进入节点/边过滤集合，不计入本次节点或边数量

#### Scenario: 写入响应丢失且起初没有 episode UUID

- **WHEN** 写入超时或连接中断，客户端没有拿到 episode UUID
- **THEN** 系统保持原幂等键与原内容，用本次 `buildId` 标记与内容哈希检索
- **AND** 检索命中后恢复并保存该 episode UUID
- **AND** 不生成第二个幂等键或第二个逻辑 episode

#### Scenario: 分页读回全部节点和边

- **WHEN** 本次 build 的节点或边数量超过一页
- **THEN** 系统按 `graph.node.list`/`graph.edge.list` 的游标继续读取直到没有下一页
- **AND** 用 `uuid_` 去重后计算节点数与边数
- **AND** 展示的节点、边、计数与详情来自同一份读回集合

#### Scenario: 读回集合不稳定时继续等待

- **WHEN** 两次相邻读回得到的节点或边 UUID 集合不同
- **THEN** build 不发布最终快照
- **AND** 在处理预算内继续读回；预算耗尽后以 `processing_timeout` 失败

### Requirement: 等待索引、增量绘图与完成三态

系统 SHALL 以服务端持久化证据判定建图页面状态，不得由前端计时器、动画或本地推断决定。状态 MUST 至少包含等待索引、增量绘图与完成：

- 等待索引：至少一个 episode 的写入已被服务端受理，但尚无任何本次 build 的检索匹配；页面 MUST 显示等待索引，不得把空图显示为完成。
- 增量绘图：至少一个本次 build 的 episode 已被检索匹配，但并非全部计划 episode 都已匹配、处理完成并读回可用节点/边；页面 MUST 保留上一份已发布快照并展示新的部分快照。
- 完成：全部计划 episode 已匹配并处理完成，节点与边读回稳定，最终快照已在发布事务中发布，任务为 `succeeded`，build 为 `ready`，且最终快照至少包含一个材料来源节点与一条边。

每次读取图数据 MUST 返回同一份已发布快照的 `snapshotId`、`revision`、`complete`、`nodeCount`、`edgeCount`、节点与边。节点数与边数 MUST 等于该快照内的节点与边数量；页面 MUST NOT 把不同快照的计数与画面混用。刷新页面 MUST 读取当前 build 最近一次已发布快照；任务失败或取消后 MUST 保留当前 build 最近一次已发布的部分快照并显示失败/取消原因，不得显示为完成。

#### Scenario: 写入已受理但尚未检索到时保持等待

- **WHEN** 建图 episode 已被 Zep 受理，但本次 build 尚无检索匹配
- **THEN** build 状态为等待索引，接口返回 `acceptedEpisodes > 0` 与 `confirmedEpisodes = 0`
- **AND** 页面显示“等待索引”，不显示空图、节点数 0 的完成状态或“已完成”
- **AND** 在检索匹配前不进入完成状态

#### Scenario: 第一个检索匹配后进入增量绘图

- **WHEN** 本次 build 的第一个 episode 被检索匹配，但仍有计划 episode 未匹配或未处理完成
- **THEN** 接口返回 `phase=drawing` 与一份 `complete=false` 的部分快照
- **AND** 页面保留上一份已发布画面并展示该快照的节点、边与计数
- **AND** 不把部分快照标记为完成

#### Scenario: episode 匹配但无可用边时仍不完成

- **WHEN** episode 已匹配且 `processed=true`，但读回边为空或无法映射到材料来源
- **THEN** 页面保持增量绘图或等待状态，并显示“正在读取节点与边”或等价说明
- **AND** 不显示“已完成”，不发布最终快照

#### Scenario: 全部匹配、处理并读回后完成

- **WHEN** 全部计划 episode 都已匹配并处理完成，节点/边读回稳定，发布事务成功
- **THEN** build 状态为 `ready`，接口返回 `complete=true` 的最终快照
- **AND** `nodeCount` 与 `edgeCount` 等于最终快照内节点与边数量
- **AND** 最终快照、完成事件与任务 `succeeded` 来自同一发布事务

#### Scenario: 刷新后找回上次已发布快照

- **WHEN** 用户在增量绘图或完成后刷新页面
- **THEN** 页面重新读取最近一次已发布快照，并显示同一 `snapshotId`、`revision`、节点数、边数与画面
- **AND** 不依赖浏览器内存、动画状态或上一次请求的局部数据

#### Scenario: 失败后保留最近已发布快照

- **WHEN** 建图在增量绘图后因外部依赖不可达而失败
- **THEN** build 进入 `failed`，页面同时显示失败原因与当前 build 最近一次已发布的部分快照
- **AND** 不把该快照标记为完成，不允许进入下一阶段

### Requirement: 幂等写入、响应丢失与重复事实防护

系统 MUST 在发起每次 episode 写入前持久化该 episode 的内容与唯一幂等键，并在同一次逻辑写入的所有重试、worker 接手与索引等待中复用同一键、同一内容和同一项目图谱。搜索未匹配 MUST NOT 作为换键、改写内容或重复写入的依据。写入响应丢失时 MUST 先检索确认；预算内无法确认 MUST 判定为 `write_unconfirmed`，不得把未确认内容发布为完成快照。

同一项目图谱允许历史 build 的 episode 共存；检索 MUST 用本次 `buildId` 标记、episode 标识与内容哈希精确过滤，只接受三者同时匹配的命中。命中属于其他 build、其他项目或内容不匹配时 MUST 以 `stale_or_foreign_hit` 失败，不得用旧记录冒充本次结果。

#### Scenario: 写入响应丢失但服务端已受理

- **WHEN** episode 写入超时或连接中断，但服务端实际已受理
- **THEN** 系统保持原幂等键与原内容，通过检索确认该 episode
- **AND** 最终只存在一条本次逻辑 episode，不产生第二个 episode 或第二个幂等键

#### Scenario: 首次搜索未匹配不触发换键重写

- **WHEN** 写入响应丢失后第一次检索尚未返回本次 episode
- **THEN** 状态保持等待索引，继续用同一 `buildId` 搜索
- **AND** 不生成新幂等键、不改写 episode 内容、不重复写入

#### Scenario: 预算耗尽仍无法确认

- **WHEN** 写入响应丢失且确认预算耗尽仍未匹配
- **THEN** build 以 `write_unconfirmed` 失败，页面保留最近已发布快照并显示原因
- **AND** 不发布包含未确认 episode 的完成快照，不允许进入下一阶段

#### Scenario: worker 接手复用原幂等键

- **WHEN** worker 在写入已受理后中断，接手 worker 重新进入检索/读回
- **THEN** 接手者读取已持久化的 episode 与幂等键，不重新生成键或内容
- **AND** 最终 episode 数量、节点数、边数与未中断执行一致

#### Scenario: 历史 build 的 episode 不冒充本次结果

- **WHEN** 同一项目图谱中存在以前 build 的 episode，本次检索同时返回旧记录
- **THEN** 旧记录因 build 标识不匹配被忽略
- **AND** 只有本次 build 的精确命中进入快照；只返回旧记录时继续等待或按预算失败

#### Scenario: 错误 UUID 或内容不匹配时失败

- **WHEN** 检索返回携带本次 build 标识但 episode UUID 或内容哈希不匹配的记录
- **THEN** build 以 `stale_or_foreign_hit` 失败
- **AND** 不把该记录写入快照，不标记完成

### Requirement: 失败分类区分索引超时、处理超时与写入未知

系统 MUST 区分以下失败：

- `index_timeout`：写入已受理且 episode UUID 已知，但在索引预算内未被检索匹配。
- `processing_timeout`：episode 已匹配，但在处理预算内未达到 `processed=true`、节点/边读回不稳定或没有可用边。
- `graph_incomplete`：episode 已处理，但读回结果无法组成至少一个材料来源节点与一条材料关系。
- `write_unconfirmed`：写入响应丢失、起初没有 episode UUID，且在确认预算内无法通过检索恢复 UUID。
- `auth_failed`、`graph_unavailable`、`model_unavailable`、`invalid_model_output`、`source_mismatch`、`ambiguous_source`、`graph_too_large`、`insufficient_graph`：分别表示认证、网络、模型、输出、来源歧义、规模与证据不足。

失败 MUST NOT 生成伪造节点、边或完成快照；当前 build 最近一次已发布的部分快照 MUST 保留，`canContinue` MUST 为 false。认证失败、明确无效请求与内容校验失败 MUST NOT 无限重试；限流与可重试网络错误 MUST 只在配置预算内重试。

#### Scenario: 已受理后的索引超时

- **WHEN** episode UUID 已知，但索引预算内没有检索匹配
- **THEN** build 以 `index_timeout` 失败
- **AND** 报告保留已受理事实、已等待时间与检索次数
- **AND** 不与“写入结果未知”混为一类

#### Scenario: 已匹配但没有可用边

- **WHEN** episode 已匹配且处理完成，但读回没有可用边或边无法映射来源
- **THEN** build 以 `processing_timeout` 或 `graph_incomplete` 失败
- **AND** 不把“可搜索”当作完成证据

#### Scenario: 写入结果未知

- **WHEN** 写入响应丢失、起初没有 episode UUID，且确认预算内无法通过检索恢复
- **THEN** build 以 `write_unconfirmed` 失败
- **AND** 报告记录原幂等键、写入尝试与检索尝试，不生成新键

#### Scenario: 模型不可达时失败且不生成节点

- **WHEN** worker 调用模型时网络不可达或超时
- **THEN** build 进入 `failed`，原因码为 `model_unavailable` 或 `timeout`
- **AND** 没有新增 episode、节点、边或完成快照，`canContinue=false`

#### Scenario: 认证失败只调用一次

- **WHEN** 模型或 Zep 返回 401 或普通 403
- **THEN** build 以 `auth_failed` 失败，不进行可重试循环
- **AND** 报告记录实际调用次数与稳定原因码，不记录密钥或响应正文

#### Scenario: 失败后重新开始产生新身份

- **WHEN** 一个 build 已失败，项目所有者用同一材料与问题重新开始
- **THEN** 返回新的 `buildId`、`jobId`、`runId` 与新的 episode 幂等键
- **AND** 旧 build 的状态、事件、快照与材料来源保持不变

### Requirement: 完成判定、原子发布与下一阶段入口

系统 SHALL 在建图接口中返回 `canContinue` 与下一阶段标识。`canContinue` MUST 只有在 build 为 `ready`、最终已发布快照 `complete=true`、节点数至少为 1、边数至少为 1，且所有 `origin=material` 的节点与边都至少有一条通过校验的材料来源时才为 true。前端 MUST 使用该服务端字段控制“进入下一阶段”，不得自行根据节点数或动画状态推断。

第 6 步可以准备或校验候选快照，但候选快照 MUST NOT 对外可见。只有第 6 步发布事务成功后，外部才能同时看到最终快照 `complete=true`、`phase=ready`、`canContinue=true`、完成事件与任务 `succeeded`。发布事务前崩溃、取消或失败 MUST NOT 产生可见的最终快照；当前 build 最近一次已发布的部分快照 MUST 仍可查看。

取消与发布竞争时 MUST 只保留先提交的终态：若发布先提交，取消返回已 `succeeded`；若取消先提交，发布事务 MUST 因任务已终态或租约失效而回滚，不产生最终快照或完成事件。

`canContinue=true` 时，工作台 MUST 提供进入第二阶段“准备角色与环境”的入口，并保留当前 `layout` 与 `lang`；本变更 MUST NOT 实现角色与环境配置本身。`canContinue=false` 时 MUST 禁用入口并说明缺少完成建图或缺少有效来源。

#### Scenario: 完成且来源齐全时允许进入下一阶段

- **WHEN** build 为 `ready`，最终已发布快照至少有一个材料节点和一条材料关系，所有材料来源均通过校验
- **THEN** 接口返回 `canContinue=true` 与下一阶段标识
- **AND** 工作台显示可用的“进入准备角色与环境”入口，点击后进入第二阶段并保留布局与语言

#### Scenario: 部分快照不允许进入下一阶段

- **WHEN** build 处于等待索引或增量绘图
- **THEN** 接口返回 `canContinue=false`
- **AND** 工作台禁用下一阶段入口并说明建图尚未完成

#### Scenario: 发布前崩溃不产生可见最终快照

- **WHEN** worker 在第 6 步准备或校验候选快照后、发布事务提交前崩溃
- **THEN** 接口仍只返回最近一次已发布的部分快照，最终快照不可见
- **AND** `phase` 不是 `ready`，`canContinue=false`，没有完成事件
- **AND** 接手者重新校验并发布后，五类结果才同时可见

#### Scenario: 取消先于发布只保留部分快照

- **WHEN** 建图在增量绘图后收到取消，且取消事务先于发布提交
- **THEN** build 进入 `canceled`，当前 build 最近一次已发布的部分快照仍可查看
- **AND** 不产生最终快照、`ready`、`canContinue=true` 或完成事件
- **AND** 页面显示取消原因，不显示“已完成”

#### Scenario: 发布先于取消时保留成功

- **WHEN** 发布事务先提交，取消请求随后到达
- **THEN** 任务保持 `succeeded`，返回已发布的最终快照与 `canContinue=true`
- **AND** 不追加 `canceled` 事件，不把最终快照标记为取消

#### Scenario: 来源不完整不允许完成

- **WHEN** 建图结果中存在 `origin=material` 但没有任何有效材料来源的节点或边
- **THEN** build MUST NOT 进入 `ready`，`canContinue=false`
- **AND** 页面显示来源校验失败原因，不展示为可继续的完成图谱

#### Scenario: 空图不标记完成

- **WHEN** 全部 episode 已匹配处理，但读回结果没有材料来源节点或没有边
- **THEN** build MUST NOT 进入 `ready`，而以 `insufficient_graph` 失败
- **AND** 页面显示证据不足原因，`canContinue=false`

### Requirement: 规模上限与明确原因

首版单次建图 MUST 限制为 5 份材料、200 个节点、400 条边、每项来源最多 5 条。材料数量超限 MUST 在开始请求内以 422 `too_many_documents` 拒绝；节点、边或来源数量超限 MUST 在建图终态以 `graph_too_large` 或 `too_many_sources` 失败，并记录实际数量与配置上限。系统 MUST NOT 通过静默截断、丢弃来源或只展示前 N 条来“满足”上限。

#### Scenario: 材料数量超限明确拒绝

- **WHEN** 项目所有者选择 6 份材料
- **THEN** 返回 422 与 `too_many_documents`，说明单次最多 5 份
- **AND** 不创建 build、job 或 run

#### Scenario: 节点数量超限明确失败

- **WHEN** 读回节点数超过 200
- **THEN** build 以 `graph_too_large` 失败，记录实际节点数与上限
- **AND** 不发布截断后的完成快照

#### Scenario: 边数量超限明确失败

- **WHEN** 读回边数超过 400
- **THEN** build 以 `graph_too_large` 失败，记录实际边数与上限
- **AND** 不发布截断后的完成快照

#### Scenario: 单项来源超限明确失败

- **WHEN** 某个节点或边关联超过 5 条材料来源
- **THEN** build 以 `too_many_sources` 失败，记录条目标识与上限
- **AND** 不丢弃多余来源来伪装通过

### Requirement: 最近 build 选择与旧 build 查询

建图读取接口 MUST 支持“默认最近 build”和“按 ID 查询旧 build”：

- 未提供 `build` 参数时，若项目存在 `queued` 或 `running` 的 build，MUST 返回该活动 build；否则返回最近创建的 build。
- 提供 `build=<buildId>` 时，MUST 返回该 ID 对应的 build；该 build 必须属于当前项目，否则返回与不存在 build 相同的 404。
- 前端 MUST 用 `buildId` 绑定当前展示的 build 与快照；任何 `buildId` 不匹配的迟到响应 MUST 被丢弃。

同一项目连续两次建图时，第二次仍在等待索引 MUST NOT 让刷新或迟到响应把第一次的完成图当作第二次结果。默认接口在第二次活动期间 MUST 返回第二次 build 与它的最近已发布部分快照；只有显式查询第一次 `buildId` 时才返回第一次的完成图。

#### Scenario: 第二次建图等待时不显示第一次完成图

- **WHEN** 第一次 build 已完成，第二次 build 仍处于等待索引
- **THEN** 未带 `build` 参数的接口返回第二次 build，`phase=waiting_index`，没有完成快照
- **AND** 页面不显示第一次的节点、边、计数或“已完成”
- **AND** 刷新后仍返回第二次 build

#### Scenario: 迟到的第一次响应不覆盖第二次

- **WHEN** 第一次 build 的快照响应在第二次 build 已开始后迟到到达
- **THEN** 前端因 `buildId` 不匹配丢弃该响应
- **AND** 当前画面、计数与 phase 仍属于第二次 build

#### Scenario: 按 ID 查询旧 build

- **WHEN** 用户显式请求 `build=<第一次 buildId>`
- **THEN** 接口返回第一次 build 与其已发布完成快照
- **AND** 不改变第二次活动 build 的默认读取结果

#### Scenario: 他人项目不能读取 build

- **WHEN** 用户 B 请求 A 项目的 build 或快照
- **THEN** 返回与不存在项目相同的 404 `项目不存在`
- **AND** 不返回 build、节点、边或来源元数据

### Requirement: 默认零外呼与测试替身

系统 SHALL 通过 provider 配置区分真实调用、未配置与测试替身。默认 `.env.example` 的占位值 MUST 解析为未配置；建图在外部调用前以 `missing_config` 失败。测试替身 MUST 只能由显式测试配置与允许开关启用；默认 `make up`、默认 `make verify` 与 CI MUST NOT 触发业务建图或真实模型/Zep 调用。`make verify CHANGE=2026-11-22-build-evidence-graph` MUST 使用原始夹具与替身，并在报告中记录 `calls.real=0`、`calls.double>0`。

#### Scenario: 默认配置下点击建图不产生真实外呼

- **WHEN** 使用占位配置启动默认环境并尝试建图
- **THEN** provider 未配置，任务在外部调用前以 `missing_config` 失败
- **AND** 真实模型与 Zep 调用次数均为 0

#### Scenario: Change 07 验收使用替身

- **WHEN** 执行 `make verify CHANGE=2026-11-22-build-evidence-graph`
- **THEN** 自动化使用原创小材料与测试替身完成建图全链路
- **AND** 报告记录 `calls.real=0`、`calls.double>0`
- **AND** 数据库、队列、租约、事件与快照仍使用真实实现

#### Scenario: 测试替身未显式允许时拒绝

- **WHEN** 配置请求测试替身但未设置允许开关
- **THEN** provider 拒绝启动替身，任务在外部调用前失败
- **AND** 真实调用次数保持 0，不静默回退到真实 provider
