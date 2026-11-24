# Design

## Context

本变更建立在 Change 01–07 已合并的实现上，设计只引用与第二阶段直接相关的现状：

- `web/src/views/EvidenceGraphPanel.vue` 的 `goNextStage()` 已用 `stageLocation(props.projectId, 'roles-environment', { ...route.query, build: state.value.buildId })` 跳转，因此第二阶段可以从地址中精确取得 `build`，不需要再猜“最近一次建图”。
- `web/src/views/WorkbenchView.vue` 当前把 `stageHasData` 固定为 `false`，第二阶段显示 `empty.rolesTitle` / `empty.rolesBody` 的“角色与环境尚未配置”空态；`web/src/workbench/stages.ts` 是五阶段顺序、双语名称与进入条件的唯一注册表。
- `app/routers/evidence_graph.py` 的 `GET /api/projects/{projectId}/evidence-graph` 使用 `read_build_graph()` 在同一数据库语句快照中返回 build 与最近已发布快照；`app/services/evidence_graph.py` 的 `GraphSnapshot` 包含 `snapshotId`、`revision`、`complete`、`published`、`nodes`、`edges` 与 `evidence`，`snapshot_can_continue()` 已定义完整性判据。
- `app/services/worker.py` 已有可复用的领取、租约验证、`_commit_graph_step()` 式步骤事务、`run_events.append_event()`、`_fail_graph_build()` 与 `awaitingTakeover` 语义；`app/worker.py` 已按 `runs.kind` 分派 `evidence.graph.build` 与 `document.ingest`。
- `app/routers/jobs.py` 已提供项目内任务的 SSE 与 JSON 补发接口，游标语义为“已完整处理的最后一个连续 seq”；前端已有 `web/src/workbench/job-stream.ts` 与 `job-log.ts`。
- 迁移只前向执行；现有 `runs.kind` 约束在 `0005_evidence_graph_builds.sql` 中扩展过，新增 run 类型必须继续以前向迁移扩展。
- 证据来源定位已固定为 PDF 物理页原始文本与文本解码后完整原文的 UTF-16 偏移；角色来源必须复用这套定位，不得重新解析材料或另造坐标。
- `app/services/worker.py` 的 `_entity_label_map()` 把建图计划里的实体键与英文类型名映射为 `displayName`；因此当前快照的 `nodeType` 通常是中文显示类型（如“高校”“事件”）。旧快照没有该映射时保留读回标签或 `其他主体`，英文类型仍可能出现，选择器必须同时识别中文显示类型与英文类型。
- `_materialize_readback_text()` 生成的文本快照节点 `summary=None`；节点来源来自 `select_mention_quote()`，只能证明“材料提到该主体”；关系来源来自 `select_support_quote()`，同时定位起点、动作与对象，并写入 `claimNature`。`app/routers/evidence_graph.py` 的 `_snapshot_body()` 原样返回 `summary` 与 `claimNature`，旧快照的 `claimNature` 可能为空。角色筛选与来源说明必须建立在这些实际字段上，而不是假设节点摘要或关系起点天然足够。

## Goals / Non-Goals

**Goals:**

- 把“进入第二阶段”变成一次可审计的绑定：`buildId`、`snapshotId`、`question` 三者从读取到发布、确认、后续读取都不漂移。
- 角色筛选与材料事实可重复：同一份快照、同一套规则得到同一批候选；事件、说法、建议、平台不会因模型措辞而变成角色。
- 模型只补充模拟设定，材料事实保持原样且可回到原文；前端不从事件或本地状态拼装角色。
- 准备任务复用现有 worker/租约/事件，具备普通开始复用、显式重试、刷新重连、中断接手与分批预览；最终角色、平台、话题与顺序仍原子发布。
- 轮数计算与确认冻结由服务端唯一决定，Change 09 可以只读一份冻结选择。
- 默认验收零真实外呼，私有材料只用于作者人工走查。

**Non-Goals:**

- 不实现双平台实际运行、平台账号/凭据、分轮调度、报告或深度互动。
- 不做角色人工编辑、合并、删除或跨项目复用；角色集合由快照与筛选规则决定。
- 不把平台渠道节点变成角色，也不把报告建议变成角色话题或动机；媒体机构与平台渠道分开判定。
- 不为准备引入第二套模型传输、第二套事件流或前端定时器驱动的“进度”。
- 不新增固定角色类型白名单、固定角色数量或“首轮最多 N 个主体”的产品限制。

## Decisions

### 1. 绑定 build 的最终快照，而不是“最近一次图谱”

**决定**：`GET /api/projects/{projectId}/environment?build=<buildId>` 与 `POST /api/projects/{projectId}/environment/preparations` 都先复用 `resolve_owned_project()`，再读取指定 build。只有以下条件同时成立才允许准备：

- build 属于该项目；
- `build.canContinue=true`；
- 该 build 存在 `published=true` 且 `complete=true` 的最终快照；
- 快照的 `buildId` 等于请求的 build。

