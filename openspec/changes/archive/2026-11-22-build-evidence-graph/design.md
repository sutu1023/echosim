# Design

## Context

本变更建立在 Change 01–06 已合并的基础上：

- `app` 只处理 HTTP、鉴权、项目归属与同域静态托管；`worker` 独立领取 `jobs`，通过租约、`run_steps`、`jobs.next_step` 与 `run_events` 表达“做到第几步”和“发生过什么”。事件先与业务变更同事务提交，再通过 SSE 推送。
- 材料入库已产生 `documents` 与 `document_chunks`。`document_extract.py` 的坐标语义固定为：Markdown/TXT 的 `source_start`/`source_end` 是解码后完整原文的 UTF-16 偏移（换行归一与空白清理之前）；PDF 的偏移基于对应物理页的原始抽取文本；`document_chunks.text` 是清洗后的文本。
- 第一阶段工作台已有材料面板、测试任务面板、三种布局与双语；图谱画布目前只是占位说明。
- 引擎联通把固定验收图谱、模型 `json_object`、双平台动作放在隔离的 `engine` 镜像与 `make smoke-live` 中；`app` 不接收模型/Zep 密钥，`ZEP_ACCEPTANCE_GRAPH_ID` 只属于联调。
- 本机 `echosim-engine:dev` 中实际安装 `zep-cloud==4.1.0`。只读核对到的签名是：`graph.create(*, content_policy=..., description=..., name=..., time_zone=..., idempotency_key=None, request_options=None) -> Graph`，没有客户端图谱 UUID 或通用 metadata 参数，返回 `Graph.uuid_`；`graph.episode.add(...) -> AddEpisodeResult`，含 `episode.uuid_`、`episode.processed`；`graph.search_episodes(...) -> SyncPager[Episode, EpisodePage]`；`graph.node.list`/`graph.edge.list` 支持 `page_size`、`cursor` 与带 `episode_uuids` 的过滤器，返回可分页集合。

## Goals / Non-Goals

**Goals:**

- 让项目所有者用本项目已就绪材料与情景问题发起一次业务建图，请求立即返回，执行只在 worker。
- 每个项目通过 `graph.create` 的项目专用幂等键绑定一个图谱；创建响应丢失、响应已到但数据库未提交、worker 接手都只绑定一个图谱。
- 区分写入受理、episode 匹配、处理完成与节点/边读回，避免“写入成功”或“可搜索”被误报为完成。
- 第 6 步原子发布：外部同时看到最终快照、`ready`、`canContinue`、完成事件与任务成功；失败/取消保留最近已发布部分快照。
- 节点、边与计数来自同一份已发布快照；同一项目第二次建图不会被第一次完成图或迟到响应污染。
- 文本与 PDF 来源都能映射回正确原文位置；歧义引文不取第一次匹配；既有入库材料也能定位。
- 第一阶段保留材料面板与测试任务面板，提供 D3 图谱、默认最近 build、按 ID 查询旧 build、限制原因与下一阶段入口。
- 默认启动、默认 `make verify` 与 CI 保持零真实外呼。

**Non-Goals:**

- 不实现角色与环境配置、双平台运行、报告生成或深度互动。
- 不实现图算法、实体消歧、跨项目图谱共享、生产级配额/计费或项目图谱删除入口。
- 不把验收专用图谱业务化，不让业务建图写入 `ZEP_ACCEPTANCE_GRAPH_ID`。
- 不把私有材料、私有样本、外部品牌名、链接、代码、截图或回放数据写入仓库、日志或报告。
- 不实现多轮追问、图谱编辑、手工合并节点或每次 build 独立图谱。

## Decisions

### 1. 业务 worker、引擎依赖与调用边界

**决定**：业务建图任务由现有 `worker` 进程领取和执行。`app` 只提供开始、读取与快照接口，不调用模型或 Zep，也不持有模型/Zep 密钥。模型调用使用标准库 HTTP；Zep 客户端只在业务 worker 的独立构建目标中安装，并在真正需要时延迟导入。

