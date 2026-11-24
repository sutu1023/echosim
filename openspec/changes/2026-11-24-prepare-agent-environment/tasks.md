# Tasks

## 0. 交付边界

| 类别 | 内容 |
| --- | --- |
| 本轮交付 | build/最终快照绑定、按实际快照的中文/英文类型角色筛选、材料提及/关联/表达/模拟设定分层、五步 worker 与分批预览、普通开始/显式重试、准备详情与 SSE 观察、自动/手动轮数、带版本确认冻结、第二阶段工作台、Change 08 离线验收 |
| Change 09 消费 | 已确认准备的 `frozenSelection`（`preparationId`、`buildId`、`snapshotId`、`question`、轮数、角色、平台映射、初始话题来源性质、首发角色与话题对应、激活顺序）；不包含 `create_post`/`create_comment` 作为唯一动作；本变更不执行双平台动作 |
| 作者亲测 | 私有项目材料下的中文快照角色详情、材料提及/关联/表达/模拟设定标签、第一批预览、失败/取消重试、两页并发确认、72→40 刷新读回、准备期间新图提示、三种布局与中英文 |

## 1. 数据模型与前向迁移

- [x] 1.1 新增 `app/migrations/0011_agent_environment_preparation.sql`：扩展 `runs.kind` 加入 `agent.environment.prepare`，给 `graph_snapshots` 增加 `(build_id, id)` 唯一约束，创建 `environment_preparations`（含预览/画像计数与 provider 调用计数）、`environment_roles`（含 `batch_index`、`preview_ready_at`、`published_at`）、`environment_role_sources`、`environment_platforms`（仅平台映射）、`environment_topics`（含 `origin`/`claimNature`）、`environment_activation`（含首发话题）、`environment_frozen_selections` 及索引；验证方式是在空数据卷与已有 Change 01–08 数据卷上各启动一次，迁移只执行一次且旧表数据不变
- [x] 1.2 为准备表补充约束测试：同一 build 只允许一个 `preparing`/`ready` 准备、`run_id` 唯一、角色 `(preparation_id, node_key)` 唯一、角色节点必须属于同一快照、角色来源必须属于同一快照、平台键与原生平台映射固定、预览/最终角色标记合法、确认选择每准备唯一；验证方式是 `.venv/bin/python -m unittest tests.test_migrate` 覆盖重复迁移与非法插入
- [x] 1.3 新增准备持久化服务：创建准备与 job/run、按 build 读取活动/最近准备、按准备读取详情、保存轮数与 `settingsRevision`、写入角色草稿/批次预览与来源、确认冻结；验证方式是服务层单元测试覆盖默认值、普通开始复用、失败/取消后重试新准备、批次预览恢复、冻结幂等
- [x] 1.4 为快照绑定增加服务校验：准备只能引用同一项目 build 的 `published=true, complete=true` 快照；`canContinue=false`、部分快照、跨 build 快照与不存在快照分别拒绝；验证方式是数据库测试覆盖四类绑定失败，断言没有准备/job/run 记录

## 2. 角色候选与材料事实

- [x] 2.1 新增纯角色选择器：读取实际快照的中文显示类型与英文类型，`origin=material`、有效来源、媒体机构/平台渠道区分、`其他主体` 的来源原句判定；只出现在终点的机构可入选，有出边的事件与待核实说法不入选；验证方式是单元测试覆盖中文快照、旧英文快照、`summary=null`、三个反例、无来源节点与空候选
- [x] 2.2 组装角色材料分层：`mention`/身份、`background`、`expressedTopics`、`involvedTopics`、`simulation.topics`，分别关联节点来源与关系来源，并保留关系 `claimNature`（旧空值保留未知）；无背景/关注证据显示“材料未说明”，`author_recommendation` 只作为环境背景；验证方式是单元测试断言节点引文只作提及、表达话题有归属原句、仅关联写“材料涉及的话题”、旧空性质显示未知
- [x] 2.3 复用来源定位：PDF 返回 `pageNo` 与 `#page=`，文本返回 UTF-16 `sourceStart`/`sourceEnd`；验证方式是原创三页 PDF 与含 emoji/CRLF 的 TXT 测试，断言第 3 页、原文切片与偏移一致
- [x] 2.4 验证角色随材料变化：用两份原创微型材料分别生成快照，断言角色名称、类型或数量不同，且不会复用旧角色；验证方式是选择器与准备服务测试，不读取私有样本
- [x] 2.5 为无角色与来源缺失增加失败路径：无候选在开始请求内返回 `no_role_candidates`，无来源/跨快照来源在发布前返回 `role_source_missing`/`role_source_mismatch`；验证方式是路由与服务测试断言不创建准备或无法进入 `ready`