准备记录保存 `buildId`、`snapshotId`、`question`（build 的推演问题）。角色表、来源表与冻结选择都带 `snapshotId`，并用复合外键保证它们只能引用同一快照。

不带 `build` 的请求沿用证据图谱的默认选择：活动 build 优先，否则最近 build。前端拿到响应后用 `router.replace` 把 `buildId` 固定到地址；之后刷新、布局切换、语言切换、阶段导航都显式保留 `build`。这样“进入时解析一次”不会在准备期间因新 build 完成而改读新图。

`newerBuildId` 只用于提示“已有更新的图谱”，不参与角色读取。旧 build 只要最终快照仍已发布就继续可读；准备期间新 build 完成不会覆盖旧准备。

**理由**：证据图谱的“默认最近 build”适合第一阶段，但第二阶段一旦开始生成角色，任何隐式切换都会让角色与来源跨快照。显式 build + 最终快照 + 复合外键把“避免混用”从 UI 约定升级为数据库约束。

**备选方案**：

- 每次读取都重新选最近 build：否决，准备期间新图会悄悄改变角色。
- 只保存 `buildId`，来源查询时再取快照：否决，快照版本可能在读取与写入之间变化。
- 允许使用部分快照开始准备：否决，角色可能来自尚未读回完整的图。

### 2. 角色候选按实际快照类型筛选，关系方向只提供上下文

**决定**：新增一个纯选择器，读取已发布快照的 `nodeType`、`label`、`summary`、节点来源与关系来源，不重新建图。候选先要求 `origin=material`，且至少一条有效节点来源（引文非空、`sourceStart < sourceEnd`、材料属于本项目并属于本次快照）。

类型判定同时接受 EchoSim 实际存在的两种形态：

- 中文显示类型：`个人`、`人物`、`群体`、`高校`、`机构`、`机构组织`、`部门`、`政府机构`、`法院`、`医疗机构`、`社会组织`、`媒体机构`、`新闻机构` 等为可参与主体；`事件`、`事故`、`待核实说法`、`传言`、`建议`、`措施`、`话题`、`地点`、`平台`、`社交媒体`、`传播平台`、`媒体平台` 等不是角色。
- 英文 PascalCase：`*Person`、`*User`、`*Group`、`*Community`、`*Organization`、`*Institution`、`*Company`、`*University`、`*Government`、`*MediaOutlet`、`*NewsAgency` 等为可参与主体；`*Event`、`*Incident`、`*Claim`、`*Allegation`、`*Rumor`、`*Recommendation`、`*Suggestion`、`*Measure`、`*Topic`、`*Location`、`*Platform`、`*Channel` 等不是角色。

**媒体机构与平台渠道分开**：`媒体机构`/`新闻机构`/`MediaOutlet`/`NewsAgency` 可以发言与报道，是角色候选；`平台`/`社交媒体`/`传播平台`/`媒体平台`/`Platform`/`Channel` 是环境渠道，不成为角色。旧快照若只有 `媒体` 标签，按媒体机构处理；只有明确含“平台”的类型才按平台渠道处理。

**旧快照与 `其他主体`**：类型为 `其他主体`、`entity`、`Entity`、`Node`、空或无法识别时，不得因为它是关系起点就纳入；必须结合节点来源原句判断该名称是否被明确描述为人物/群体/组织/机构/媒体机构，且没有被描述为事件、说法、建议或平台。仍无法判定时不纳入，并在筛选原因中记录，不需要重新建图。

**关系方向只提供上下文，不提供资格**：节点只出现在关系终点时，只要类型是可参与主体且有材料依据，仍可入选；节点是关系起点但类型为事件、待核实说法或建议时仍不入选。三个反例：

- 只出现在终点的机构：`某高校`（类型“高校”）只作为“媒体机构报道某高校”的终点，仍应成为角色。
- 有出边的事件：`某争议事件`（类型“事件”）作为“事件发生于某地”的起点，仍不得成为角色。
- 待核实说法：`某传闻（报告所称，未证实）`（类型“待核实说法”）即使出现在关系起点，也不得成为角色。

角色键使用快照节点键 `nodeKey`，每个准备内生成稳定 `roleId`。角色类型直接保留快照 `nodeType` 及显示名，数量等于合格主体数量；不设固定类型白名单、固定数量或“首轮 N 个”。没有候选时在开始请求内返回 `no_role_candidates`，不创建准备/job/run。

**理由**：EchoSim 当前快照的 `nodeType` 已经是中文显示类型或旧英文类型，且文本快照 `summary` 恒为空。若继续按英文 PascalCase 或“关系起点”推断，会把高校、媒体机构漏掉，同时把有出边的事件放进来。按类型优先、类型不明时才看原句，才能覆盖新旧快照且不重新建图。

**备选方案**：

- 模型输出角色清单：否决，难以保证不混入事件/说法，且离线验收会依赖模型措辞。
- 固定人物类型白名单：否决，材料中的群体、机构、媒体会被系统性漏掉。
- 把所有材料节点都当角色：否决，会把事件、说法、平台渠道画成可发言者。
- 只按关系起点判断：否决，事件也可以有出边，且只出现在终点的机构会被漏掉。