| 服务 | 代码 | 额外依赖 | 引擎变量 | 执行建图 |
| --- | --- | --- | --- | --- |
| `app` | 应用运行时 | 无 | 无模型/Zep 变量 | 否 |
| `worker` | 应用运行时 + 建图执行分支 | 仅 Zep 客户端（`app/requirements-worker-graph.txt`） | 业务模型/Zep 配置、provider 模式、规模上限 | 是 |
| `engine` | `app/engines/` 隔离镜像 | 候选 camel/oasis/zep | 模型/Zep 联调配置、`ZEP_ACCEPTANCE_GRAPH_ID` | 否（只跑 smoke-live） |

业务模型输入 MUST 来自本次选中的、本项目已就绪材料与用户问题；不能使用仓库内固定夹具冒充业务输入。自动化与 CI 使用运行时生成的原创小材料和测试替身；替身只替换外部调用，不替换数据库、队列、租约、事件或快照。

**理由**：复用现有 worker 直接获得租约、`attempts`、`awaitingTakeover`、步骤事务与 SSE；把 camel/oasis 留在 `engine`，避免联调依赖污染业务运行时；模型不引入新 SDK，Zep 延迟导入使缺少依赖时能在外部调用前失败。

**备选方案**：

- `app` 请求内调用模型/Zep：否决，违反长任务边界，无法接手，且让 HTTP 进程持有外部密钥。
- 复用 `engine` 镜像执行建图：否决，`engine` 不含应用数据库与服务代码。
- 把 camel/oasis 装进 `app`：否决，Pydantic 约束会反向锁死应用基线。

### 2. 项目图谱绑定：先持久化幂等键，再调用 graph.create

**决定**：新增 `project_graph_bindings` 表，每个项目一行：

- `project_id` 主键；
- `create_idempotency_key`（UUID，唯一）；
- `request_name`、`request_description`、`request_time_zone`（稳定、无用户正文）；
- `graph_id`（服务端返回的 `Graph.uuid_`，可空，唯一）；
- `state`（`pending` 或 `bound`）、时间戳。

首次建图时，先在独立事务中插入并提交绑定记录（幂等键与稳定请求参数），然后调用 `zep-cloud==4.1.0` 的：

```python
graph = client.graph.create(
    name=request_name,
    description=request_description,
    time_zone=request_time_zone,
    idempotency_key=create_idempotency_key,
)
graph_id = graph.uuid_
```

不在调用中传客户端指定的图谱 UUID，也不传通用 metadata。成功后保存 `graph.uuid_` 并把状态置为 `bound`。

- 已有 `graph_id`：后续 build 直接使用，不再次创建。
- `graph_id` 为空：使用原幂等键、原 `name`/`description`/`time_zone` 重试创建。
- 响应丢失：同一逻辑创建重试，服务端应按幂等键返回同一图谱；最终只保存一个 `graph_id`。
- 响应已返回但数据库未提交：绑定记录仍是 `pending`，接手者用同一幂等键重试，拿到同一图谱后保存。
- 返回空 UUID：`invalid_graph_response`；返回 UUID 与已保存 `graph_id` 不一致：`invalid_graph_scope`，不覆盖。
- 业务 worker 只从当前项目的绑定记录取图谱标识，不接受请求、查询参数或模型输出中的图谱标识。
- 绑定图谱标识与验收专用图谱相同时，在写入 episode 前以 `invalid_graph_scope` 失败；业务 worker 环境没有 `ZEP_ACCEPTANCE_GRAPH_ID`。

**理由**：`graph.create` 的实际签名不接受客户端图谱 UUID 或 metadata，因此“客户端先生成图谱 UUID”不可用。能稳定重试的身份只能是 SDK 支持的 `idempotency_key` 加稳定请求参数。先持久化绑定再调用，才能覆盖“响应丢失”和“响应已到但数据库未提交”两种崩溃窗口。

**备选方案**：

- 客户端指定图谱 UUID：SDK 不支持，否决。
- 用 `graph.list(search=name)` 反查：名称不是唯一键，且可能匹配其它项目，否决为主路径。
- 每次 build 创建新图谱：项目会积累图谱，后续阶段难选择，产品决定为项目复用，否决。

### 3. 六步 run 与第 6 步原子发布

**决定**：新增 `runs.kind = evidence.graph.build`，`jobs.operation_key = evidence-graph-build:v1`。一次 build 固定六步：