## 3. 模型模拟设定与离线替身

- [x] 3.1 新增模拟设定契约：动机、立场、活跃时段（0–23 升序、至少一项）、发言频率、响应延迟、影响力权重、平台偏好、额外初始话题、激活顺序、首发角色与首发话题；验证方式是纯契约测试覆盖非法小时、重复角色、顺序缺角色、空首发、首发话题缺失与未知字段
- [x] 3.2 新增画像模型适配器：复用现有 Chat Completions 传输、`User-Agent`、JSON 对象输出、30 秒/最多 2 次请求预算与错误分类；输入包含本次绑定的 `question`、角色键、类型与材料事实摘要；验证方式是假 transport 覆盖正常、超时、401/402/403、非法 JSON、未知角色、改写材料事实与越界字段
- [x] 3.3 新增确定性测试替身：接收同一 `question`，按角色键与材料摘要生成稳定模拟设定、初始话题、首发话题与激活顺序，不访问网络；验证方式是同一输入两次结果一致、默认不允许替身时拒绝启用、显式允许后真实调用为 0
- [x] 3.4 增加准备配置：provider/替身开关、默认 72 小时与 60 分钟、手动轮数上限、画像批次与超时；同步 `.env.example`、`docker-compose.yml`、`app/config.py` 与 README 配置表；验证方式是配置一致性测试断言名称、默认值与占位值语义

## 4. worker 五步执行、租约与事件

- [x] 4.1 实现 `execute_preparation` 五步：`bind`、`select`、`profile`、`compose`、`publish`；每步复用锁定任务行、数据库实际时钟、租约令牌、步骤唯一约束与事件事务；`profile` 按稳定批次写 `preview_ready_at`，未校验草稿不可见，第 5 步原子发布最终角色与配置；验证方式是单元测试覆盖正常五步、两批预览、发布前崩溃与配置不可见
- [x] 4.2 在 `app/worker.py` 按 `runs.kind=agent.environment.prepare` 分派准备任务，保持测试任务、材料解析与建图分支不变；验证方式是单元测试断言四种 kind 分派正确，准备任务不进入测试任务执行路径
- [x] 4.3 实现准备失败与取消：模型不可用、非法输出、来源校验失败、用户取消分别写稳定原因码与终态，不发布最终角色/配置；验证方式是 worker 测试断言 `failed`/`canceled`、终态事件、`canContinue=false`、只读未完成预览与无冻结选择
- [x] 4.4 实现中断接手：已 `preview_ready_at` 的角色按批次复用，接手从第一个未提交步骤或未完成批次继续，不重复角色或步骤；验证方式是模拟 worker 停止、租约过期与新 worker 领取，断言 `attempts>1`、同一 `preparationId`/`jobId`/`runId`、批次复用且不重复生成
- [x] 4.5 定义准备事件载荷并接入 `run_events`：只含准备/build/快照、步骤、阶段、批次标识、角色标识、计数、可选轮数与原因，不含角色正文、材料正文、引文或密钥；验证方式是事件序列测试断言五步事件、批次事件、终态事件、发布前不引用配置与事件脱敏
- [x] 4.6 隔离测试任务面板：准备任务不出现在 `test.simulation` 列表、副作用计数或测试日志；验证方式是路由与前端状态测试断言准备事件不改写测试任务面板