### 3. 材料实际支持什么：提及、关联话题、表达话题与模拟设定分开

**决定**：角色详情不再把所有材料字段都写成“材料事实”，而是按证据能力分层：

| 字段 | 材料实际支持什么 | 展示 | 来源 |
| --- | --- | --- | --- |
| `mention` / `identity` | 节点来源引文只证明该主体被材料提到；身份名称与类型来自快照节点 | “材料提及” | 节点来源 |
| `background` | 只有关系支持原句明确描述该主体时才成立；节点摘要为空或只有提及不构成背景 | 有则显示；否则“材料未说明” | 关系支持引文 |
| `expressedTopics` | 关系起点是该角色，且支持原句能读出该角色表达、主张或回应了该话题 | “该角色表达/主张…” | 关系支持引文 + `claimNature` |
| `involvedTopics` | 角色只是关系起点或终点之一，原句只能证明材料把角色与该话题关联 | “材料涉及的话题” | 关系支持引文 + `claimNature` |
| `simulationTopics` / `simulation` | 模型或替身为推演补充的关注点、动机、立场、活跃习惯 | “模拟设定” | 模型/替身 |

`author_recommendation` 关系只作为环境背景保留“报告建议”，不作为角色表达过的话题或动机。每条关系来源 MUST 保留快照已有的 `claimNature`；旧快照该字段为空时保留为未知，页面显示“来源性质未知”，不得默认成其他材料陈述、媒体报道或正式决定。

节点来源是 `select_mention_quote()` 选出的提及句，不能单独支持“该角色关注某话题”。只有关系支持句同时出现主体、动作与对象时，才可把话题写成角色表达过的关注；否则只能写“材料涉及的话题”。没有背景或关注证据时，页面显示“材料未说明”，不得用模型内容补成材料事实。

真实模型与替身输入 MUST 包含本次绑定的 `question`，使补充的模拟设定与关注点围绕同一个推演问题；冻结选择 MUST 明确保存该 `question`，Change 09 不再从当前 build 重新取问题。

**理由**：用户需要知道每句话是“材料提到”“材料关联”还是“角色说过”。把节点引文和关系支持句混用，会把媒体名单、机构名单或偶然共现写成角色立场；把旧边空来源性质默认成“已证实”也会改变材料含义。

**备选方案**：

- 节点引文直接生成背景与话题：否决，提及句不等于角色表达。
- 旧边空 `claimNature` 默认成 `other_material`：否决，会把未知来源性质伪装成已知。
- 让模型补写材料事实：否决，无法追溯且会与材料冲突。

### 4. 准备状态机、分批预览与五步 worker

**决定**：新增 `runs.kind=agent.environment.prepare`，操作键为 `agent-environment-prepare:v1:<buildId>`，`runs.stage=roles-environment`，固定五步：

| 步 | 步骤键 | 主要动作 | 提交后可见性 |
| --- | --- | --- | --- |
| 1 | `bind` | 复核 build、最终快照、问题与候选非空；确认准备绑定 | `phase=preparing` |
| 2 | `select` | 运行候选选择器，写入角色草稿、材料字段与来源；`preview_ready_at=NULL` | 草稿不可见 |
| 3 | `profile` | 按稳定批次分批调用模型/替身补齐模拟设定；每批通过来源校验后写 `preview_ready_at` | 已完成批次可预览 |
| 4 | `compose` | 组装平台映射、初始话题、激活顺序与首发话题；校验全部角色/来源/顺序/平台/轮数 | 配置不可见 |
| 5 | `publish` | 单事务把全部角色置为已发布，并发布平台、话题、顺序、`phase=ready`、完成事件与 `succeeded` | 最终结果同时可见 |

`phase` 只取 `preparing`、`ready`、`failed`、`canceled`。批次由配置的画像大小按角色键稳定排序切分；角色行保存 `batch_index`、`preview_ready_at`、`published_at`。`preview_ready_at` 非空且 `published_at` 为空表示“准备中的已校验预览”；`published_at` 非空表示最终角色。平台、初始话题、首发话题与激活顺序只在第 5 步出现。

准备接口在 `phase=preparing` 时返回 `previewRoles` 与已完成批次信息；预览角色必须已通过来源校验。刷新时从该接口恢复已完成批次。worker 接手时读取已 `preview_ready_at` 的角色，跳过整批已完成批次，只补未完成角色；不重复生成已提交批次。预览可供项目所有者展开查看，但不能用于确认或启动第三阶段。

失败或取消时，准备进入 `failed`/`canceled`；准备接口可返回 `previewRoles` 作为“未完成预览”，但 `previewOnly=true`、`canContinue=false`、没有平台/话题/顺序，确认与第三阶段入口均不可用。页面显示失败或取消原因与重试入口；重试创建新的准备，旧预览不参与新准备。