| 步 | 步骤键 | 主要动作 | 外部调用 | 提交后的可见性 |
| --- | --- | --- | --- | --- |
| 1 | `bind` | 校验输入；确保项目图谱绑定 | 首次可能需要 `graph.create` | `phase=preparing` |
| 2 | `plan` | 模型生成实体/关系类型与批次计划 | 模型 | 计划持久化，仍 `preparing` |
| 3 | `extract` | 按批次抽取节点、关系与来源 | 模型 | 抽取清单持久化，仍 `preparing` |
| 4 | `write` | 持久化 episode 内容/幂等键并写入 | `episode.add` | 至少一个 accepted，进入 `waiting_index` |
| 5 | `readback` | 检索匹配、等待处理、分页读回节点/边 | 检索/列表 | 部分快照已发布 |
| 6 | `publish` | 准备或校验候选快照，并在发布事务中原子发布最终快照、`ready`、`canContinue`、完成事件、任务成功 | 否 | 五类结果同时可见 |

每个步骤在单事务内完成“锁定任务行 → 用数据库实际时钟验证租约 → 写步骤副作用 → 写业务状态 → 追加事件 → 推进步号”。`run_steps` 与 `jobs.next_step` 是步骤权威；快照版本是业务读模型。

第 6 步可以在发布事务内准备或校验候选快照，但候选快照 MUST NOT 出现在事件或读取接口。第 6 步事务提交时，才把最终快照置为 `complete=true, published=true`，同时设置 build `phase=ready`、`can_continue=true`、写 `completed` 事件、把 job 置为 `succeeded`。发布前崩溃只留下已发布部分快照或不可见候选；取消先提交时发布事务因任务终态/租约失效回滚。

**理由**：把“准备候选”和“对外发布”分开，才能做到“五类结果同时可见”且不暴露半成品；六步比七步少一个可崩溃的中间发布窗口。

### 4. 读回证据：写入受理、episode 匹配、处理完成、节点/边读回

**决定**：四个证据分开记录：

1. **写入受理**：`graph.episode.add` 返回 `AddEpisodeResult.episode.uuid_`。保存 UUID、幂等键、内容哈希。没有 UUID 就不算受理。
2. **episode 匹配**：`graph.search_episodes` 返回的 `Episode.uuid_`、本次 `buildId` 标记与内容哈希同时一致。写入响应丢失、起初没有 UUID 时，用 `buildId` 标记 + 内容哈希在检索结果中恢复 UUID。
3. **处理完成**：全部计划 episode 匹配，且 `Episode.processed == true`。可搜索不等于处理完成。
4. **节点/边读回**：用本次 build 已匹配、已处理 episode 的 UUID 集合作为过滤条件：

```python
nodes = client.graph.node.list(
    graph_id, page_size=100, cursor=cursor,
    filters=NodeListFilters(episode_uuids=current_episode_uuids),
)
edges = client.graph.edge.list(
    graph_id, page_size=100, cursor=cursor,
    filters=EdgeListFilters(episode_uuids=current_episode_uuids),
)
```

按 `SyncPager` 的 `has_next`/`next_page` 或游标循环到没有下一页；按 `uuid_` 去重；节点上限 200、边上限 400。读回集合必须在两次相邻读取中稳定，且至少有一个节点与一条边。

同一图谱存在旧 build 时，旧 episode 不在本次 `episode_uuids` 集合中，因此旧节点/边被过滤掉。检索同批返回旧记录与正确记录时，只接受 UUID + `buildId` 标记 + 内容哈希三者匹配的正确记录。只返回旧记录时继续等待。

**材料关系映射**：第 3 步持久化的抽取清单包含 `relationKey`、端点、关系类型、`origin` 与来源。读回边时，用其 `episode_uuids` 交集定位本次 episode，再按关系事实/名称与端点匹配清单关系；若同一 episode 内仍无法唯一映射，该边不展示为材料关系，并在发布前以 `processing_timeout`/`graph_incomplete` 失败。每条展示的材料关系必须能关联到清单中的关系与至少一条通过校验的原文来源。

**失败分类**：