## 5. API、轮数与冻结选择

- [x] 5.1 新增准备上下文、普通开始与显式重试接口：`GET /api/projects/{projectId}/environment?build=`、`POST .../preparations` 与 `POST .../preparations/{id}/retry`；复用 `resolve_owned_project`，普通开始对 preparing/ready 返回 200、对 failed/canceled 返回 `preparation_retry_required`，重试返回 202；验证方式是并发测试断言一个 202、一个 200、只新增一组记录，失败/取消后普通开始不新建、重试新建
- [x] 5.2 新增准备详情接口与序列化：返回绑定 build/快照/问题、阶段、失败、`previewRoles`/`previewOnly`、最终角色、材料分层、模拟设定、平台映射、话题来源性质、首发话题、激活顺序、轮数与冻结选择；未校验草稿不返回，预览与最终角色分开；验证方式是路由测试覆盖两批预览、草稿不可见、失败/取消只读预览、已发布完整结构与匿名 401
- [x] 5.3 实现轮数 schema 与服务：默认 72/60/auto → 72；手动 40、3；零时长、非法轮数、不能整除分别返回 `duration_invalid`、`rounds_invalid`、`rounds_not_integral`；保存递增 `settingsRevision` 且读回一致；验证方式是服务与路由测试覆盖成功、失败与刷新读回
- [x] 5.4 实现 `PATCH .../configuration`：校验 `settingsRevision`，过期返回 `settings_conflict`，已冻结返回 `preparation_frozen`，失败/取消返回 `preparation_not_editable`；验证方式是并发保存与确认后保存测试，断言旧值不被静默覆盖
- [x] 5.5 实现 `POST .../confirm`：请求携带 `settingsRevision`，同事务核对版本、重新校验就绪/轮数/来源后创建冻结选择；版本冲突返回 `settings_conflict` 且无冻结记录；相同版本重复确认返回同一 `selectionId`；`canContinue` 只在确认后为 true；验证方式是服务与路由测试覆盖两页并发确认、幂等、未就绪拒绝与冻结后拒绝修改
- [x] 5.6 为 Change 09 固定读取契约：冻结选择返回 `selectionId`、`preparationId`、`buildId`、`snapshotId`、`question`、`settingsRevision`、轮数、完整角色（提及/背景/表达话题/关联话题/模拟设定）、平台映射、初始话题来源性质、首发角色与话题对应、激活顺序；验证方式是契约测试断言字段稳定、未确认返回空、后续新 build 不改变冻结选择、平台映射不含唯一动作限制
- [x] 5.7 增加错误映射与脱敏：401/404 `项目不存在`/`图谱不存在`/`准备不存在`、409 `graph_not_ready`/`preparation_not_ready`/`preparation_retry_required`/`preparation_not_retryable`/`settings_conflict`/`preparation_frozen`、422 配置原因、503 保留 Cookie；验证方式是路由测试断言响应不含材料正文、角色正文、模型响应、密钥或内部路径

## 6. 第二阶段工作台

