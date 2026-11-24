# Tasks

## 1. 数据模型与前向迁移

- [x] 1.1 新增 `app/migrations/0005_evidence_graph_builds.sql`：扩展 `runs.kind` 加入 `evidence.graph.build`，新增 `project_graph_bindings`（项目主键、创建幂等键唯一、稳定 `name`/`description`/`time_zone`、服务端 `graph_id` 唯一可空、状态、时间戳）、`graph_builds`、`graph_build_documents`、`graph_episodes`、`graph_snapshots`（含 `published`）、`graph_nodes`、`graph_edges`、`graph_evidence` 及索引；验证方式是在空数据卷与已有 Change 01–06 数据卷上各启动一次，迁移只执行一次且旧表数据不变
- [x] 1.2 为迁移补充约束测试：项目绑定唯一、创建幂等键唯一、`(build_id, episode_key)` 与 `idempotency_key` 唯一、`(build_id, revision)` 唯一、快照内节点/边主键唯一、来源必须关联存在的 `(document_id, chunk_index)`；验证方式是 `.venv/bin/python -m unittest tests.test_migrate` 覆盖重复迁移与非法插入
- [x] 1.3 新增建图持久化服务：创建 build/run/job、按项目读取默认最近 build 或指定 build、按版本读取已发布快照、记录 episode 状态与计数、限制节点/边/来源数量；验证方式是服务层单元测试覆盖创建、默认最近 build、按 ID 旧 build、活动 build 优先、版本单调、上限拒绝
- [x] 1.4 为快照可见性与保留策略增加服务与测试：候选快照 `published=false` 不可读取；部分快照可发布；完成后保留最终快照与最近若干部分快照并清理更旧部分；验证方式是构造候选/已发布/完成快照，断言读取接口只返回已发布版本，最终快照与最近版本仍可读
- [x] 1.5 为项目图谱绑定增加持久化服务：先提交绑定记录再调用外部；已有 `graph_id` 直接复用；空 `graph_id` 用原幂等键与稳定参数重试；返回空 UUID 或与已保存 UUID 不一致时拒绝覆盖；验证方式是数据库测试覆盖首次、已绑定、未绑定、UUID 不一致四种状态

## 2. 引擎契约、Provider 与 worker 执行

- [x] 2.1 新增 `app/requirements-worker-graph.txt`，只锁定 `zep-cloud==4.1.0` 与必要直接依赖；在 `Dockerfile` 增加从 runtime 派生的 worker 构建目标，只安装该文件；验证方式是在目标 Linux aarch64、Python 3.11 容器中完成解析、安装、导入与最小初始化，且 `app` 镜像不包含 Zep/camel，smoke `engine` 依赖不变
- [x] 2.2 在 `app/config.py` 增加业务建图配置：模型/图谱 provider、允许替身开关、5 份材料/200 节点/400 边/5 条来源上限、问题长度、批次大小、索引/处理预算；在 `.env.example` 增加同名占位值；验证方式是 `tests/test_engine_config_consistency.py` 断言配置名与默认值一致，占位值解析为未配置
- [x] 2.3 新增业务建图纯契约模块：实体/关系类型、抽取批次、节点/边/来源、`origin` 枚举、阶段枚举、原因码（含 `too_many_documents`、`graph_too_large`、`too_many_sources`、`ambiguous_source`、`index_timeout`、`processing_timeout`、`graph_incomplete`、`write_unconfirmed`）与结构校验，不导入第三方 SDK；验证方式是单元测试覆盖合法结果、重复键、悬空端点、缺来源、非法 origin、超限与空图
- [x] 2.4 新增业务模型规划器：输入只来自本次选中的、本项目已就绪材料与用户问题；先按材料分块生成实体/关系类型与批次计划，再按批次抽取节点、关系与来源；MUST NOT 使用仓库内固定夹具、验收专用材料或私有样本冒充业务输入；验证方式是假 transport 断言请求包含选中材料分块与问题、不包含 `private-inputs/` 或固定夹具，字段校验、预算与失败分类正确
- [x] 2.5 新增 Zep 业务 provider 的项目图谱绑定：按 `zep-cloud==4.1.0` 实际签名 `graph.create(name=..., description=..., time_zone=..., idempotency_key=...)` 创建，不传客户端图谱 UUID 或 metadata，保存 `Graph.uuid_`；只接受当前项目绑定，覆盖首次、已绑定、响应丢失、响应已到但数据库未提交、返回空 UUID、UUID 不一致、绑定项目不匹配；验证方式是假客户端断言只调用一个幂等键、只保存一个 `graph_id`、异常不覆盖、不回退到验收图谱
- [x] 2.6 新增 Zep 业务 provider 的写入与读回：`graph.episode.add` 保存 `episode.uuid_`；`graph.search_episodes` 按 UUID/build 标记/内容哈希匹配；`Episode.processed` 判定处理完成；`graph.node.list`/`graph.edge.list` 用当前 episode UUID 集合过滤并按 `page_size`/`cursor` 分页到没有下一页，按 `uuid_` 去重；验证方式是假客户端覆盖旧记录+正确记录、写入响应丢失无 UUID、可搜索但无可用边、分页、读回不稳定
- [x] 2.7 新增测试 provider：根据本次材料分块与问题确定性生成节点、边与来源，不访问网络；只有允许替身开关为 true 时可用；验证方式是单元测试断言默认配置拒绝 stub、显式开启后真实调用为 0、同一输入产生稳定结果且标记来源类型
- [x] 2.8 在 `app/services/worker.py` 增加 `execute_graph_build` 六步执行：`bind`、`plan`、`extract`、`write`、`readback`、`publish`；每步复用锁定任务行、实际数据库时钟、租约令牌、步骤唯一约束与事件事务；第 6 步准备或校验候选快照并在发布事务中原子发布最终快照/`ready`/`canContinue`/完成事件/`succeeded`；验证方式是单元测试覆盖正常六步、发布前崩溃、取消先于发布、发布先于取消、旧令牌拒绝、接手从下一步继续
- [x] 2.9 在 `app/worker.py` 按 `runs.kind` 分派建图任务，保持测试任务与材料解析分支不变；验证方式是单元测试断言三种 kind 分派正确，建图任务不进入测试任务执行路径
- [x] 2.10 新增建图开始服务：用 advisory lock 串行化同一 `(project, operation_key)`，返回 202/200 语义，只新增一对 job/run 与一个 build，终态后允许新 build；验证方式是并发测试断言一个 202、一个 200、数据库只有一组记录，冲突路径无孤立 run

