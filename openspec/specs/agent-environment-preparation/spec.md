# agent-environment-preparation Specification

## Purpose
让项目所有者把一次已完成的证据图谱绑定成可审阅、可追溯、可冻结的角色与环境准备：角色只来自同一份最终快照，材料事实与模拟设定分开显示，轮数在确认后固定，供后续双平台运行按同一份选择读取。

## Requirements

### Requirement: build、最终快照与推演问题绑定

“准备角色与环境”SHALL 在读取上下文或开始准备时复用项目归属校验，并核对传入的 `buildId`。只有该 build 的项目归属正确、服务端 `canContinue=true`，且存在同一 build 的已发布最终快照（`complete=true`、`published=true`）时，才允许把该快照与 build 的推演问题绑定到本次准备。

准备记录 MUST 同时保存 `buildId`、`snapshotId` 与 `question`；角色节点、角色来源与后续冻结选择 MUST 引用同一 `snapshotId`，不得从另一个 build 或另一份快照补充角色、来源或计数。候选快照、部分快照与其他 build 的最终快照 MUST NOT 被当作本次绑定对象。

打开第二阶段时，地址带 `build` 参数 MUST 精确读取该 build；不带 `build` 时 MUST 先选择活动 build，否则选择最近 build，并由页面把解析出的 `build` 固定到地址中。图谱尚未完成、没有最终快照或 `canContinue=false` 时，接口 MUST 返回可区分的未就绪原因，不得创建准备记录或任务。

#### Scenario: 从图谱进入并固定 build

- **WHEN** 项目所有者从已完成的证据图谱点击“进入准备角色与环境”
- **THEN** 地址保留 `layout`、`lang` 与 `build`，第二阶段读取该 `buildId`
- **AND** 页面显示同一 build 的最终快照标识、推演问题与准备入口
- **AND** 后续准备记录只引用该 `snapshotId`

#### Scenario: 直接打开页面并固定解析结果

- **WHEN** 项目所有者直接打开 `/projects/<项目 ID>/workbench/roles-environment?layout=graph&lang=zh` 且存在多个 build
- **THEN** 服务端按“活动 build 优先、否则最近 build”返回一个 build
- **AND** 页面使用替换历史记录的方式把该 `buildId` 写入地址
- **AND** 刷新后仍读取同一 build，不会因默认选择变化而改读另一个 build

#### Scenario: 图谱未完成时不创建准备

- **WHEN** 项目所有者打开一个仍在等待索引、增量绘图、失败、取消或来源不完整的 build
- **THEN** 接口返回 `canPrepare=false` 与具体原因（如 `graph_not_ready`、`graph_snapshot_missing`）
- **AND** 页面说明证据图谱尚未完成并提供返回第一阶段的入口
- **AND** 数据库不新增准备、任务或 run 记录

#### Scenario: 打开旧 build

- **WHEN** 项目所有者使用 `?build=<旧 buildId>` 打开第二阶段，且该 build 的最终快照仍已发布
- **THEN** 页面显示旧 build 的问题、快照与准备结果，不回退到当前活动 build
- **AND** 旧 build 的角色、来源和冻结选择不被新 build 覆盖

#### Scenario: 准备期间又建了新图

- **WHEN** 一个准备已绑定旧 build，随后同一项目又完成了一次新 build
- **THEN** 旧准备仍读取旧 `buildId`、旧 `snapshotId` 与旧角色，不显示新图的角色或来源
- **AND** 页面提示已有更新的图谱，并说明当前准备仍绑定旧快照
- **AND** 用户为新图准备时使用新 build 创建另一份准备，两份准备的角色与来源不得交叉

#### Scenario: 快照不完整或不属于该 build

- **WHEN** build 返回的最近已发布快照仍为 `complete=false`，或快照的 `buildId` 与请求的 build 不一致
- **THEN** 服务端拒绝绑定并返回 `graph_not_ready` 或 `graph_snapshot_missing`
- **AND** 不创建准备记录，也不使用该快照的角色节点

### Requirement: 角色候选筛选

系统 SHALL 直接读取绑定 build 的已发布最终快照，按快照实际保存的 `nodeType`、`label`、`summary`、节点来源与关系来源筛选角色候选，不要求重新建图。候选 MUST 满足：`origin=material`；至少一条通过校验的节点来源（引文非空、偏移有效、材料属于本项目并属于本次快照）。

`nodeType` MUST 同时识别中文显示类型与英文 PascalCase。EchoSim 当前快照通常保存建图计划中的中文显示类型；旧快照可能保留英文类型或 `其他主体`。可参与主体包括 `个人`、`人物`、`群体`、`高校`、`机构`、`机构组织`、`部门`、`政府机构`、`法院`、`医疗机构`、`社会组织`、`媒体机构`、`新闻机构` 以及 `*Person`、`*User`、`*Group`、`*Community`、`*Organization`、`*Institution`、`*Company`、`*University`、`*Government`、`*MediaOutlet`、`*NewsAgency`。事件、事故、待核实说法、传言、建议、措施、话题、地点、平台渠道 MUST NOT 成为角色，包括 `事件`、`事故`、`待核实说法`、`传言`、`建议`、`措施`、`话题`、`地点`、`平台`、`社交媒体`、`传播平台`、`媒体平台` 以及 `*Event`、`*Incident`、`*Claim`、`*Allegation`、`*Rumor`、`*Recommendation`、`*Suggestion`、`*Measure`、`*Topic`、`*Location`、`*Platform`、`*Channel`。