每个步骤复用现有“锁定 job 行 → 用 `clock_timestamp()` 验证租约 → 写副作用 → 写业务状态 → 追加事件 → 推进 `next_step`”的事务边界。第 5 步先校验全部角色、来源、平台、话题、首发话题、顺序与轮数，再一次性发布终态。

**理由**：分批预览让用户在长模型步骤中看到已校验画像，同时把“可预览”与“可确认/可运行”分开；最终发布仍保持原子，避免平台或顺序只出现一半。

**备选方案**：

- 在 HTTP 请求里同步生成：否决，违反长任务边界。
- 所有角色完成前完全不显示：否决，长批次下用户无法判断是否在推进。
- 每批完成即允许确认：否决，角色、平台与顺序尚未完整，后续阶段会读到半套配置。

### 5. 数据模型与前向迁移

**决定**：新增前向迁移，扩展 `runs.kind` 并新增以下表：

| 表 | 作用 | 关键约束 |
| --- | --- | --- |
| `environment_preparations` | 一次准备的绑定、阶段、轮数、预览/画像计数、provider 调用计数与失败信息 | `run_id` 唯一；`(build_id) WHERE phase IN ('preparing','ready')` 部分唯一；`(id, snapshot_id)` 唯一供角色复合外键；`preview_role_count`、`profile_batch_count`、`provider_calls_real`/`provider_calls_double` 单调递增 |
| `environment_roles` | 角色草稿、分批预览与已发布角色 | `role_id` 主键；`(preparation_id, node_key)` 与 `(preparation_id, role_id)` 唯一；`(snapshot_id, node_key)` 外键到 `graph_nodes`；保存 `batch_index`、`preview_ready_at`、`published_at` |
| `environment_role_sources` | 角色材料字段到快照来源的映射 | `(snapshot_id, target_kind, target_key, evidence_index)` 外键到 `graph_evidence`；保存 `material_field` 与 `claim_nature` |
| `environment_platforms` | 两个平台的稳定别名与原生映射 | `(preparation_id, platform_key)` 主键；`platform_a` 必须映射 `twitter`，`platform_b` 必须映射 `reddit`；不保存 Change 06 探针动作作为唯一允许动作 |
| `environment_topics` | 初始话题与来源性质 | `(preparation_id, topic_index)` 主键；`origin` 为 `question/material/simulation`；材料话题必须有来源，`claimNature` 可为空并保留未知 |
| `environment_activation` | 激活顺序、首发角色与首发话题 | `(preparation_id, position)` 主键；`(preparation_id, role_id)` 唯一；首发必须是前缀由服务校验；另存首发角色到话题标识的对应关系 |
| `environment_frozen_selections` | 确认后的不可变选择 | `preparation_id` 唯一；保存 `build_id`、`snapshot_id`、`settings_revision` 与规范 JSON `payload` |

为建立复合外键，迁移同时给 `graph_snapshots` 增加 `UNIQUE (build_id, id)`，给准备表增加 `UNIQUE (id, snapshot_id)`，给角色表增加 `UNIQUE (preparation_id, snapshot_id, role_id)`。这样“角色属于准备、角色节点属于同一快照、角色来源属于同一快照”由数据库裁决，而不是只靠应用层。

角色行分三档：未通过来源校验的草稿（`preview_ready_at IS NULL`、`published_at IS NULL`）不对外返回；已校验预览（`preview_ready_at` 非空、`published_at` 为空）只在准备详情中作为 `previewRoles` 返回；最终角色（`published_at` 非空）在 `phase=ready` 后返回。失败/取消后可返回未完成预览，但 `previewOnly=true`，没有平台、话题、顺序或冻结选择。重试创建新的准备，旧预览不参与新准备。

**理由**：现有图谱已经用复合主键表达“快照内唯一”，准备继续用复合外键比只存 JSON 更能防止跨快照串数据；草稿与已发布共用表避免再加一套发布表。

**备选方案**：

- 角色只存 JSON：否决，无法用数据库约束来源与快照一致性。
- 草稿与已发布分两表：可行但增加搬运与一致性窗口，当前规模不需要。
- 直接复用 `graph_nodes` 当角色：否决，角色有模拟设定、顺序与冻结版本，不能写回图谱。

### 6. 轮数按服务端公式计算，确认时冻结完整选择

**决定**：准备记录保存 `duration_hours`、`minutes_per_round`、`rounds_mode`、`manual_rounds`、`rounds`、`settings_revision`、`confirmed_settings_revision`。默认 `72 / 60 / auto`，`rounds = duration_hours * 60 / minutes_per_round = 72`。

校验规则：

- `durationHours` 为 1–720 的整数，否则 `duration_invalid`；
- `minutesPerRound` 为 1–1440 的整数，否则 `rounds_invalid`；
- 自动模式下 `durationHours * 60` 必须被 `minutesPerRound` 整除且结果 ≥1，否则 `rounds_not_integral`；
- 手动模式下 `manualRounds` 为 1–1000 的整数，否则 `rounds_invalid`；
- 保存必须携带当前 `settingsRevision`，过期返回 `settings_conflict`；成功保存递增 `settingsRevision` 并返回 `autoRounds`、`rounds` 与持久化值。