## 3. 业务 API 与事件流

- [x] 3.1 新增建图开始请求 schema：材料列表非空、去重、最多 5 份，问题去除首尾空白后 1–500 字符；验证方式是 422 测试覆盖空问题、纯空白、超长、空材料、6 份材料、非法材料标识，且数据库无新增
- [x] 3.2 新增 `POST /api/projects/{projectId}/evidence-graph/builds`：复用 `resolve_owned_project`，校验材料属于本项目且 `ready`，返回 202/200 与 `buildId`/`jobId`/`runId`；验证方式是路由测试覆盖 202、重复 200、409 未就绪、422 超过 5 份、404 他人/不存在材料、401 匿名不访问数据库
- [x] 3.3 新增 `GET /api/projects/{projectId}/evidence-graph`、`GET .../builds/{buildId}` 与 `GET .../builds/{buildId}/snapshots/{revision}`：默认选择活动 build 或最近 build，`build` 参数按 ID 查询旧 build；只返回已发布快照；验证方式是路由测试覆盖默认最近、活动 build 优先、按 ID 旧 build、第二次等待不返回第一次完成图、匿名 401、他人项目 404、非法版本 400、数据库不可达 503
- [x] 3.4 扩展建图事件 payload：包含 `buildId`、`phase`、`acceptedEpisodes`、`confirmedEpisodes`、`plannedEpisodes`、`snapshotRevision`，不包含整张图；候选快照不进入事件；验证方式是事件序列测试断言事件与已发布快照版本一致、终态事件与最终快照同一发布事务、重连按游标补齐、旧 build 迟到事件被前端丢弃
- [x] 3.5 实现 `canContinue` 服务端判定：仅在 `ready`、最终已发布快照 `complete=true`、至少一个材料节点与一条材料关系、所有材料来源通过校验时为 true；验证方式是单元测试覆盖完成、部分、失败、取消、空图、来源不完整、规模超限七种状态
- [x] 3.6 增加建图错误映射与脱敏：`too_many_documents`、`document_not_ready`、`invalid_graph_scope`、`invalid_graph_response`、`source_mismatch`、`ambiguous_source`、`source_unavailable`、`invalid_model_output`、`model_unavailable`、`graph_unavailable`、`write_unconfirmed`、`index_timeout`、`processing_timeout`、`graph_incomplete`、`graph_too_large`、`too_many_sources`、`insufficient_graph`；验证方式是路由/服务测试断言响应只含中文说明与原因码，不含正文、密钥、完整响应或内部图谱标识