- `index_timeout`：写入已受理且 UUID 已知，但索引预算内未匹配。
- `processing_timeout`：已匹配，但处理预算内未 `processed=true`、读回不稳定或没有可用边。
- `graph_incomplete`：已处理但读回无法组成至少一个材料节点与一条材料关系。
- `write_unconfirmed`：写入响应丢失、起初无 UUID，确认预算内无法通过检索恢复。

**理由**：`episode.add` 成功、`search_episodes` 命中、`processed=true`、`node/edge.list` 返回可用结构是四个不同事实。分开记录才能给用户准确状态，也才能在“episode 可搜索但还没有可用边”时继续等待而不是假完成。

### 5. 完成判定与同版本快照

**决定**：完成必须同时满足：

1. 全部计划 episode 匹配并 `processed=true`；
2. 节点/边按当前 episode 集合分页读回、两次相邻读取稳定；
3. 至少一个 `origin=material` 节点与一条材料关系，且所有材料来源通过原文校验；
4. 最终快照在发布事务中 `complete=true, published=true`；
5. build `phase=ready`、`can_continue=true`、完成事件与 job `succeeded` 同一事务可见。

快照是完整物化：`graph_snapshots` 一行带 `revision`、`complete`、`published`、`node_count`、`edge_count`；`graph_nodes`/`graph_edges`/`graph_evidence` 都挂 `snapshot_id`。接口一次返回同一快照的计数、节点、边与来源；前端以 `revision` 单调合并，忽略旧响应。部分快照 `complete=false` 可以发布给页面做增量绘图；候选最终快照在发布前不可见。

**理由**：同一快照同时决定计数与画面，排除计数/画面错配；完整快照让刷新、接手与回放简单。代价是快照重复存储，用节点/边上限与旧部分快照清理控制。

### 6. 最近 build、旧 build 与第二次建图隔离

**决定**：读取接口默认选择：项目有 `queued`/`running` build 时返回该活动 build，否则返回最近创建的 build。提供 `build=<buildId>` 时返回该 ID 的 build，必须属于当前项目，否则 404。

前端把当前 `buildId` 与快照绑定：

- 默认进入第一阶段使用服务端返回的最近 build。
- 打开旧 build 使用完整 URL 的 `build=<旧 buildId>`，保留 `layout`/`lang`/`document`。
- 任何 `buildId` 不匹配的迟到响应直接丢弃。
- 第二次 build 仍在等待索引时，默认读取返回第二次 build 与它的部分快照，不显示第一次完成图；只有显式 `build=<第一次>` 才显示第一次完成图。

**理由**：同一项目图谱长期复用，第二次 build 可能仍在等待，而第一次已完成。如果只按“最近快照”读取，会把第一次完成图误显示为第二次结果。用 buildId 选择与响应守卫才能隔离。

### 7. 原文定位：清洗文本映射回原文，歧义不取第一次

**决定**：来源坐标语义完全遵循 `app/services/document_extract.py`：

- TXT/Markdown：`sourceStart`/`sourceEnd` 是解码后完整原文的 UTF-16 偏移，区间左闭右开，基于换行归一与空白清理之前的位置。
- PDF：偏移基于对应物理页的原始抽取文本，不是跨页拼接文本或清洗文本；`pageNo` 从 1 开始。
- `document_chunks.text` 是清洗后的分块文本。

服务端在发布快照前做映射：

1. 按需读取原件；文本按解析时编码解码完整原文，PDF 逐页重新抽取原始文本。
2. 用与入库相同的归一化逻辑重建“清洗文本 → 原文偏移”的字符映射；对 PDF 逐页重建，对文本基于完整原文重建。
3. 用分块已有 `source_start`/`source_end` 与分块文本定位该分块在清洗文本中的位置；已有入库材料无需重新上传或重新解析。
4. 在分块清洗文本中查找候选引文的全部出现位置；若多次出现或映射到多个不同原文区间，要求模型提供 `sourceStart`/`sourceEnd` 消歧；仍不能唯一确定则以 `ambiguous_source` 失败，绝不取第一次匹配。
5. 用映射后的原文区间切片校验 `quote`；不一致为 `source_mismatch`。原件不可读取为 `source_unavailable`。

浏览器使用同一位置：