媒体机构与平台渠道 MUST 分开：`媒体机构`、`新闻机构`、`MediaOutlet`、`NewsAgency` 可以发言与报道，是可参与主体；`平台`、`社交媒体`、`传播平台`、`媒体平台`、`Platform`、`Channel` 是环境渠道，不是角色。旧快照只有 `媒体` 标签时按媒体机构处理，只有明确含“平台”的类型才按平台渠道处理。

类型为 `其他主体`、`entity`、`Entity`、`Node`、空或无法识别时，MUST NOT 因为节点是关系起点就纳入；必须结合节点来源原句判断该名称是否被明确描述为人物、群体、组织、机构或媒体机构，且没有被描述为事件、说法、建议或平台。仍无法判定时不纳入，并在筛选原因中记录。`summary` 为空 MUST NOT 阻止筛选。

关系方向只提供上下文，不提供资格。只出现在关系终点的主体，只要类型是可参与主体且有材料依据，MUST 仍可入选；节点是关系起点但类型为事件、待核实说法或建议时 MUST NOT 入选。角色类型与数量 MUST 由绑定快照的合格主体决定，不得使用固定角色名单、固定类型白名单或固定数量。筛选结果为零时，开始准备 MUST 以 `no_role_candidates` 拒绝，且不创建准备、任务或 run。

#### Scenario: 中文快照筛选

- **WHEN** 绑定快照的 `summary` 均为 `null`，节点类型保存为中文显示类型，包含“张同学”（`个人`）、“某校学生群体”（`群体`）、“某大学”（`高校`，只作为“某报社报道某大学”的终点）、“某报社”（`媒体机构`）、“某争议事件”（`事件`，有指向地点的出边）、“某传闻（报告所称，未证实）”（`待核实说法`）与“某社交平台”（`平台`）
- **THEN** 角色列表包含张同学、某校学生群体、某大学与某报社
- **AND** 某争议事件、某传闻与某社交平台不进入角色列表
- **AND** 页面使用该已发布快照直接生成候选，不要求重新建图，也不因 `summary=null` 跳过任何角色
- **AND** 某大学虽然只出现在关系终点，仍作为机构角色出现；某争议事件虽然有出边，仍因类型为事件被排除

#### Scenario: 只出现在终点的机构仍可入选

- **WHEN** 某大学只有入边“某报社报道某大学”，没有作为任何关系的起点
- **THEN** 某大学仍被选为机构角色，身份来源指向报道原句
- **AND** 关系方向本身不阻止其入选

#### Scenario: 有出边的事件仍不入选

- **WHEN** 某争议事件的类型为 `事件`，并作为“发生于某地”的起点
- **THEN** 该事件不进入角色列表
- **AND** 不能因为它是关系起点就把它当成可发言主体

#### Scenario: 待核实说法不成为角色

- **WHEN** 某传闻节点的类型为 `待核实说法`，并出现在关系起点或终点
- **THEN** 该说法不进入角色列表，仍保持为待核实说法节点
- **AND** 与它相关的角色来源保留“报告所称/未证实”的限定

#### Scenario: 旧快照与英文类型仍可筛选

- **WHEN** 绑定的是旧快照，`nodeType` 为 `Person`、`Organization`、`Event` 或 `其他主体`，且 `summary` 为空
- **THEN** `Person` 与 `Organization` 仍可作为角色候选
- **AND** `Event` 不作为角色
- **AND** `其他主体` 只有来源原句明确描述为人物、群体、组织、机构或媒体机构时才可入选
- **AND** 不需要重新建图或回填旧快照

#### Scenario: 没有合格角色时明确拒绝

- **WHEN** 绑定快照只有事件、说法、建议、平台渠道或没有材料来源的节点
- **THEN** 开始准备返回 `no_role_candidates`，页面显示具体原因
- **AND** 数据库没有准备、job 或 run 记录，也没有可供后续读取的角色

#### Scenario: 换材料后角色随之变化

- **WHEN** 同一项目使用另一份原创材料完成新 build 并准备角色
- **THEN** 新准备的角色名称、类型或数量随新快照变化
- **AND** 旧准备的角色不被复用或混入新准备

### Requirement: 角色详情与材料来源

每个已发布角色 SHALL 包含：稳定的 `roleId`、来源节点键 `nodeKey`、显示名、角色类型、`mention`、`background`、`expressedTopics`、`involvedTopics` 与 `simulation`。系统 MUST 按材料实际支持的内容逐项标注，不得把节点引文直接写成角色背景或关注话题。

`mention` 与身份名称 MUST 来自快照节点及其节点来源。节点引文只能证明“材料提到该主体”；`background` 只有在关系支持原句明确描述该主体时才成立，否则 MUST 显示“材料未说明”。`expressedTopics` MUST 只包含关系起点是该角色、且支持原句能读出该角色表达、主张或回应了该话题的关系；`involvedTopics` MUST 用于角色只是关系起点或终点之一、原句只能证明材料把角色与该话题关联的情况，显示为“材料涉及的话题”。模型或替身为推演补充的关注点 MUST 放在 `simulation.topics` 并标记为“模拟设定”。没有背景或关注证据时 MUST 允许显示“材料未说明”，不得用模型内容补成材料事实。