## 4. 材料来源、快照与原文定位

- [x] 4.1 实现文本来源映射：按解析编码解码完整原文，用与入库相同的归一化逻辑重建清洗文本到原文字符偏移的映射，结合分块 `source_start`/`source_end` 定位分块，再映射候选引文；验证方式是单元测试覆盖含 emoji、连续空格、CRLF、GB18030 文本，断言 UTF-16 偏移与原文切片一致
- [x] 4.2 实现 PDF 来源映射：逐页重新抽取原始文本，用重复页眉/页脚清理逻辑重建清洗文本到页内偏移的映射，结合分块 `page_no`/`source_start`/`source_end` 定位；验证方式是单元测试覆盖含重复页眉的多页 PDF，断言第 3 页正文页码与页内偏移正确，页眉被清理但偏移不漂移
- [x] 4.3 实现既有入库材料定位：不重新上传或重新解析，使用现有分块文本、页码/段落与偏移范围完成映射；原件缺失时 `source_unavailable`；验证方式是使用迁移前已入库的分块记录运行映射测试，断言结果与重新解析一致
- [x] 4.4 实现引文歧义处理：同一引文在分块中多次出现或映射到多个不同原文区间时，必须用模型 `sourceStart`/`sourceEnd` 消歧；仍不唯一则 `ambiguous_source`，不得取第一次匹配；验证方式是单元测试覆盖两次出现、偏移消歧、无偏移歧义、引文不存在、切片不一致
- [x] 4.5 实现来源校验与快照物化：材料必须属于当前项目且被本次选中，分块必须存在，偏移在原文/物理页范围内，引文等于映射后的原文切片；节点/边/来源挂同一 `snapshot_id`，计数由该快照行数计算；验证方式是单元测试覆盖 PDF 第 3 页、文本第 12 段、未选中材料、他人材料、超限来源、计数等于数组长度
- [x] 4.6 实现 `origin=material` 与 `origin=hypothesis` 校验：材料陈述必须有至少一条有效来源，用户假设可以无来源，假设引用材料时仍须通过同一校验；验证方式是单元测试覆盖问题假设、材料陈述、假设引用材料、材料声明无来源
- [x] 4.7 在快照响应中生成来源定位：PDF 返回 `#page=<pageNo>` 原件地址，文本返回原文视图与 UTF-16 偏移，详情列出全部来源；验证方式是路由测试断言 PDF/文本来源字段与既有材料接口一致，且不发起模型或 Zep 调用
- [x] 4.8 增加来源/快照访问边界与规模失败：所有读取先鉴权与项目归属，再校验 build 与版本；超过 200 节点、400 边或每项 5 条来源时在发布前失败；验证方式是匿名 401、他人项目 404、不存在 build 404、未来版本 400、规模超限 `graph_too_large`/`too_many_sources`，且不发布截断快照

## 5. 前端图谱面板与 D3 交互