- 文本来源：`/source` 返回解码后的完整原文；浏览器用 UTF-16 偏移切片/高亮，emoji、连续空格、CRLF 不影响偏移。
- PDF 来源：打开 `/file#page=<pageNo>`；详情展示引文，页码是物理定位。
- 点击一条边：`buildId + snapshotId + edgeKey` → 同快照 `graph_evidence` → `documentId/chunkIndex/pageNo/paragraphStart/End/sourceStart/End/quote` → PDF `#page=N` 或文本高亮。不调用模型或 Zep。

**理由**：清洗会折叠空白、移除重复页眉，清洗文本下标不能直接当原文偏移。用原文坐标 + 清洗映射 + 歧义拒绝，才能让“回到原文”真的可核对，且兼容既有材料。

### 8. 规模上限与明确原因

**决定**：首版固定上限：单次 5 份材料、200 节点、400 边、每项 5 条来源。

- 6 份及以上材料：开始请求 422 `too_many_documents`，不创建任务。
- 节点超过 200 或边超过 400：`graph_too_large`，记录实际数量与上限。
- 单项来源超过 5：`too_many_sources`，记录条目标识与上限。
- 不截断节点/边、不丢弃来源、不只展示前 N 条。

**理由**：明确失败比静默截断更可信；上限同时保护 D3 渲染、快照大小和报告体积。

### 9. 第一阶段工作台

**决定**：

- 保留 `MaterialsPanel` 和“测试任务”面板；新增 `EvidenceGraphPanel`。
- 图谱面板：材料多选（最多 5）、问题输入、开始、三态进度、快照版本、计数、D3 力图、图例、缩放/平移、节点拖动、边标签开关、刷新、最大化、详情侧栏、下一阶段入口。
- 默认最近 build；旧 build 通过 `?build=<id>` 打开；`buildId` 不匹配的响应丢弃。
- `graph` 布局把图谱面板作为全宽画布，材料面板折叠为工具条抽屉；`split`/`workbench` 保留材料与图谱同屏。
- 详情来源跳转复用材料面板的 PDF/原文定位，保留 `layout`/`lang`/`buildId`/`document`。
- `canContinue=true` 时 `push` 到 `roles-environment` 并保留 `layout`/`lang`/`buildId`；第二阶段显示角色与环境尚未配置，不再显示缺少证据图谱。
- 规模超限、来源歧义、处理超时等都在面板显示明确原因。

**理由**：保留材料与测试面板满足既有工作流；D3 只渲染已发布快照，避免前端重建图；buildId 守卫解决第二次建图串图。

### 10. 默认零外呼与两轮人工验收

**决定**：

- provider 配置：`EVIDENCE_GRAPH_MODEL_PROVIDER`、`EVIDENCE_GRAPH_GRAPH_PROVIDER`（`auto|unconfigured|zep|stub`）、`EVIDENCE_GRAPH_ALLOW_STUB`（默认 false）。
- `auto` 只在配置完整时解析为真实 provider；`.env.example` 占位值解析为未配置，建图在外部调用前以 `missing_config` 失败。
- `stub` 只在显式允许时可用；默认 `make up`、默认 `make verify`、CI 不触发业务建图。
- `make verify CHANGE=2026-11-22-build-evidence-graph` 使用原创小材料与替身，报告 `calls.real=0`、`calls.double>0`。
- 人工验收两轮：第一轮私有 25 页报告只通过作者自己的私有项目上传，观察三态并抽查 3 个材料节点 + 3 条材料关系回到原文；第二轮另一份原创校园接驳车材料，核对实体/关系随材料改变。私有样本、正文、外部品牌名、链接、代码、截图与回放数据留在仓库外；仓库内只使用 EchoSim 自己的文案、代码、资产与运行时生成的原创小材料。

**理由**：默认配置不能因新增接口产生外部调用；替身必须显式开启；两轮人工验收才能证明“可追溯”和“随材料改变”。

### 11. 数据库与前向迁移

新增 `app/migrations/0005_evidence_graph_builds.sql`（名称以实现为准），只前向：