确认请求 MUST 携带用户最后读到的 `settingsRevision`。服务端在同一事务内锁定准备行、比较当前 `settings_revision` 与请求值，再重新校验就绪条件、轮数与来源；版本不一致返回 `settings_conflict`，不创建冻结记录。版本一致时创建 `environment_frozen_selections`，其 `payload` 是 Change 09 的唯一读取契约：`selectionId`、`preparationId`、`projectId`、`buildId`、`snapshotId`、`question`、`settingsRevision`、轮数配置、角色、平台映射、初始话题及其 `origin`/`claimNature`、首发角色与话题的对应关系、激活顺序。

确认后修改轮数返回 `preparation_frozen`。相同版本重复确认返回同一 `selectionId`；已确认后携带不同版本确认返回 `settings_conflict`，不新增冻结记录。双标签页中 A 读到 40 轮（revision 2）、B 保存 3 轮（revision 3）时，A 用 revision 2 确认必须失败且没有冻结记录；A 重新读取 revision 3 后确认才冻结 3 轮。

**理由**：轮数是用户最容易在刷新后怀疑“有没有保存”的字段，必须由服务端算、服务端存、服务端读。冻结 JSON 让后续阶段不必重新聚合多张表，也不会因后续配置变化而改变已确认选择。

**备选方案**：

- 前端计算并只存最终轮数：否决，刷新后无法解释自动/手动来源，也无法防止四舍五入。
- 允许非整除时取整：否决，72 小时配置以外的值会静默改变用户选择。
- 确认后仍可修改：否决，后续阶段可能读到与用户确认时不同的轮数。

### 7. API 契约与错误映射

**决定**：新增以下同域接口：

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/projects/{projectId}/environment?build=<buildId>` | 读取 build 上下文、最终快照、推演问题与当前准备（若有） |
| `POST /api/projects/{projectId}/environment/preparations` | 普通开始：无准备返回 202；`preparing`/`ready` 返回 200 原准备；`failed`/`canceled` 返回 409 `preparation_retry_required` |
| `POST /api/projects/{projectId}/environment/preparations/{preparationId}/retry` | 仅失败或取消后显式重试：创建新的准备/job/run，返回 202 |
| `GET /api/projects/{projectId}/environment/preparations/{preparationId}` | 读取准备详情、已完成批次预览、最终角色、来源、平台、话题、顺序、轮数与冻结选择 |
| `PATCH /api/projects/{projectId}/environment/preparations/{preparationId}/configuration` | 保存轮数配置，携带 `settingsRevision` |
| `POST /api/projects/{projectId}/environment/preparations/{preparationId}/confirm` | 携带 `{settingsRevision}`，同事务核对版本并冻结；相同版本幂等 |

角色来源响应复用证据图谱的 `locator` 形状：PDF 为 `{kind:'pdf', pageNo, url}`，文本为 `{kind:'text', sourceStart, sourceEnd, url}`。角色响应分为 `mention`、`background`、`expressedTopics`、`involvedTopics` 与 `simulation`；每个材料字段带 `sources`，关系话题另带 `claimNature`（旧快照可为空并显示未知）。准备详情在 `preparing` 或失败/取消时返回 `previewRoles` 与 `previewOnly`；`ready` 时返回最终 `roles`、平台、话题、首发话题、顺序与冻结选择。

错误映射：

| 场景 | 状态 | 原因码 / 文案 |
| --- | --- | --- |
| 匿名 | 401 | 不访问准备数据 |
| 他人/不存在项目 | 404 | `项目不存在` |
| 他人/不存在 build | 404 | `图谱不存在` |
| 他人/不存在准备 | 404 | `准备不存在` |
| 开始请求遇到图谱未完成或没有最终快照 | 409 | `graph_not_ready` / `graph_snapshot_missing` |
| 无角色 | 422 | `no_role_candidates` |
| 普通开始遇到失败或取消 | 409 | `preparation_retry_required` |
| 非失败/取消准备调用重试 | 409 | `preparation_not_retryable` |
| 零时长 / 非法轮数 / 不能整除 | 422 | `duration_invalid` / `rounds_invalid` / `rounds_not_integral` |
| 未就绪确认 | 409 | `preparation_not_ready` |
| 已冻结修改 | 409 | `preparation_frozen` |
| 保存或确认携带过期设置版本 | 409 | `settings_conflict` |
| 模型不可用 / 输出不合法 | 任务失败 | `model_unavailable` / `invalid_model_output` |
| 数据库不可达 | 503 | 保留 Cookie，可重试 |

**理由**：现有前端已经按 401/404/503 与 `detail` 处理错误；沿用同域相对路径、项目归属先于资源归属和统一 404，可以避免新接口成为泄露他人项目的旁路。

**备选方案**：

- 新增独立事件流端点：否决，现有 job SSE 已满足游标、终态与鉴权语义。
- 用 200 空对象表示图谱未完成：否决，页面无法区分“没有准备”和“图谱不可用”。

### 8. 第二阶段页面与状态守卫

**决定**：新增 `RolesEnvironmentPanel`（可再拆角色列表/详情/配置子组件），由 `WorkbenchView` 在 `currentStage.id === 2` 时渲染，不再显示 `rolesTitle/rolesBody` 空态。面板职责：

- 读取 `route.query.build`；无值时调用上下文接口，拿到 `buildId` 后 `router.replace` 固定；`layout`、`lang` 与会话不变。
- 用 `buildId` 与 `preparationId` 守卫所有异步响应；切换 build/准备先清空旧角色、旧轮数、旧来源，再加载新数据。
- 用现有 `openJobStream()` 订阅准备的 `jobId`，事件只更新阶段、批次标识、计数与原因；收到预览批次事件后重新读取准备详情，不把事件当角色正文。
- 显示“图谱未完成 / 尚未准备 / 准备中 / 已就绪待确认 / 已确认 / 失败或取消”六态。`preparing` 时显示“准备中”和可展开的已完成批次；失败或取消时显示原因与重试，重试调用显式重试接口，不调用普通开始。
- 角色详情按“材料提及 / 材料未说明 / 表达话题 / 材料涉及的话题 / 模拟设定”展示，不把节点提及句写成背景或关注；来源跳转复用材料深链接：PDF 使用原件 `#page=<pageNo>`，文本使用原文视图与 UTF-16 偏移，并保留 `layout`、`lang`、`build`、`preparation`、`document`。
- 轮数表单只编辑输入，显示服务端返回的 `autoRounds`/`rounds`；保存成功后以服务端响应覆盖本地。确认按钮只在 `canContinue` 为 false 且 `phase=ready` 时可用，确认请求携带当前 `settingsRevision`；`settings_conflict` 时提示重新读取，不允许用旧值确认。确认成功后显示冻结选择与第三阶段入口。
- 新 build 完成时显示提示“已有更新图谱，本次准备仍绑定旧快照”，不自动切换角色。