角色来源 MUST 复用证据图谱的来源定位语义：PDF 返回物理页码与从 1 开始的 `pageNo`，页面可打开原件 `#page=<pageNo>`；文本返回解码后完整原文的 UTF-16 偏移（左闭右开）与原文视图地址。来源 MUST 同时包含材料标识、显示名、引文与定位信息，不得只给出材料名称或内部路径。关系来源 MUST 保留快照已有的 `claimNature`；旧快照该字段为空时 MUST 保留为未知并显示“来源性质未知”，不得默认成其他材料陈述、媒体报道或正式决定。`author_recommendation` 关系 MUST NOT 作为角色的 `expressedTopics` 或模拟设定依据，只作为环境背景保留其报告建议性质。

无法定位支持原句、来源不属于同一快照或引文与偏移不一致的角色 MUST NOT 发布。节点没有来源时不得成为候选；关系没有可核对支持句时不得生成表达话题或背景。

#### Scenario: 节点引文只证明被提及

- **WHEN** 某角色的节点来源只有“材料提到某大学”的提及句，没有任何关系支持句
- **THEN** 角色详情显示该主体被材料提及及来源
- **AND** 背景与关注话题显示“材料未说明”，不把提及句改写成角色背景或关注

#### Scenario: 表达话题需要归属原句

- **WHEN** 关系起点是该角色，支持原句同时出现该角色、动作与话题对象
- **THEN** 该话题进入 `expressedTopics`，并按关系 `claimNature` 显示来源性质
- **AND** 页面文案读成“该角色表达/主张”，而不是无主语的“材料涉及”

#### Scenario: 仅关联时写材料涉及的话题

- **WHEN** 角色只作为关系的终点，或支持原句不能证明该角色表达过该话题
- **THEN** 该话题进入 `involvedTopics`，显示“材料涉及的话题”
- **AND** 不得把它写成该角色表达过的关注

#### Scenario: 没有背景或关注证据时显示材料未说明

- **WHEN** 角色节点 `summary=null`，没有可用于背景的关系支持句，也没有可归属的表达话题
- **THEN** 页面显示“材料未说明”或“材料未说明关注话题”
- **AND** 模型补充的关注点只显示在“模拟设定”，不填补材料事实

#### Scenario: 旧边来源性质为空保留未知

- **WHEN** 角色话题来自旧快照中 `claimNature=null` 的关系
- **THEN** 响应保留 `claimNature=null` 与 `claimNatureKnown=false`
- **AND** 页面显示“来源性质未知”，不默认成其他材料陈述、媒体报道或正式决定

#### Scenario: 三类角色详情各有来源

- **WHEN** 用户依次打开个人、群体与机构角色的详情
- **THEN** 每个详情都显示材料提及、可用的背景/话题或“材料未说明”，以及至少一条材料来源
- **AND** 角色类型与显示名来自同一快照节点，不被前端改写

#### Scenario: PDF 角色来源跳到物理页

- **WHEN** 角色来源来自三页 PDF 的第 3 页
- **THEN** 详情显示 `pageNo=3` 与原件 `#page=3` 地址
- **AND** 点击后打开该材料并落在第 3 页，而不是按字符偏移猜测页码

#### Scenario: 文本角色来源回到原始段落

- **WHEN** 角色来源来自 Markdown 或 TXT 的某一段
- **THEN** 详情显示 `sourceStart`/`sourceEnd` 与原文视图地址
- **AND** 打开原文后按 UTF-16 偏移高亮同一段文字

#### Scenario: 来源缺失或错配时不发布

- **WHEN** 某个候选角色没有任何有效节点来源，或关系来源指向另一份快照/另一份材料
- **THEN** 准备以 `role_source_missing` 或 `role_source_mismatch` 失败
- **AND** 不发布该角色，也不把无来源字段显示为材料事实

### Requirement: 材料事实与模拟设定分离

角色中的身份、背景、关注话题与来源性质 SHALL 属于材料事实；动机、立场、活跃时段、发言频率、响应延迟、影响力权重与平台参与偏好 SHALL 属于模拟设定。模拟设定 MUST 以 `origin=simulation` 标记，页面 MUST 用“模拟设定”或等价标签与材料事实区分。

模型 MUST NOT 改写、补充或删除材料事实，也不得把模拟设定写成材料来源。若模型输出引用不存在的角色、节点、来源或材料，服务端 MUST 以 `invalid_model_output` 拒绝发布。页面 MUST NOT 把材料引文与模型推断合并成一段无来源的“人设”。

#### Scenario: 同一角色的事实与假设可分别识别

- **WHEN** 用户打开一个角色详情
- **THEN** 身份、背景与话题显示材料来源与来源性质
- **AND** 动机、立场与活跃时段显示“模拟设定”，不显示材料来源
- **AND** 两者不会以同一视觉样式或同一字段呈现

#### Scenario: 转述材料不被升级为角色动机

- **WHEN** 材料中只有“某方称其反对该安排”的转述，模型据此生成角色立场
- **THEN** 材料侧仍显示“当事方主张”的转述内容
- **AND** 角色立场作为模拟设定显示，不写成材料已证实事实

#### Scenario: 模型不能覆盖材料事实

- **WHEN** 模型输出试图修改角色显示名、身份来源或材料话题
- **THEN** 服务端拒绝该输出并进入 `invalid_model_output`
- **AND** 不发布被改写的角色或来源

### Requirement: 准备任务复用 worker、租约、分批预览与持久事件