- [x] 6.1 新增前端准备 API 类型与纯状态 reducer：处理 `buildId`/`preparationId` 守卫、迟到响应丢弃、切换准备先清空旧角色/轮数/来源、准备中预览/失败取消等页面状态；验证方式是 `cd web && npm test` 覆盖旧 build、旧准备、失败、加载、空态与正常
- [x] 6.2 新增 `RolesEnvironmentPanel`：按地址 `build` 读取上下文，无 build 时用 `router.replace` 固定；显示图谱未完成、尚未准备、准备中（含批次预览）、已就绪待确认、已确认、失败/取消六态；验证方式是 `npm run typecheck` 与面板状态单元测试
- [x] 6.3 实现普通开始、显式重试与任务订阅：普通开始只在无准备时新建，preparing/ready 显示原准备，failed/canceled 显示重试；订阅现有 job SSE，事件只更新阶段/批次标识/计数/原因，收到批次或终态后重读准备详情；验证方式是状态测试覆盖 202/200、`preparation_retry_required`、断线重连、读取详情期间收到终态与失败重试
- [ ] 6.4 实现角色列表与详情：分开展示材料提及、背景、表达话题、材料涉及的话题、材料未说明与模拟设定；显示来源性质（旧空值显示未知）；PDF 来源打开 `#page=`，文本来源打开原文并按 UTF-16 偏移高亮；验证方式是 URL 构造测试 + 浏览器手工确认落页与高亮
- [x] 6.5 实现环境配置展示：活跃时段、`platform_a`/`platform_b` 原生平台映射、初始话题来源性质、首发角色与话题对应、激活顺序；不把 `create_post`/`create_comment` 显示成唯一允许动作；验证方式是组件状态测试断言顺序覆盖所有角色、首发为前缀、首发话题有对应、平台映射固定
- [x] 6.6 实现轮数表单与确认：显示服务端 `autoRounds`/`rounds`，保存手动 40 与 3，确认携带当前 `settingsRevision`，处理 `duration_invalid`/`rounds_invalid`/`rounds_not_integral`/`settings_conflict`/`preparation_frozen`；冲突时重新读取，确认后显示冻结选择与第三阶段入口；验证方式是纯函数测试 + `npm run typecheck`
- [ ] 6.7 把第二阶段接入工作台外壳：更新 `stages.ts` 与 `WorkbenchView.vue`，移除第二阶段固定空态；阶段导航、来源跳转与进入第三阶段显式保留 `layout`、`lang`、`build`、`preparation`；验证方式是 `cd web && npm test`、`npm run typecheck` 与三种布局手工走查
- [ ] 6.8 补齐中英文文案与错误码：六态、角色类型、材料提及/背景/表达/关联/材料未说明、模拟设定、平台映射、轮数、冻结、重试要求、版本冲突、无角色、模型不可用与服务不可用；验证方式是 `lang=zh|en` 对照，项目名、阶段 slug、角色显示名与材料名不被翻译

## 7. 验收、报告与文档