- [x] 5.1 在 `web/package.json` 增加精确版本的 D3 依赖与类型依赖，更新锁文件；验证方式是 `cd web && npm ci && npm run typecheck && npm run build` 通过
- [x] 5.2 新增前端建图 API 类型与纯状态 reducer：处理 `buildId`、`phase`、`revision`、`nodeCount`、`edgeCount`、旧响应丢弃、刷新读取最近已发布快照、失败保留旧快照、第二次 build 不接收第一次响应；验证方式是 `npm test` 覆盖等待/增量/完成/旧响应/跨 build/失败六种输入
- [x] 5.3 新增 `EvidenceGraphPanel` 建图表单：只列 `ready` 材料、最多 5 份、问题输入、开始按钮、空问题/未就绪/超限提示、开始后显示 `buildId`/`jobId`/`runId` 与状态；验证方式是纯函数测试 + `npm run typecheck`，并在第一阶段页面手工确认空问题与 6 份材料不创建任务
- [x] 5.4 实现默认最近 build 与旧 build 查询：默认显示服务端最近/活动 build；`?build=<id>` 打开旧 build 并保留 `layout`/`lang`/`document`；`buildId` 不匹配响应丢弃；验证方式是纯函数/路由测试 + 浏览器手工确认第二次等待时不显示第一次完成图
- [x] 5.5 实现三态进度与同快照渲染：等待索引显示已受理/已确认，增量绘图保留上一份画面，完成显示最终已发布快照；“正在读取节点与边”覆盖可搜索但无可用边；节点数、边数、图例与画布使用同一 `revision`；验证方式是 `npm test` 断言计数与数组同源、旧响应不覆盖新版本
- [x] 5.6 实现 D3 力图、图例、缩放/平移、节点拖动、边标签开关、刷新与最大化；验证方式是 `npm test` 覆盖纯视图状态，`npm run build` 通过，并在浏览器手工确认缩放/拖动/标签/最大化不改变服务端数据与地址上下文
- [x] 5.7 实现节点/边详情与来源跳转：显示类型、`origin`、摘要、全部来源；PDF 打开 `#page=<pageNo>`，文本打开原文并高亮 UTF-16 偏移；跳转保留 `layout`、`lang`、`buildId`、`document`；验证方式是 URL 构造单元测试 + 浏览器手工确认 PDF 落页与文本高亮
- [x] 5.8 实现下一阶段入口：只使用服务端 `canContinue`，完成后 `push` 到 `roles-environment` 并保留 `layout`/`lang`/`buildId`；未完成/超限/来源不完整时禁用并说明原因；第二阶段显示角色与环境尚未配置；验证方式是状态单元测试 + 浏览器手工确认入口可用/禁用与后退行为
- [x] 5.9 保留材料面板与测试任务面板并隔离状态：材料列表、上传、摘录、来源、图谱面板与测试任务互不改写；三种布局下材料面板仍可访问，`graph` 布局折叠为工具条抽屉；验证方式是状态单元测试 + 三种布局手工走查
- [x] 5.10 补齐中英文文案：建图表单、三态、规模上限、来源歧义/不可用、处理中、错误、图例、详情、来源、下一阶段与空态；验证方式是 `lang=zh|en` 手工对照，项目名、阶段 slug 与材料名不被翻译

## 6. 配置、镜像、文档与工程规则

- [x] 6.1 更新 `docker-compose.yml`：`worker` 使用新构建目标并只接收业务模型/Zep 变量、provider 模式与建图上限；`app` 不接收模型/Zep 变量；`engine` 继续独占 `ZEP_ACCEPTANCE_GRAPH_ID`；验证方式是 `docker compose config` 断言三组环境变量边界
- [x] 6.2 更新 `README.md`：新增建图用户流程、默认最近 build/旧 build、三态、六步与原子发布、来源定位、5/200/400/5 上限、API 表、配置表、零外呼说明、验收命令与失败码；验证方式是逐条执行文档命令并核对输出
- [x] 6.3 更新 `AGENTS.md`：新增业务建图 worker、项目图谱幂等绑定、六步原子发布、读回证据、原文偏移与歧义、规模上限、默认最近 build、两轮人工验收、零外呼与私有样本规则；验证方式是人工检索关键条目与 specs/design 无矛盾
- [x] 6.4 确认 `.env.example` 只含占位值，未新增真实凭据；验证方式是 `git check-ignore .env` 仍成立，全文检索 `replace-with-`/`example.invalid`/全零 UUID 语义一致
- [x] 6.5 检查日志与报告字段：只允许标识、阶段、原因码、调用次数、耗时、快照版本、页码/段落与布尔校验结果；验证方式是注入唯一测试密钥运行失败路径，扫描日志与报告不含该密钥、材料正文、完整请求/响应或完整 episode

## 7. 自动化验收、报告与 CI