“准备角色与环境”SHALL 作为新的 run 类型 `agent.environment.prepare` 进入现有 PostgreSQL 队列，由独立 `worker` 领取执行；`app` MUST NOT 在 HTTP 请求内调用模型或生成长画像。一次准备 MUST 固定为 `bind`、`select`、`profile`、`compose`、`publish` 五步，并复用现有 `run_steps`、`jobs.next_step`、租约令牌、数据库实际时钟、旧令牌拒绝、`attempts` 与 `awaitingTakeover` 语义。

每个步骤 MUST 在“锁定任务行、验证租约、写步骤副作用、写准备状态、追加事件、推进步号”的同一事务内提交。`profile` 步 MUST 按稳定批次生成；每批角色通过来源校验后写入 `preview_ready_at`，准备详情可返回已完成的 `previewRoles`，页面标明“准备中”。SSE 事件 MUST 只传准备/build/快照/批次标识、角色标识、计数与原因，不传角色正文、材料正文、引文或来源地址；正文 MUST 由受鉴权的准备接口读取。刷新 MUST 从准备详情恢复已完成批次；worker 接手 MUST 复用已 `preview_ready_at` 的角色，跳过已完成批次，不重复生成已提交批次。

预览角色 MUST NOT 用于确认或启动第三阶段；整套最终角色、平台、话题、首发话题与激活顺序 MUST 仍在第 5 步 `publish` 的单个事务内发布。失败或取消后，准备详情 MAY 返回只读的未完成预览，但 MUST 标记 `previewOnly=true`、`canContinue=false`，且没有平台、话题、顺序或冻结选择；页面显示失败/取消原因与重试入口。

普通开始与重试 MUST 分开：同一个 build 没有既有准备时，普通开始 MUST 返回 202 并创建准备、job 与 run；遇到 `preparing` 或 `ready` 时 MUST 返回 200 与同一 `preparationId`、`jobId`、`runId`，不新增记录；遇到 `failed` 或 `canceled` 时 MUST 返回 409 `preparation_retry_required`，不得自动创建新准备。用户显式重试失败或取消的准备时 MUST 返回 202 并创建新的准备、job 与 run；旧准备保持其终态与预览。worker 接手 MUST 保留原 `preparationId`、`jobId`、`runId`，不得新建身份。

准备任务 MUST NOT 出现在测试任务面板、测试副作用计数或测试日志中。

#### Scenario: 准备任务执行五步

- **WHEN** 项目所有者对一个可继续的 build 开始准备
- **THEN** 数据库新增 `runs.kind=agent.environment.prepare` 的 run、一个 job、一个准备记录
- **AND** `run_steps` 依次记录 `bind`、`select`、`profile`、`compose`、`publish`
- **AND** 任务先为 `queued`，由 worker 领取后才变为 `running`

#### Scenario: 普通开始复用已有准备

- **WHEN** 同一 build 已有 `preparing` 或 `ready` 准备，用户再次点击普通开始
- **THEN** 响应为 200，返回原 `preparationId`、`jobId`、`runId`
- **AND** 数据库不新增第二组准备、job 或 run

#### Scenario: 失败或取消后显式重试

- **WHEN** 最近准备为 `failed` 或 `canceled`，用户点击重试
- **THEN** 服务端返回 202，并创建新的准备、job 与 run
- **AND** 普通开始此时返回 `preparation_retry_required`，不会静默创建新准备
- **AND** 旧准备的终态、事件与未完成预览保持可诊断

#### Scenario: 第一批画像可预览

- **WHEN** 准备至少有两批角色，第一批已完成并通过来源校验，第二批仍在生成
- **THEN** 页面显示“准备中”，项目所有者可以展开第一批角色及其来源
- **AND** 第二批仍显示生成中，未完成角色不出现在第一批预览
- **AND** SSE 只携带批次标识、角色标识与计数，角色正文通过准备详情读取
- **AND** 确认、冻结与“进入双平台运行”仍不可用，直到第 5 步原子发布完成

#### Scenario: 刷新恢复已完成批次

- **WHEN** 用户在第一批预览后刷新页面
- **THEN** 准备详情返回同一批已完成预览与批次计数
- **AND** 页面不重复请求已提交批次，也不把预览当成最终角色
- **AND** worker 接手后只补未完成批次，不重复生成已提交批次

#### Scenario: 刷新与重连先读详情再补事件

- **WHEN** 用户在收到 `seq=2` 后刷新或断线，再用游标重连
- **THEN** 页面先读取准备详情，取得当前 `phase`、已完成批次与预览角色
- **AND** 随后从游标之后补齐事件，事件只用于更新计数与触发再次读取
- **AND** 已显示的事件不重复，预览角色不从事件载荷重建

#### Scenario: 读取详情期间恰好收到终态

- **WHEN** 准备详情请求尚未返回时收到 `completed`、`failed` 或 `canceled` 事件
- **THEN** 客户端以准备详情返回的 `phase` 为准，终态事件只触发一次重新读取
- **AND** 若详情已进入终态，关闭事件流，不再应用后续进度事件
- **AND** 若详情仍为 `preparing`，保留事件计数并等待下一次详情终态，不提前显示最终角色

#### Scenario: worker 中断后接手

- **WHEN** 准备已提交第一批画像后 worker 停止，租约过期后由新 worker 接手
- **THEN** `preparationId`、`jobId`、`runId` 不变，`attempts` 大于 1，页面显示等待接手
- **AND** 接手从 `profile` 的未完成批次继续，已提交批次被复用
- **AND** 不出现重复角色、重复步骤或重复事件

#### Scenario: 生成失败不发布半成品