- [x] 7.1 在 `scripts/verify.py` 注册 `ENVIRONMENT_CHANGE_ID = "2026-11-24-prepare-agent-environment"` 与 `environment_evidence`，加入 `KNOWN_CHANGES`；验证方式是 `python3 -m py_compile scripts/verify.py` 通过，未知 `CHANGE` 仍以退出码 2 结束
- [x] 7.2 增加准备全链路场景：原创小材料入库、建图到最终快照、普通开始 202、五步事件、`ready`、角色/来源/平台映射/话题来源/首发话题/顺序与 72 轮；替身收到 `question`；验证方式是 Change 08 运行两次均通过，报告记录 `preparationId`、`buildId`、`snapshotId`、`question` 与 provider 边界持久化的 `calls.real=0`、`calls.double>0`
- [x] 7.3 增加角色与来源场景：中文快照筛选、旧英文快照、`summary=null`、只出现在终点的机构、有出边的事件、待核实说法、媒体机构/平台渠道区分、PDF 第 3 页、文本 UTF-16、材料提及/背景/表达/关联/模拟设定分层、旧 `claimNature` 空值保留未知；验证方式是报告记录三类角色、三个反例、来源定位与性质断言
- [x] 7.4 增加轮数与冻结场景：默认 72、手动 40 刷新读回、手动 3、零时长/非法/不能整除拒绝、两页并发确认（A 40/revision 2、B 3/revision 3、A 冲突后重读确认 3）、相同版本重复确认幂等、冻结后修改拒绝、Change 09 读取契约；验证方式是报告记录模式、生效轮数、冲突前后 `settingsRevision`、冻结记录数与 `selectionId`
- [x] 7.5 增加普通开始、显式重试、分批预览、重连与接手场景：preparing/ready 普通开始 200、failed/canceled 普通开始 409 与重试 202、第一批可展开第二批生成中且第三阶段不可用、读取详情期间恰好收到终态、两段 SSE 序号、worker 停止后 `awaitingTakeover`、接手 `attempts>1`、批次复用；验证方式是报告记录身份、批次、序号、attempts 与角色计数
- [x] 7.6 增加边界场景：无角色 `no_role_candidates`、模型不可用 `model_unavailable`、失败/取消后只读预览与重试、匿名 401、他人项目/他人 build/不存在准备 404、数据库不可达 503 保留 Cookie；验证方式是报告按原因码记录，失败不产生最终角色、配置或冻结选择
- [x] 7.7 增加换材料场景：同一项目换原创材料完成新 build，准备角色名称/类型/数量变化，旧准备与旧冻结选择保持旧 `snapshotId`；验证方式是报告记录两组 build/快照/角色差异
- [x] 7.8 增加默认验收轻量断言：第二阶段深链接返回 `EchoSim` HTML、匿名准备接口 401、无 build 返回可区分原因；默认 `make verify` 不运行准备 worker、不调用模型；验证方式是默认报告与日志中准备真实调用为 0
- [x] 7.9 更新 `.github/workflows/verify.yml`：保留默认 job，增加 Change 08 独立 job 与不同 artifact 名称，断言 `tested_sha`、`dirty=false`、`result=passed`、`environment_evidence.calls.real=0`；验证方式是审阅 YAML 或本地等价检查
- [x] 7.10 更新 README 与 AGENTS.md：第二阶段用户流程、中文/英文快照角色筛选、材料提及/关联/表达/模拟设定、分批预览、普通开始/重试、五步与原子发布、轮数公式、带版本确认冻结、平台映射与动作边界、API 表、失败码、零外呼与私有样本边界；验证方式是逐条执行文档命令并核对输出

## 8. 集成检查与人工走查

- [x] 8.1 运行后端单元测试 `.venv/bin/python -m unittest discover -s tests`，确认迁移、选择器、worker、API、轮数与冻结测试全部通过
- [x] 8.2 运行前端 `cd web && npm ci && npm test && npm run typecheck && npm run build`，确认状态守卫、角色/轮数组件与阶段链接回归通过
- [x] 8.3 在已有 Change 01–08 数据卷上执行 `make migrate` 并启动 `make up`，确认旧项目、材料、建图、测试任务与第二阶段深链接仍可用
- [x] 8.4 在干净候选 SHA 上运行默认 `make verify` 与 `make verify CHANGE=2026-11-24-prepare-agent-environment` 各两次，确认同一 SHA、连续两次通过、默认零外呼、专属报告 `calls.real=0`。已在干净候选 `edfe58ea6d871cf78093b5e08ef93f6dc3acf30d` 验证，默认两次、专属两次全部通过
- [ ] 8.5 由作者在私有项目中使用私有材料人工走查：中文快照角色详情、材料提及/关联/表达/材料未说明/模拟设定、第一批预览、失败/取消重试、两页并发确认、72→40 刷新读回、确认冻结、准备期间新图提示、三种布局与中英文；确认私有 PDF、含正文截图、真实画像与模型原始输出未进入仓库、日志或报告
- [ ] 8.6 人工走查完成后只在 `docs/acceptance/` 准备脱敏结论、SHA、任务标识与必要证据链接，不写入正文、截图、真实画像或模型原始输出；不提前归档、不合并；自动化检查不得创建、修改或删除该目录内容


## 10. 复核修复