- [x] 7.1 在 `scripts/verify.py` 注册 `GRAPH_CHANGE_ID = "2026-11-22-build-evidence-graph"` 与 `graph_evidence`，加入 `KNOWN_CHANGES`；验证方式是 `python3 -m py_compile scripts/verify.py` 通过，未知 `CHANGE` 仍以退出码 2 结束
- [x] 7.2 增加建图基础场景：上传原创小材料并等待 `ready`、选择材料与问题、开始 202、重复点击 200、匿名 401、他人 404、空问题 422、未就绪 409、6 份材料 422；验证方式是 Change 07 运行两次均通过，报告记录拒绝原因与重复身份
- [x] 7.3 增加项目图谱创建场景：模拟 `graph.create` 响应丢失、响应已到但数据库未提交、worker 接手；断言同一幂等键与稳定参数只绑定一个 `graph_id`；验证方式是报告记录创建尝试次数与绑定数
- [x] 7.4 增加三态与读回场景：用 stub worker 观察 `waiting_index`、`drawing`、`ready`，覆盖“episode 可搜索但无可用边”“同批旧记录+正确记录”“写入响应丢失无 UUID”“分页读回”“读回不稳定”；验证方式是报告记录阶段序列、快照版本、节点/边计数与读回集合
- [x] 7.5 增加失败分类场景：分别构造 `index_timeout`、`processing_timeout`/`graph_incomplete`、`write_unconfirmed`、`graph_too_large`、`too_many_sources`；验证方式是报告按不同原因码记录，且都不产生完成快照
- [x] 7.6 增加原子发布与竞争场景：候选快照准备后停止 worker、取消先于发布、发布先于取消；验证方式是报告记录只有一条终态事件，候选快照不可见，取消后仍可读取部分快照
- [x] 7.7 增加来源场景：用原创三页 PDF 与含 emoji/连续空格/CRLF 的原创 TXT 生成至少 3 个材料节点和 3 条材料关系，断言 PDF `pageNo=3` 与 `#page=3`、文本 UTF-16 偏移、既有入库材料可定位、歧义引文 `ambiguous_source`；验证方式是报告记录来源校验与歧义结果，且不读取私有样本
- [x] 7.8 增加第二次建图隔离场景：同一项目连续开始两次，第二次仍在等待时默认读取返回第二次 build，第一次迟到响应被丢弃，按第一次 `buildId` 查询仍可读；验证方式是报告记录两个 buildId 与响应丢弃结果
- [x] 7.9 在验收脚本中重建 worker 时显式启用 stub 与允许开关，结束后恢复默认；验证方式是运行后 `docker compose config` 与 worker 环境恢复，报告 `calls.real=0`、`calls.double>0`
- [x] 7.10 在 `.github/workflows/verify.yml` 保留默认 verify job，增加 Change 07 独立 job，使用不同 artifact 名称并断言 `tested_sha`、`dirty=false`、`result=passed`、`graph_evidence.calls.real=0`；验证方式是审阅 YAML 或本地等价检查，确认两个 job 都调用同一 `make verify` 入口
- [x] 7.11 确认默认 `make verify` 不触发建图、不读取真实密钥、不发起真实外部调用；验证方式是默认运行报告与日志中建图真实调用为 0，既有场景全部保留
- [x] 7.12 在 README 验收章节补充 Change 07 命令、`graph_evidence` 字段、连续两次运行要求与“离线通过不替代人工签收”；验证方式是文档命令与实际输出一致

## 8. 集成验证与人工验收

- [x] 8.1 运行 `.venv/bin/python -m unittest discover -s tests`、`cd web && npm test && npm run typecheck && npm run build`，确认既有回归与新增测试全部通过
- [x] 8.2 在干净提交上连续两次执行默认 `make verify`，确认退出码 0、`tested_sha` 一致、`dirty=false`、既有场景完整且零真实外呼
- [x] 8.3 在同一 SHA 上连续两次执行 `make verify CHANGE=2026-11-22-build-evidence-graph`，确认退出码 0、`graph_evidence` 阶段/来源/接手/发布结果一致、`calls.real=0`
- [x] 8.4 第一轮人工验收：项目作者仅通过自己的私有项目上传 25 页报告，在浏览器中观察等待索引、增量绘图、完成，抽查 3 个材料节点和 3 条材料关系返回原文位置；验证方式是作者本地签收清单，截图与私有报告留在仓库外，仓库只使用 EchoSim 自己的文案、代码与资产
- [x] 8.5 第二轮人工验收：作者用另一份原创校园接驳车材料重新建图，核对实体类型、关系类型、节点或边确实随材料改变；验证方式是作者本地签收清单，原创材料内容不提交
- [x] 8.6 准备待签收清单（不写入 `docs/acceptance/`）：被测 SHA、默认与 Change 07 报告路径、阶段序列、快照版本、3 节点/3 关系来源、接手 `attempts`、创建绑定数、零真实外呼、已知限制；验证方式是清单可逐项对应报告字段
- [x] 8.7 人工签收后才在 `docs/acceptance/` 写入记录，且记录只引用同 SHA、`dirty=false` 报告与作者本地证据；验证方式是签收前 `git status --porcelain docs/acceptance` 为空，签收后记录不含私有正文、外部品牌名、链接、代码、截图或回放数据
- [x] 8.8 最终扫描仓库与 `runtime/`：确认没有私有 25 页报告、原创校园接驳车材料正文、真实密钥、完整模型/Zep 响应、截图或回放数据；验证方式是全文检索与 `git status`，并确认自动化流程未创建或修改 `docs/acceptance/`