- **WHEN** 模型不可用、输出不合法或角色来源校验失败
- **THEN** 准备进入 `failed`，终态事件包含稳定原因码与可读说明
- **AND** 准备详情最多返回只读的未完成预览，`canContinue=false`
- **AND** 不返回平台、初始话题、激活顺序或冻结选择
- **AND** 用户可显式重试，旧失败记录保持可诊断

#### Scenario: 取消不发布

- **WHEN** 用户在准备进入终态前取消任务
- **THEN** 准备进入 `canceled`，不发布最终角色、平台、话题、顺序或冻结选择
- **AND** 已完成的预览只读显示为未完成预览，确认与第三阶段入口不可用

#### Scenario: 准备任务不混入测试面板

- **WHEN** 同一项目同时存在测试任务与准备任务
- **THEN** 任务列表只返回测试任务，不出现准备的 `jobId`、`runId`、角色或轮数
- **AND** 准备事件不写入测试任务日志或副作用计数

### Requirement: 服务端就绪判定与原子发布

准备是否就绪 MUST 只由服务端判定。`phase=ready` MUST 要求：至少一个最终角色；每个角色有有效材料来源、材料提及与完整模拟设定；`expressedTopics`、`involvedTopics` 与 `simulation.topics` 按材料实际支持程度分开；激活顺序包含每个角色恰好一次；首发角色是激活顺序的前缀且至少一个；首发角色通过 `firstSpeakerTopics` 与具体初始话题对应；两个平台映射都存在且 `platform_a/twitter`、`platform_b/reddit` 正确；至少一个来自 build 推演问题的初始话题；初始话题保留 `origin` 与 `claimNature`（旧来源为空时保留未知）；轮数通过校验；所有角色、来源、话题、平台与顺序来自同一 `preparationId` 与 `snapshotId`。

第 5 步 `publish` MUST 在单个事务内发布最终角色、角色来源、平台映射、初始话题、首发话题、激活顺序、`phase=ready`、完成事件与任务 `succeeded`。已完成批次的 `previewRoles` 可以在准备中读取，但平台、初始话题、首发话题、激活顺序与冻结选择 MUST NOT 在发布事务提交前对外可见；预览角色 MUST NOT 被标记为最终角色。前端 MUST 使用服务端 `phase`、`canContinue` 与原因码，不得根据角色数量、动画或本地状态推断就绪。

#### Scenario: 完整准备才进入 ready

- **WHEN** 五步全部成功且角色、来源、平台、话题、首发话题、顺序与轮数都通过校验
- **THEN** 读取接口返回 `phase=ready` 与最终角色、平台映射、初始话题、首发话题和激活顺序
- **AND** 角色、来源、话题、平台与顺序的 `preparationId`、`snapshotId` 一致

#### Scenario: 任一必需项缺失不进入 ready

- **WHEN** 角色无来源、背景/话题归属证据不足、激活顺序缺角色或重复角色、平台映射错误、初始话题为空、首发话题缺失或轮数非法
- **THEN** 准备不进入 `ready`，返回对应原因码
- **AND** 读取接口不返回最终平台、话题、顺序或冻结选择

#### Scenario: 发布事务原子可见

- **WHEN** 第 5 步发布事务提交
- **THEN** 最终角色、平台映射、初始话题、首发话题、激活顺序、`phase=ready`、完成事件与任务 `succeeded` 同时可见
- **AND** 提交前只能看到已完成批次的只读预览，不能看到平台、话题、顺序或冻结选择

#### Scenario: 预览不能用于确认或启动

- **WHEN** 页面正在展示 `previewRoles`
- **THEN** 确认按钮与“进入双平台运行”均不可用
- **AND** 服务端确认接口返回 `preparation_not_ready`，不创建冻结选择

#### Scenario: 前端不自行判定可继续

- **WHEN** 页面收到角色数量或进度事件
- **THEN** “进入双平台运行”只使用服务端确认后的 `canContinue`
- **AND** 未确认或未就绪时入口禁用并显示服务端原因

### Requirement: 自动与手动轮数

准备 SHALL 保存模拟总时长、每轮分钟数、轮数模式、手动轮数与生效轮数。默认 `durationHours=72`、`minutesPerRound=60`、`roundsMode=auto`，因此生效轮数为 72。自动轮数 MUST 按 `durationHours * 60 / minutesPerRound` 计算，结果必须是大于等于 1 的整数；不能整除时 MUST 以 `rounds_not_integral` 拒绝，不得静默取整或截断。

手动模式 MUST 使用 `manualRounds` 作为生效轮数。`durationHours` MUST 为 1–720 的整数，`minutesPerRound` MUST 为 1–1440 的整数，`manualRounds` MUST 为 1–1000 的整数。零、负数、小数、越界或缺失手动轮数 MUST 以 `duration_invalid` 或 `rounds_invalid` 拒绝。`manualRounds=3` MUST 被允许。

保存轮数 MUST 持久化到服务端并递增 `settingsRevision`；刷新页面后 MUST 从服务端读回同一模式、手动值与生效轮数。保存请求携带过期 `settingsRevision` 时 MUST 返回 `settings_conflict`，不得静默覆盖。自动模式 MUST 在响应中同时返回计算出的自动轮数与当前生效轮数，使用户能看出 72 小时的默认结果为 72。

#### Scenario: 默认 72 小时显示 72 轮