- [x] 10.1 验收隔离：校验 `VERIFY_COMPOSE_PROJECT` 只允许 `echosim-verify` 或安全后缀，启动前获取独占锁，清理只作用于本项目并检查退出码；用命令替身证明误指开发栈时在任何停止/删卷/重建命令前拒绝；验证方式是 `tests.test_verify_isolation` 与隔离验收运行
- [x] 10.2 工作台状态交接：显式重试成功后按新 `preparationId` 强制切换、清空旧预览/游标并订阅新 job，旧事件按身份丢弃；冻结后第三阶段读取服务端 `frozenSelection` 显示“准备已确认，运行功能待接入”，刷新一致；验证方式是页面级重试与第三阶段刷新测试
- [x] 10.3 轮数确认守卫：未保存或非法输入禁用确认并说明原因，保存后从服务端读回轮数与 revision 才允许确认；验证方式是页面级“已保存 40 → 输入 3 → 不请求确认 → 保存读回 3 → 确认冻结 3”、输入 0 与 `settings_conflict` 路径
- [x] 10.4 来源深链接：首次进入与切换材料均加载正确原文并按 UTF-16 高亮，旧材料迟到响应不覆盖新材料；角色提及/背景/话题来源逐个核对 PDF `#page=` 与文本区间；验证方式是含 emoji 文本与三页 PDF 的页面级回归
- [x] 10.5 归属判定：未知类型绑定当前主体名称与所在从句，“某大学聘请林记者”不判高校；表达话题只认当前主体自己的动作，“王同学参加…主办方表示…”不算给王同学；角色详情显示 `simulation.topics`；验证方式是两个原创反例与页面渲染测试
- [x] 10.6 provider 与话题交接：provider 每次实际尝试在边界计数并在失败/超时/失去租约前持久化，成功批次不重复计数；批次 `initialTopics` 中只出现在初始话题的合法项进入最终配置与冻结选择并保留来源性质；验证方式是假 transport、零真实外呼和画像话题断言
- [x] 10.7 运行后端测试、前端页面/状态测试、typecheck、build 与 OpenSpec 严格校验，并在隔离环境运行默认与 Change 08 开发验证
- [ ] 10.8 在干净候选 SHA 上重复默认与 Change 08 验收、远端 CI 与作者浏览器签收（本轮保留待办）


## 11. 浏览器验收复核修复

- [x] 11.1 `layout=graph` 来源深链接自动展开材料上下文抽屉，保留图谱布局并显示 UTF-16 选区；验证方式是页面级 WorkbenchView 回归与 Chromium 无头观察
- [x] 11.2 `lang=en` 翻译角色类型与 PDF 页码标签，角色名/项目名/材料名保持原值；验证方式是英文页面级回归与 Chromium 无头观察
- [x] 11.3 旧准备期间轮询同一 build 上下文，新图完成后无需刷新显示提示，旧 build/snapshot/准备身份不变；验证方式是页面级轮询回归与 Chromium 无头观察

## 9. 能力与场景对应

| 能力 | 覆盖场景 |
| --- | --- |
| `agent-environment-preparation` | build/快照绑定、直接进入、旧 build、新图、中文/英文快照筛选、三个反例、材料提及/关联/表达/模拟设定、分批预览、普通开始/显式重试、刷新重连、接手、失败/取消、就绪判定、轮数 72/40/3、两页并发确认、Change 09 契约、访问边界、模型不可用、默认零外呼 |
| `project-workbench` | 第二阶段六态、图谱未完成、可准备、准备中分批预览、待确认、已确认、失败/取消、重试、版本冲突重读、来源跳转、三种布局与中英文、阶段导航保留 build/preparation |
| `background-job-queue` | 新 run 类型与操作键、五步事务、普通开始复用、失败/取消显式重试、不同 build 隔离、批次复用、原子发布、测试面板隔离 |
| `run-event-stream` | 准备事件标识/计数、批次预览事件、发布前配置不可见、终态一致、先详情后事件、详情期间终态、旧准备事件丢弃、事件鉴权 |
| `reproducible-verification` | 默认零外呼、Change 08 全链路、中文快照与三个反例、材料分层、第一批预览、普通开始/重试、两页并发确认、轮数与冻结、接手、无角色/无效配置/模型不可用、匿名/跨项目、换材料、私有边界、未知 CHANGE、两次运行 |