`stages.ts` 只更新第二阶段 `hasDataInThisChange` 与必要文案；阶段顺序、slug、布局/语言参数与 `push`/`replace` 规则不变。英文文案与中文文案必须同时覆盖六态、角色类型、平台、轮数与错误码。

**理由**：现有工作台已经解决外壳、布局、语言与阶段深链接；第二阶段只需把真实数据状态接进同一外壳。用 `buildId`/`preparationId` 守卫与清空旧数据，能避免旧响应污染新准备。

**备选方案**：

- 把第二阶段做成独立路由页：否决，会破坏既有布局/语言/后退语义。
- 用前端定时器轮询生成角色：否决，进度与角色应来自 worker 事件和准备详情。

### 9. 事件、刷新、重连与接手

**决定**：准备任务复用 `/api/projects/{projectId}/jobs/{jobId}/events`。事件载荷只包含 `preparationId`、`buildId`、`snapshotId`、`step`、`stepKey`、`batchIndex`、`batchRoleIds`、`phase`、`eligibleRoles`、`profiledRoles`、`totalRoles`、`previewRoleCount` 与可选的 `rounds`、`reason`。`batchRoleIds` 只是角色标识，事件 MUST NOT 携带角色显示名、画像正文、材料正文、完整引文、来源地址、模型响应、密钥或平台凭据。

刷新与重连统一为“先详情、后事件”：先 GET 受鉴权的准备详情，取得 `preparationId`、`buildId`、当前 `phase`、已完成批次预览或最终结果；若已经是终态，直接以详情渲染并停止；否则再用 `Last-Event-ID` 优先、`after` 次之打开事件流补齐进度。收到 `profile` 批次事件后重新 GET 详情以取得该批正文；收到终态事件后再次 GET 详情。事件只负责唤醒与计数，不负责重建角色。

读取详情期间恰好收到终态事件时，以服务端详情为准：终态事件只触发一次重新读取；若详情已返回 `ready`、`failed` 或 `canceled`，关闭事件流，不再应用后续进度；若事件先到而详情仍是 `preparing`，保留事件计数并等待详情终态。相同 `preparationId` 的重复详情响应按 `phase` 与 `settingsRevision` 取较新者；不同 `preparationId` 的响应直接丢弃。事件缺口以连续游标重连，不以最大序号跳过。

worker 中断时，`jobs` 查询的 `awaitingTakeover` 与 `attempts` 继续表达状态；接手复用 `run_steps` 与已写角色预览，从第一个未提交步骤或未完成批次继续。准备失败/取消的终态写入同一事务，读取接口返回失败/取消原因与只读的未完成预览，不返回平台、话题、顺序或冻结选择。

**理由**：准备详情包含预览角色正文与最终结果，事件只带标识和计数；先读详情可以避免“事件到了但正文接口尚未提交”的空窗，也能覆盖详情读取期间恰好收到终态的情况。

**备选方案**：