- **WHEN** 用户首次打开一个可准备的 build 且未修改时间配置
- **THEN** 服务端返回 `durationHours=72`、`minutesPerRound=60`、`roundsMode=auto` 与 `rounds=72`
- **AND** 页面显示 72 轮，不需要前端自行计算

#### Scenario: 手动改为 40 后刷新仍为 40

- **WHEN** 用户切换为手动模式、保存 `manualRounds=40`，随后刷新页面
- **THEN** 保存响应与服务端读取都返回 `roundsMode=manual`、`manualRounds=40`、`rounds=40`
- **AND** 页面显示 40，不回退到自动 72

#### Scenario: 允许 3 轮小规模配置

- **WHEN** 用户在确认前保存 `manualRounds=3`
- **THEN** 服务端接受并持久化 3 轮
- **AND** 确认后冻结选择中的轮数为 3

#### Scenario: 零时长被拒绝

- **WHEN** 用户提交 `durationHours=0`
- **THEN** 服务端返回 422 与 `duration_invalid`
- **AND** 页面显示具体原因，不保存该配置，也不允许确认

#### Scenario: 非法轮数被拒绝

- **WHEN** 用户提交手动轮数为 0、负数、小数或超过 1000
- **THEN** 服务端返回 422 与 `rounds_invalid`
- **AND** 保留上一次有效配置，不把非法值写入准备

#### Scenario: 自动轮数不能整除时拒绝

- **WHEN** 用户提交 `durationHours=2`、`minutesPerRound=90`
- **THEN** 服务端返回 422 与 `rounds_not_integral`
- **AND** 不保存四舍五入或截断后的轮数

### Requirement: 确认冻结与后续阶段读取契约

用户确认后，系统 SHALL 在单个事务内核对用户最后读到的 `settingsRevision`、重新校验准备就绪条件、轮数配置与角色来源，并创建不可变的冻结选择。确认请求 MUST 携带 `settingsRevision`；服务端 MUST 锁定准备行比较当前版本，不一致时返回 `settings_conflict`，且 MUST NOT 创建冻结记录或修改准备。

冻结选择 MUST 包含 `selectionId`、`preparationId`、`buildId`、`snapshotId`、`question`、`settingsRevision`、轮数配置、角色（材料提及、背景、表达话题、关联话题与模拟设定）、平台映射、初始话题及其 `origin`/`claimNature`、首发角色与话题的对应关系、激活顺序，使后续阶段无需重新聚合当前图谱或当前准备草稿。

冻结选择中的 `platform_a/twitter` 与 `platform_b/reddit` 是平台映射；Change 06 的 `create_post`/`create_comment` 只是连通性探针动作，MUST NOT 被写成两个平台此后唯一允许的动作，也 MUST NOT 出现在冻结选择中作为动作限制。初始话题 MUST 保留来源性质：`origin=question` 表示 build 问题，`origin=material` 表示材料话题并带来源与 `claimNature`，`origin=simulation` 表示模型补充；旧关系 `claimNature` 为空时 MUST 保留为未知。`activation.firstSpeakerTopics` MUST 明确每个首发角色对应的话题。

确认 MUST 是幂等的：相同 `settingsRevision` 重复确认返回同一个冻结选择，不新增第二份。确认前保存的轮数只属于草稿；确认后修改配置 MUST 返回 `preparation_frozen`，不得改写已冻结选择。未就绪、轮数非法或来源缺失时 MUST 拒绝确认并返回具体原因。后续“双平台运行”MUST 通过准备读取接口取得 `frozenSelection`，并只使用其中数据；Change 09 不得从当前活动 build、最新图谱或前端本地状态重新拼装角色、轮数、话题或平台动作。

#### Scenario: 确认创建冻结选择

- **WHEN** 准备已 `ready`、轮数为 40、`settingsRevision=2` 且用户点击确认
- **THEN** 服务端创建一份包含 40 轮、`question`、全部角色、平台映射、初始话题来源性质、首发话题与激活顺序的冻结选择
- **AND** 准备返回 `canContinue=true` 与 `selectionId`
- **AND** 冻结选择中的 `snapshotId` 与准备绑定快照一致

#### Scenario: 重复确认幂等

- **WHEN** 用户用同一 `settingsRevision=2` 对同一准备再次确认
- **THEN** 返回同一个 `selectionId` 与同一份冻结内容
- **AND** 不新增第二条冻结记录，不改变确认时间

#### Scenario: 两页并发确认

- **WHEN** A 页面已读到 revision 2 的手动 40 轮，B 页面保存手动 3 轮使服务端版本变为 revision 3，随后 A 携带 revision 2 点击确认
- **THEN** 服务端返回 409 `settings_conflict`
- **AND** 不创建任何冻结记录，准备仍保持未确认且轮数为 B 保存的 3 轮
- **AND** A 重新读取准备详情，看到 revision 3 与 3 轮后再次确认，才创建冻结选择并冻结 3 轮
- **AND** 之后用 revision 3 重复确认返回同一 `selectionId`

#### Scenario: 冻结后不能再改轮数

- **WHEN** 用户尝试在确认后保存新的轮数
- **THEN** 服务端返回 `preparation_frozen`
- **AND** 冻结选择与准备读取结果保持不变

#### Scenario: 后续阶段只读冻结选择

- **WHEN** “双平台运行”读取一个已确认准备
- **THEN** 它取得 `frozenSelection` 中的 `question`、`buildId`、`snapshotId`、角色、轮数、平台映射、初始话题来源性质、首发话题与激活顺序
- **AND** 即使项目之后又有新 build，也不改用新图的角色、问题或轮数
- **AND** 它不把 Change 06 的 `create_post`/`create_comment` 当成两个平台唯一允许的动作