- `ALTER TABLE runs DROP CONSTRAINT runs_kind_check`，加入 `evidence.graph.build`。
- `project_graph_bindings`：项目主键、创建幂等键唯一、稳定请求参数、`graph_id` 唯一可空、状态、时间戳。
- `graph_builds`：`id`、`project_id`、`run_id UNIQUE`、`question`、`phase`、`planned_episodes`、`accepted_episodes`、`confirmed_episodes`、`failure_code/message`、时间戳。
- `graph_build_documents`：`(build_id, document_id)` 主键、`document_sha256`、`chunk_count`、`ordinal`。
- `graph_episodes`：`(build_id, episode_key)` 主键、`idempotency_key UNIQUE`、`content`、`content_sha256`、`episode_uuid`、`state`、`attempts`、时间戳。
- `graph_snapshots`：`id`、`build_id`、`revision`、`complete`、`published`、`node_count`、`edge_count`、`episode_count`、`created_at`，`UNIQUE(build_id, revision)`。
- `graph_nodes`/`graph_edges`：`(snapshot_id, key)` 主键，保存类型、标签、`origin`、摘要与端点。
- `graph_evidence`：`(snapshot_id, target_kind, target_key, evidence_index)` 主键，保存 `document_id`、`chunk_index`、页码/段落、UTF-16 偏移与引文，并以 `(document_id, chunk_index)` 关联 `document_chunks`。

迁移在已有数据卷上可重复安全启动；旧项目没有绑定记录，首次建图时补齐。回滚策略是部署上一版镜像；新表保留但旧代码不读取，不做破坏性 down。

## Risks / Trade-offs

- **`graph.create` 幂等语义依赖服务端**：SDK 接受 `idempotency_key`，但服务端是否严格返回同一图谱需要在真实联调/受阻测试中确认。→ 绑定记录先落库；若返回不同 UUID，按 `invalid_graph_scope` 失败而不是覆盖；真实联调前不宣称已验证。
- **Zep 处理延迟**：episode 可搜索但节点/边尚未可用。→ 区分 `processed` 与节点/边读回，使用处理预算；预算耗尽按 `processing_timeout`/`graph_incomplete` 失败，不假完成。
- **节点/边与抽取清单映射**：Zep 自动抽取的边可能无法唯一对应清单关系。→ 用 `episode_uuids` 过滤 + 关系事实/端点匹配；无法唯一映射的边不展示，并在发布前失败而不是伪造来源。
- **既有材料重抽取成本**：文本/PDF 需要按需重新解码/抽取以建立映射。→ 只在发布快照或查看来源时按材料缓存；不修改分块与原件；原件缺失明确 `source_unavailable`。
- **歧义引文**：短引文可能多次出现。→ 要求模型偏移消歧，仍不唯一则 `ambiguous_source`，不取第一次。
- **快照膨胀**：每次部分命中存完整快照。→ 200/400 上限、完成后保留最终快照与最近若干部分快照，清理更旧部分。
- **worker 依赖变化**：worker 增加 Zep 客户端。→ 只进入独立构建目标，不安装 camel/oasis；`app` 依赖不变；缺少依赖时 `dependency_unavailable`。
- **第二次 build 串图**：同一项目图谱长期复用。→ 默认活动 build、`build` 查询、前端 `buildId` 守卫、迟到响应丢弃。
- **私有样本泄漏**：→ 私有 25 页报告与原创校园接驳车材料只存在作者本地；自动化只用运行时生成的原创小材料；日志/报告只记录标识、阶段、原因码与异常类型。

## Migration Plan

1. 应用 `0005` 迁移，确认旧数据卷可启动、旧接口不变。
2. 构建 worker 新目标镜像（只加 Zep 客户端），`app` 与 `engine` 镜像不变。
3. 以未配置 provider 启动，确认建图在外部调用前失败且无回归；默认 `.env.example` 仍为占位值。
4. 部署 API 与前端；默认显示最近 build，旧 build 通过 `?build=` 查询。
5. 自动化验收环境显式启用 stub，执行 Change 07 场景；CI 使用同一入口与独立 artifact。
6. 开发者在本地配置真实模型/Zep（不提交）后完成两轮人工验收；通过后再考虑是否把 provider 默认改为真实。

回滚：部署上一版 app/worker/web 镜像；`0005` 新表保留但旧代码不读取。迁移只前向，不提供 down；若必须清理，使用单独运维脚本并先备份数据卷，不属于本变更。