- 事件携带角色对象：否决，会绕过准备详情的鉴权与来源校验，也会让重连重建角色。
- 先补事件再读详情：否决，终态事件到达时详情可能尚未稳定，容易把旧预览覆盖成最终结果。
- 前端轮询准备详情制造进度：否决，进度应由 worker 步骤与持久事件表达。

### 10. 模型适配、替身与零外呼

**决定**：新增 `agent_profile` 适配器，复用 `app/engines/model.py` 的 Chat Completions 传输、`User-Agent: EchoSim/0.1.0`、`response_format={"type":"json_object"}`、30 秒与最多 2 次实际 HTTP 请求预算、HTTP 状态分类与脱敏规则。输入 MUST 包含本次绑定的 `question`、`buildId`/`preparationId`、角色节点键、角色类型与材料事实摘要（不含完整材料正文）；模型只返回每个角色的模拟设定、额外初始话题、激活顺序、首发角色与首发话题。字段、角色键、范围、顺序与话题对应关系由服务端校验。每次真实或替身调用在 provider 边界累加到 `environment_preparations.provider_calls_real`/`provider_calls_double`，报告直接读取该逐准备值。

配置沿用业务建图的 provider 模式与 `allow_stub` 规则：默认未配置时 `model_unavailable`，显式 `stub` 且允许替身时才使用确定性替身；`app` 不接收模型密钥，只有需要真实生成的 `worker` 接收。替身 MUST 接收同一 `question`，按角色键与问题确定性生成模拟设定、顺序与话题，不访问网络。

**理由**：复用现有传输与预算可以避免第二套超时/错误分类；让输入只含摘要而不是完整材料，可以降低正文进入模型与日志的风险。默认关闭真实调用符合仓库既有规则。

**备选方案**：

- 复用 `evidence.graph.build` 的抽取规划器：否决，输出契约不同，会把角色生成混进建图。
- 在 `app` 内调用模型：否决，违反长任务与密钥边界。
- 缺配置时静默用替身：否决，会让真实运行看起来成功但实际是假数据。

### 11. 验收、报告与人工走查

**决定**：`scripts/verify.py` 注册 `ENVIRONMENT_CHANGE_ID = "2026-11-24-prepare-agent-environment"` 与 `environment_evidence`。专属场景使用运行时生成的原创 TXT/PDF 小材料，先用现有建图流程得到最终快照，再开始准备。替身生成至少三类角色（个人、群体、机构）与确定性模拟设定，并接收本次绑定的 `question`；报告记录中文快照筛选与三个反例、材料提及/关联/表达/模拟设定分层、第一批预览、普通开始/显式重试、72→40→3 轮、两页并发确认、冻结选择、接手、失败原因与 `calls.real=0`。

默认 `make verify` 只增加轻量断言：第二阶段深链接返回 `EchoSim` HTML、匿名准备接口 401、没有 build 时返回可区分原因；不运行准备 worker、不调用模型。Change 08 专属运行两次都必须通过，未知 `CHANGE` 仍退出 2。

人工走查使用作者私有项目中的私有材料，检查三类角色详情、PDF 落页、文本高亮、事实/假设标签、72→40 刷新读回、确认冻结、新图提示与失败/重试。私有 PDF、含正文截图、真实画像与模型原始输出 MUST 留在仓库外；`docs/acceptance/` 只由作者记录脱敏结论、SHA、任务标识与必要证据链接，不保存正文、截图、真实画像或模型原始输出。自动化只使用运行时生成的原创材料。若查阅仓库外的只读参考实现，只参考行为；仓库内工件使用 EchoSim 自己的术语，不写入外部项目名称、链接、路径、文案、提示词或示例资产。

**理由**：自动化需要可重复、零外呼；人工需要验证真实材料下的可读性与浏览器落页。两者继续分离，避免把离线通过写成真实模型或浏览器验收结论。

**备选方案**：

- 自动化使用作者私有材料或外部参考资产：否决，违反私有样本与术语边界。
- 自动化调用真实模型：否决，不可重复且违反默认零外呼。

### 12. Change 09 的读取契约

**决定**：Change 09 只读 `frozenSelection`，不重新读取当前活动 build 或重新筛选角色。契约字段固定为：