#### Scenario: 未确认不能进入后续阶段

- **WHEN** 准备已生成最终角色但尚未确认
- **THEN** `canContinue=false`，页面说明需要确认
- **AND** 后续阶段入口禁用，不创建双平台运行

### Requirement: 环境配置与工作台展示

第二阶段工作台 SHALL 展示真实准备状态：尚未准备、准备中（含可展开的已完成批次）、已就绪待确认、已确认、失败与取消。准备中 MUST 显示“准备中”和已完成批次；失败或取消 MUST 显示原因与显式重试入口。准备结果展示角色列表与类型、角色详情、活跃时段、两个平台映射、初始话题及其来源性质、首发角色与话题对应关系、激活顺序、生效轮数与确认状态。

两个平台 MUST 使用稳定别名 `platform_a` 与 `platform_b`，并分别映射到原生 `twitter` 与 `reddit`；Change 06 的 `create_post`/`create_comment` 是连通性探针动作，MUST NOT 在工作台或冻结选择中写成两个平台此后唯一允许的动作。本变更只准备配置，不执行双平台动作。

活跃时段 MUST 为 0–23 的整数集合，至少一项，按升序显示。初始话题 MUST 至少包含 build 推演问题；材料话题 MUST 带来源与 `claimNature`，旧来源为空时显示“来源性质未知”；模型补充话题 MUST 标记为模拟设定。激活顺序 MUST 覆盖所有角色且不重复，首发角色 MUST 是顺序前缀，并通过 `firstSpeakerTopics` 与具体话题对应。

页面 MUST 用 `buildId` 与 `preparationId` 守卫丢弃迟到响应；切换 build 或准备时 MUST 先清空旧角色、旧轮数与旧来源，再读取新上下文。页面 MUST 提供普通开始、失败/取消后的显式重试、轮数编辑与保存、确认、来源跳转以及返回第一阶段的入口。普通开始在 `preparing`/`ready` 时显示原准备，不重复创建；失败/取消后只显示重试入口。确认 MUST 携带当前 `settingsRevision`；收到 `settings_conflict` 时 MUST 提示重新读取，不得用旧版本继续确认。英文界面 MUST 使用同一数据的英文标签，不翻译项目名、阶段 slug、角色显示名或材料名。

#### Scenario: 展示角色、活跃时段与平台映射

- **WHEN** 准备已 `ready`
- **THEN** 页面按类型显示角色及数量、每个角色的活跃时段与模拟设定标签
- **AND** 显示 `platform_a`/`platform_b` 的原生平台映射与启用状态
- **AND** 不把 `create_post`/`create_comment` 显示成平台唯一允许动作
- **AND** 显示初始话题及来源性质、首发角色、首发话题与激活顺序

#### Scenario: 角色详情与来源跳转

- **WHEN** 用户点选一个角色
- **THEN** 页面分别显示材料提及、材料未说明、表达话题、材料涉及的话题与模拟设定
- **AND** 点击 PDF 来源落到对应物理页，点击文本来源回到原文高亮区间
- **AND** 跳转保留 `layout`、`lang`、`build`、`preparation` 与 `document`

#### Scenario: 准备中的第一批可展开

- **WHEN** 准备正在生成第二批，第一批已通过来源校验
- **THEN** 页面显示“准备中”，第一批角色可展开且带来源
- **AND** 确认与“进入双平台运行”不可用
- **AND** 第二批未完成角色不显示为已完成

#### Scenario: 失败或取消后预览只读

- **WHEN** 准备失败或被取消，且已有已完成批次
- **THEN** 页面显示失败或取消原因与重试入口
- **AND** 已完成批次只作为“未完成预览”只读显示
- **AND** 不显示平台、初始话题、激活顺序、冻结选择或可用的第三阶段入口

#### Scenario: 普通开始与重试入口不混用

- **WHEN** 准备为 `preparing` 或 `ready`，另一时刻为 `failed` 或 `canceled`
- **THEN** 第一种情况页面显示原准备状态，不显示新建准备按钮
- **AND** 第二种情况页面显示重试入口，普通开始不会静默创建新准备
- **AND** 重试成功后页面切换到新的 `preparationId`

#### Scenario: 切换准备不混入旧角色

- **WHEN** 用户从旧 build 的准备切换到新 build 的准备
- **THEN** 页面先清空旧角色、旧轮数与旧来源，再显示新准备
- **AND** 旧准备迟到的响应因 `buildId` 或 `preparationId` 不匹配被丢弃

#### Scenario: 英文界面覆盖第二阶段

- **WHEN** 用户以 `lang=en` 打开已就绪准备
- **THEN** 角色类型、状态、平台、轮数与错误说明显示英文
- **AND** 项目名、阶段 slug、角色显示名与材料名保持原值

### Requirement: 访问边界与错误恢复

准备上下文、开始、详情、轮数保存与确认接口 MUST 先鉴权、再项目归属、再 build/准备归属。匿名请求 MUST 立即返回 401 且不访问数据库；他人项目、不存在项目与非法项目 ID MUST 返回相同的 404 `项目不存在`；他人 build 与不存在 build MUST 返回相同的 `图谱不存在`；他人准备与不存在准备 MUST 返回相同的 `准备不存在`。