```json
{
  "selectionId": "uuid",
  "preparationId": "uuid",
  "projectId": "uuid",
  "buildId": "uuid",
  "snapshotId": "uuid",
  "question": "string",
  "settingsRevision": 3,
  "settings": {
    "durationHours": 72,
    "minutesPerRound": 60,
    "roundsMode": "manual",
    "manualRounds": 3,
    "rounds": 3
  },
  "roles": [
    {
      "roleId": "uuid",
      "nodeKey": "string",
      "displayName": "string",
      "roleType": "string",
      "mention": {"text": "string", "sources": ["source"]},
      "background": {"text": "string", "stated": true, "sources": ["source"]},
      "expressedTopics": [
        {"text": "string", "claimNature": "party_claim", "claimNatureKnown": true, "sources": ["source"]}
      ],
      "involvedTopics": [
        {"text": "string", "claimNature": null, "claimNatureKnown": false, "sources": ["source"]}
      ],
      "simulation": {
        "origin": "simulation",
        "motive": "string",
        "stance": "string",
        "activeHours": [9, 10, 11],
        "postingFrequency": 1.0,
        "responseDelayMinutes": 15,
        "influenceWeight": 1.0,
        "platformKeys": ["platform_a", "platform_b"],
        "topics": [{"text": "string"}]
      }
    }
  ],
  "platforms": [
    {"platformKey": "platform_a", "nativePlatform": "twitter"},
    {"platformKey": "platform_b", "nativePlatform": "reddit"}
  ],
  "initialTopics": [
    {"topicId": "t1", "text": "string", "origin": "question", "claimNature": null, "claimNatureKnown": false, "sources": []},
    {"topicId": "t2", "text": "string", "origin": "material", "claimNature": "media_report", "claimNatureKnown": true, "sources": ["source"]}
  ],
  "activation": {
    "firstSpeakerRoleIds": ["uuid"],
    "firstSpeakerTopics": [{"roleId": "uuid", "topicId": "t1"}],
    "order": ["uuid"]
  }
}
```

`platform_a/twitter` 与 `platform_b/reddit` 是平台映射；Change 06 的 `create_post`/`create_comment` 只是连通性探针动作，MUST NOT 被写入冻结选择作为两个平台此后唯一允许的动作。Change 09 自行决定实际动作。初始话题 MUST 保留 `origin` 与 `claimNature`（旧来源为空时 `claimNatureKnown=false`），首发角色 MUST 通过 `firstSpeakerTopics` 与具体话题对应。

Change 09 MUST 在 `frozenSelection` 为空时拒绝开始双平台运行，并显示“角色与环境尚未确认”。本变更不实现 Change 09 的执行接口，只保证冻结选择可读、稳定且与准备详情同源。

**理由**：冻结 JSON 是跨变更的最小稳定接口；如果 Change 09 重新聚合角色表或当前 build，就会重新引入“用户确认后配置又变了”的问题。

**备选方案**：

- Change 09 直接读角色表：否决，角色表可能被未来准备复用或清理，且没有“用户确认”语义。
- 用 URL 传角色与轮数：否决，不可信且会绕过服务端校验。

## Risks / Trade-offs

- **角色筛选规则可能误判中文/英文类型名** → 类型分类器同时识别中文显示类型与英文 PascalCase；`其他主体` 必须由来源原句明确描述为主体，且不能因关系起点就纳入；新增类型可通过测试夹具扩展，不在产品里写死名单。
- **模型批次多导致准备时间长** → 复用 worker 租约与心跳，批次写入草稿，接手复用；进度事件只报计数，不让页面等待单次长响应。
- **准备记录与角色表增多** → 只保留当前准备与冻结选择；未通过来源校验的草稿不对外返回，已校验批次可作为只读预览，失败/取消后不参与确认或后续阶段；后续可加清理任务，不在本变更删除历史。
- **确认后不能改轮数** → 这是有意的冻结语义；同一 build 的已确认准备不会被静默覆盖，用户在确认前完成 72/40/3 等配置。本变更不提供解冻入口，避免 Change 09 读到与确认时不同的选择。
- **新 build 完成时旧准备仍在页面** → 页面提示但不自动切换；用户显式切 build 才读取另一份准备，避免角色突变。
- **预览被误当成最终结果** → 预览只带 `preview_ready_at`，没有平台、话题、顺序与冻结选择；确认接口和第三阶段入口在 `preparing` 时不可用，测试断言预览不能确认。
- **两页同时编辑轮数** → 保存与确认都校验 `settingsRevision`；版本冲突不创建冻结记录，页面重新读取后再确认。
- **默认验收增加二阶段轻量断言可能拖慢默认流程** → 只做 HTML 与 401，不启动 worker；完整准备只在 Change 08 专属运行。
- **复盘时难以解释“角色为什么是这几个”** → 准备详情返回角色节点键、类型、来源与筛选原因摘要；测试断言事件/说法/建议/平台不出现。

## Migration Plan

1. 新增 `0011_agent_environment_preparation.sql`：扩展 `runs.kind` 约束，给 `graph_snapshots` 增加 `(build_id, id)` 唯一约束，创建准备、角色、角色来源、平台、话题、激活顺序与冻结选择表及索引。
2. 在空数据卷与已有 Change 01–08 数据卷上各执行一次迁移；旧 run、build、快照、材料与测试任务不变。
3. 先部署迁移与后端接口，再部署 worker 分派与前端面板；旧客户端不会调用新接口。
4. 回滚策略：只回退应用代码，不回退已执行的前向迁移；旧数据卷继续可读，准备表保留但不被旧代码使用。
5. 部署后先跑默认 `make verify`，再跑 `make verify CHANGE=2026-11-24-prepare-agent-environment` 两次，最后按需进行私有项目人工走查。