图谱未完成返回 409 与 `graph_not_ready`；普通开始遇到失败或取消返回 409 与 `preparation_retry_required`；非失败/取消准备调用重试返回 409 与 `preparation_not_retryable`；准备未就绪时确认返回 409 与 `preparation_not_ready`；确认版本不一致返回 409 与 `settings_conflict`；无效轮数返回 422；已冻结修改返回 409 与 `preparation_frozen`；数据库不可达返回 503 且不清除会话 Cookie。错误响应 MUST 只包含中文说明、稳定原因码与可恢复动作，不得包含材料正文、角色画像正文、模型响应、密钥、内部路径或完整来源内容。

生成失败或取消时页面 MUST 显示原因、失败步骤与显式重试入口；重试 MUST 创建新的准备与新的 run，不覆盖旧终态记录。已完成预览只能只读显示，不得用于确认或启动第三阶段。模型不可用时 MUST 显示 `model_unavailable`，不得显示空角色为“准备完成”。服务暂时不可用与没有角色、没有准备 MUST 使用不同文案。

#### Scenario: 匿名访问立即拒绝

- **WHEN** 未携带会话 Cookie 的请求访问准备上下文、开始或详情
- **THEN** 响应为 401，且服务端不查询准备、角色或来源表
- **AND** 不返回任何 build、快照、角色或材料信息

#### Scenario: 他人项目与资源不存在等价

- **WHEN** 用户 A 使用 B 的项目、build 或准备标识访问第二阶段
- **THEN** 分别返回 404 `项目不存在`、`图谱不存在` 或 `准备不存在`
- **AND** 与不存在标识的响应一致，不泄露资源是否存在

#### Scenario: 模型不可用可恢复

- **WHEN** 准备在模型步骤因认证、额度、网络或超时失败
- **THEN** 准备进入 `failed`，原因码为 `model_unavailable`
- **AND** 页面显示失败原因与重试入口，仍有已完成批次时只读显示为未完成预览
- **AND** 重试创建新的准备与任务，旧记录仍可诊断

#### Scenario: 无效配置阻止进入后续阶段

- **WHEN** 轮数或时间配置无效
- **THEN** 页面显示具体原因并保持 `canContinue=false`
- **AND** 确认与“进入双平台运行”均不可用

#### Scenario: 确认版本冲突不创建冻结记录

- **WHEN** 用户确认时携带的 `settingsRevision` 与准备当前版本不一致
- **THEN** 服务端返回 409 `settings_conflict`
- **AND** 不创建冻结选择，不改变准备轮数与确认状态
- **AND** 页面提示重新读取后再确认

#### Scenario: 服务不可用与空准备可区分

- **WHEN** 数据库不可达导致读取返回 503，另一时刻项目只是尚未开始准备
- **THEN** 第一种情况显示“服务暂时不可用”并提供重试，保留会话
- **AND** 第二种情况显示“尚未准备角色与环境”与开始入口

### Requirement: 模型调用边界与默认零外呼

角色画像的模拟设定与初始话题生成 SHALL 只发生在独立 `worker` 进程；`app` MUST NOT 持有模型密钥、在请求内调用模型或在 HTTP 路径生成画像。真实模型与测试替身 MUST 接收本次绑定的 `question`，使模拟设定与关注点围绕同一推演问题；冻结选择 MUST 保存该 `question`。模型调用 MUST 复用现有 Chat Completions 兼容传输、User-Agent、超时与重试预算、JSON 对象输出与错误分类，不得新增第二套外呼入口或把密钥写入日志、事件、报告或响应。准备记录 MUST 在 provider 调用边界持久化真实与替身调用计数，报告不得用角色数或步骤数推算调用次数。

默认 `make up`、默认 `make verify` 与 CI MUST NOT 发起真实模型调用。准备任务 MUST 使用测试替身完成离线验收；只有显式启用替身开关时才可使用，未启用时不得静默回退到真实服务。真实模型只在项目所有者显式配置后由 worker 调用；作者的私有样例只在私有项目中人工走查，且不得把私有样本、文件名、路径、正文、截图或角色结果写入仓库、日志或报告。

#### Scenario: 默认验收零真实外呼

- **WHEN** 运行默认 `make verify`
- **THEN** 不创建真实模型请求，报告中的准备真实调用计数为 0
- **AND** 既有健康、会话、材料、任务、图谱与测试任务场景全部保留

#### Scenario: Change 08 使用测试替身

- **WHEN** 运行 `make verify CHANGE=2026-11-24-prepare-agent-environment`
- **THEN** 准备任务由替身生成确定性的角色模拟设定
- **AND** 报告记录 `calls.real=0`、替身调用次数大于 0 与所用准备标识
- **AND** 报告与日志不包含材料正文、完整画像、密钥或外部服务响应

#### Scenario: 未显式启用替身时不外呼

- **WHEN** 准备任务需要模型但替身开关未启用且真实配置缺失
- **THEN** 任务以 `model_unavailable` 失败
- **AND** 不发起任何外部请求，也不发布角色

#### Scenario: 私有样例只用于人工走查

- **WHEN** 作者在私有项目中用私有材料走查角色详情、来源与轮数
- **THEN** 私有 PDF、含正文截图、真实画像与模型原始输出留在仓库外
- **AND** `docs/acceptance/` 只记录脱敏结论、SHA、任务标识与必要证据链接，不保存正文、截图、真实画像或模型原始输出
- **AND** 该材料、角色与记录不进入自动化夹具、日志、CI 产物或报告
- **AND** 自动化只使用运行时生成的原创微型材料
