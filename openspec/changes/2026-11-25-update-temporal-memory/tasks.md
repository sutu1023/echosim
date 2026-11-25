# Tasks

## 0. 交付边界

| 类别 | 内容 |
| --- | --- |
| 本轮交付 | 后端、worker、第三阶段页面与 Change 10 离线验收；停在候选提交前，人工签收留待浏览器复验 |
| 依赖输入 | Change 09 已合并的 `simulation.run`、`simulation_actions`/`simulation_rounds`、`_book_round()`、`_round_plan()`、`plan_round()`、运行详情与第三阶段面板；Change 07 已验证的 episode 幂等/查账/读回纪律；Change 08 的 `frozenSelection` 与冻结材料快照 |
| 留给后续 | 第四阶段报告、第五阶段深度互动、真实外网平台、跨项目复用运行记忆、真实 Zep 记忆证据（开发者显式执行） |
| 作者亲测 | 原创材料走查轮次切换、图例、来源回链、刷新恢复、未确认/暂不可用状态、中英文与三种布局；私有 25 页报告只留在仓库外作人工样例 |
| 本轮不做 | 不创建 `docs/acceptance/` 人工签收记录、不提交 commit、不归档；真实 Zep 外呼保持开发者显式入口 |

## 1. 数据模型与前向迁移

- [x] 1.1 新增 `app/migrations/0015_temporal_memory.sql`：扩展 `runs_kind_check` 加入 `simulation.memory`，创建 `simulation_graph_bindings`、`simulation_memory_entries`、`simulation_memory_episodes`、`simulation_memory_nodes`、`simulation_memory_edges`、`simulation_memory_provenance`、`simulation_round_plans`、`simulation_action_memory_reads` 与 `simulation_memory_syncs`；验证方式是在空数据卷与已有 0014 数据卷上各启动一次，迁移只执行一次且旧表数据不变；验证：migration 0015 applied on empty + existing 0014 schema；证据：迁移 0015 在空库与 0014 升级库应用，旧表不变
- [x] 1.2 补齐约束：`(simulation_id, action_id)` 唯一、`(simulation_id, memory_id)` 主键、`idempotency_key` 唯一、`episode_uuid` 唯一可空、`round_index >= 1`、`action_seq >= 1`、`state`/`write_outcome`/`memory_status`/`origin`/`facet` 枚举、来源唯一 `(simulation_id, target_kind, target_key, memory_id, facet)`；验证方式是插入重复行动记忆、重复幂等键、非法平台/轮次/枚举与跨运行来源，断言被拒绝；验证：迁移断言 + 非法插入被拒绝；证据：数据库约束反例覆盖跨运行来源、重复 episode 与非法状态
- [x] 1.3 为 `simulations` 增加 `memory_status`、`memory_last_synced_round`、`memory_started_at`、`memory_finished_at`、`memory_failure_code` 与 `memory_calls_real/double`；`simulation_memory_syncs` 以 `run_id` 为主键、`job_id` 唯一，同一 `simulation_id` 允许多次尝试并保留历史，状态与 pending 约束由表级 CHECK 裁决；验证方式是旧运行读取返回兼容空态、不伪造记忆；验证：旧数据卷兼容 + 状态约束测试
- [x] 1.4 验证迁移不触碰 `graph_snapshots`/`graph_nodes`/`graph_edges`/`graph_evidence`：迁移前后计数、哈希与 `published` 不变；验证方式是数据库断言与 `test_migrate`；验证：材料快照零变化；证据：运行前后 graph_nodes/edges/evidence 计数一致

## 2. 领域契约与记忆账本

- [x] 2.1 新增 `app/engines/temporal_memory.py`（或等价纯契约模块）：`memory_id_for(simulation_id, action_id)`、`episode_key_for`、`episode_idempotency_key_for`、`episode_content_for`、`node_key_for`、`edge_key_for`、`memory_ref` 数据结构与来源分层枚举；验证方式是纯函数测试断言确定性、同输入同 ID、不同运行不同 ID、正文不含材料正文/密钥；验证：确定性单测 + 敏感字段断言；证据：temporal_memory 纯函数确定性/规范化/无敏感字段测试
- [x] 2.2 新增记忆服务 `app/services/simulation_memory.py`：行动记账时写记忆与 episode 计划、按轮查询、按平台/轮次截断、读回节点/边/来源、状态聚合、最早未完成 episode 查询、重试任务映射；验证方式是服务层单元测试覆盖同轮多行动、跨平台截断、未处理不贡献节点、未确认不可读；验证：服务层单测；证据：tests.test_temporal_memory 服务层单测
- [x] 2.3 为 `plan_round()` 增加 `memory_refs` 输入与输出（不改变原有动作规划语义）：行动计划携带 `memoryRefs`，按 `round_index`/`action_seq` 稳定排序并受 `SIMULATION_MEMORY_MAX_REFS` 限制；验证方式是纯契约测试断言第 R 轮只收到 `<R` 且同平台引用、空记忆仍可规划、上限稳定；验证：计划契约单测；证据：计划 memoryRefs 只含前序轮次且同平台
- [x] 2.4 新增运行图谱绑定服务：先持久化创建幂等键与稳定参数，再 `graph.create`；响应丢失用同键重试，返回空/不同 UUID 按 `invalid_graph_scope` 失败；验证方式是 stub 图谱注入响应丢失、错误 UUID、已有绑定与验收图谱冲突；验证：绑定幂等测试；证据：绑定响应丢失重试同键；验收图谱 UUID 被 invalid_graph_scope 拒绝

## 3. 行动记账与记忆读取注入

- [x] 3.1 扩展 `simulation_store.write_round()`：在同一事务内为每条行动写入 `simulation_memory_entries` 与 `simulation_memory_episodes`（`state=planned`），写入 `source_event_seq` 与该轮模拟时间，重复 `(simulation_id, action_id)` 只校验不新增；验证方式是重复读回/重复记账测试断言行动、记忆、episode、计数与事件序号不增长；验证：记账事务幂等；证据：行动记账同事务写入记忆/episode，重复投影不增长
- [x] 3.2 在 `_round_plan()` 中查询同运行、同平台、`round_index < R` 的已提交记忆与冻结材料事实，注入 `plan_round(memory_refs=...)`；MUST NOT 查询图谱 API 做无约束检索；验证方式是构造平台 A 第 3 轮、平台 B 第 2 轮，断言 B 第 2 轮计划不含 A 第 3 轮或 B 第 2 轮记忆；验证：平台独立/未来排除单测；证据：B 第 2 轮只读 B 第 1 轮，排除 A 第 3 轮/当前轮
- [x] 3.3 把 `memoryRefs` 写入 `simulation_round_plans.request_payload`，并让引擎请求由该计划派生；行动记账后写入 `simulation_action_memory_reads`，以 `(action_id, memory_id)` 唯一；验证方式是端到端断言计划、引擎请求文件（保留 runtime 时）与读取记录三处 `memoryId` 一致；验证：执行入口证据测试；证据：计划、引擎请求文件与 memory_reads 三处 memoryId 一致
- [x] 3.4 为 `simulation_actions` 或详情读取增加来源行动定位所需字段（可复用现有字段，避免重复存储）；验证方式是从记忆来源能回到 `actionId` 且属于同一 run/platform；验证：回链服务测试；证据：来源回链同一 run/platform 的 actionId
- [x] 3.5 保证记忆条目在行动事务提交后立即可读；episode 仍为 `planned`/`in_flight` 时角色可读本地记忆但图不显示节点/边；验证方式是注入投影延迟，断言第 2 轮计划仍含第 1 轮 `memoryId` 且图中无伪造节点；验证：延迟投影测试；证据：延迟投影测试：计划仍有前序 memoryRefs，图不显示伪造节点

## 4. 增量投影、读回与来源分层

- [x] 4.1 在 `_advance_platform()` 每轮记账后增加尽力而为的增量投影调用：按 `action_seq` 写入/确认 episode、等待 `processed`、按本次 episode 集合读回节点/边、写 `simulation_memory_nodes`/`simulation_memory_edges`/`simulation_memory_provenance`；失败只更新记忆状态，不阻断另一平台或下一轮；验证方式是图服务故障注入，断言平台 B 继续、动作计数不变、`memory_status=unavailable`；验证：单平台记忆故障隔离；证据：记忆投影故障不影响两平台动作；unavailable 后恢复为 complete
- [x] 4.2 按 `NFKC` 规范化名称归并节点，按起点/终点/关系类型/规范化事实归并边；来源按 `facet` 分别保留 `role_speech` 与 `simulation_result`，重复来源去重；验证方式是同一角色跨轮多行动、同轮多行动、重复 episode 读回测试断言节点/边不重复、来源不丢；验证：归并与来源单测；证据：NFKC 节点键归并与 provenance 唯一键去重测试
- [x] 4.3 材料层只读：按 `simulations.snapshot_id` 读取 `graph_snapshots`/`graph_nodes`/`graph_edges`/`graph_evidence`，返回 `origin=material` 与来源；MUST NOT 写入材料表；验证方式是运行前后快照计数/哈希对比与写入语句白名单检查；验证：快照只读回归；证据：运行前后快照计数一致，材料层只读
- [x] 4.4 实现 `memory_status` 聚合：`unconfigured`/`pending`/`syncing`/`complete`/`partial`/`unconfirmed`/`unavailable`，并写入最后同步轮次、待处理/未确认计数、原因码；验证方式是各状态注入测试与详情字段断言；验证：状态聚合测试；证据：memory_status 聚合与详情字段
- [x] 4.5 实现按轮视图服务：`round_index <= R` 截断、可选 `platform`、`round` 合法性、`round_not_available`、已到达但未完成时 200 + 状态；验证方式是第 1/2/3 轮、跨平台、非法轮次、未到达轮次测试；验证：按轮视图单测；证据：第 1/2/3 轮截断与非法轮次/平台测试

## 5. 幂等、查账、中断与恢复

- [x] 5.1 实现 episode 写入状态机：写前持久化 `in_flight` 并递增 `attempts`；成功保存 UUID/`accepted`；响应丢失/空 UUID 进入查账；预算耗尽 `unconfirmed`；明确可重试拒绝进入 `rejected_retryable`；验证方式是 stub 注入响应丢失、首次搜索未命中、错误 UUID、认证失败与预算耗尽；验证：写入状态机单测；证据：in_flight 写入状态、查账补记与 add_count 不变
- [x] 5.2 实现查账：按 `simulationId`、`actionId`、`memoryId`、幂等键与 `content_sha256` 精确匹配；命中补记，MUST NOT 再次 `add`；只返回旧运行/旧 episode 时继续等待或 `write_unconfirmed`；验证方式是“服务端已写入、本地未确认”与“旧记录共存”测试；验证：查账测试；证据：已受理未确认查账补记，add_count 不变
- [x] 5.3 实现 worker 接手：锁定任务行、数据库实际时钟验证租约、先查账再继续，从最早未完成 episode 开始；验证方式是停止 worker 后恢复，断言 `attempts>1`、episode 数量不变、节点/边/来源不重复；验证：接手补记测试；证据：记忆同步任务 claim 后接手步骤与查账
- [x] 5.4 实现重复提交去重：同一行动重复进入记账/投影/同步只校验既有记录；节点/边/来源唯一约束拒绝重复；验证方式是连续两次同步与并发同步测试断言计数不变；验证：重复提交单测；证据：重复投影/同步计数不增长
- [x] 5.5 实现图服务不可达与恢复：动作继续、`memory_status=unavailable`、待同步数可见；恢复后从最早未完成 `memoryId` 继续；验证方式是故障注入 + 恢复后同步测试；验证：不可达恢复测试；证据：不可达状态保留并在恢复后从最早未完成 episode 完成

## 6. 记忆同步任务与接口

- [x] 6.1 新增 `simulation.memory` 任务创建/复用：操作键 `simulation-memory:v1:<simulationId>`，固定 `bind`、`sync`、`publish` 三步，复用现有租约/步骤/事件/终态；`simulation_memory_syncs` 每次尝试一行，`run_id` 主键、`job_id` 唯一，活动任务以 `jobs.status IN ('queued','running')` 复用，终态后允许同一 `simulationId` 新建并保留旧行；验证方式是并发重试一个 202 一个 200、worker 接手、终态后新任务；验证：任务队列单测 + Change 10 专属验收
- [x] 6.2 在 `app/worker.py` 按 `runs.kind=simulation.memory` 分派记忆同步执行器，保持测试任务、材料解析、建图、准备与模拟运行分支不变；验证方式是分派测试与默认 worker 导入测试；验证：分派与导入边界；证据：app.worker 与 simulation_worker 分派 simulation.memory
- [x] 6.3 实现 `POST /api/projects/{p}/simulations/{runId}/memory/retry`：先 `require_user`、再 `resolve_owned_project`、再运行归属；活动任务返回 200 同 `memoryJobId`，否则 202 新任务；验证方式是匿名 401、他人 404、并发 202/200、停库 503；验证：重试路由测试；证据：路由测试 202/200、401/404
- [x] 6.4 实现 `GET /api/projects/{p}/simulations/{runId}/memory?round=&platform=`：返回 `material`/`runtime`/`entries`/`status`/`cutoff`；非法轮次 400、未到达 409、平台非法 400、匿名 401、他人 404、停库 503；验证方式是路由与序列化测试；验证：记忆视图路由测试；证据：记忆视图路由与 Change 10 验收
- [x] 6.5 在运行详情 `serialize_simulation()` 增加 `memory` 摘要与 `memoryJobId`，不改变 Change 09 的动作/计数/终态字段；验证方式是对照字段快照断言旧字段不变；验证：详情兼容测试；证据：运行详情 memory 摘要与页面/验收

## 7. 第三阶段前端

- [x] 7.1 扩展 `web/src/simulation/state.ts`：`memory` 状态、`applyMemoryDetail`、`switchMemoryView`、`runId`+`round`+`platform` 守卫、状态文案与来源模型；验证方式是 `cd web && npm test` 覆盖迟到响应、切换运行/轮次/平台、未确认/不可用状态；验证：state 纯函数单测；证据：web state 单测：守卫、切换、状态文案
- [x] 7.2 扩展 `web/src/views/DualPlatformRunPanel.vue`：轮次选择器、平台筛选、材料/角色/推演图例、节点/边详情、来源行动回链、记忆状态徽标、重试入口；验证方式是页面测试与 `npm run typecheck`；验证：页面测试；证据：页面测试：轮次、图例、来源回链
- [x] 7.3 保留深链接参数：`layout`、`lang`、`build`、`preparation`、`selection`、`run`、`round`、`platform`；轮次切换使用 `replace`，阶段导航/返回控制台使用 `push`；验证方式是 `stage-links` 与页面测试；验证：链接参数测试；证据：轮次/平台切换保留 layout/lang 与 memory 请求参数，页面测试覆盖
- [x] 7.4 中英文文案：记忆状态、图层图例、来源类型、轮次/平台标签；角色名、项目名、材料名、节点名保持原值；验证方式是 i18n 一致性与页面文案测试；验证：中英文单测；证据：中英文记忆文案 typecheck + 页面测试
- [x] 7.5 刷新与迟到响应：从 URL 恢复视图、切换即解绑旧图、旧响应丢弃；验证方式是页面测试模拟延迟响应；验证：刷新/迟到响应测试；证据：刷新/切轮/迟到响应页面与 state 测试
- [x] 7.6 图服务不可达/未确认：动作流仍显示、记忆区显示暂不可用/待确认、重试按钮不换键；验证方式是页面测试；验证：异常状态页面测试；证据：不可用状态显示重试且动作流保留

## 8. 验收、报告与 CI

- [x] 8.1 在 `scripts/verify.py` 注册 `TEMPORAL_MEMORY_CHANGE_ID = "2026-11-25-update-temporal-memory"`；默认验收增加“未触发记忆任务”与 `temporal_memory_evidence.mode="not-triggered"`；未注册 `CHANGE` 保持退出码 2；验证方式是不带 `CHANGE` 与带 `CHANGE` 两种运行；验证：注册与未触发断言；证据：KNOWN_CHANGES 注册；默认未触发断言
- [x] 8.2 实现场景①：原创材料建图/准备/冻结 3 轮 → 运行 → 第 2 轮评论的 `round=1/2/3` 截断、`actionId`/`memoryId`/`episodeUuid`/`sourceEventSeq` 映射、同行多行动、来源分层、快照不变；验证方式是报告 `temporal-memory-full-chain`；验证：场景①通过；证据：temporal-memory-full-chain passed
- [x] 8.3 实现场景②：读取 `simulation_round_plans.request_payload` 与 `simulation_action_memory_reads`，断言第 3 轮只读同平台前两轮；平台 A 第 3 轮/平台 B 第 2 轮并发不泄漏；刷新/切轮稳定；同选择重跑 `overlap=0` 且旧运行可读；验证方式是报告 `temporal-memory-full-chain`、`temporal-memory-isolation` 与 `isolation`；验证：场景②通过；证据：temporal-memory-isolation passed，overlap=0
- [x] 8.4 实现场景③：重复提交 `growth=0`；响应丢失后中断接手先查账、`attempts>1`、只留一个 episode；未知 `write_unconfirmed` 可见且 `newKeyCreated=false`；图服务不可达后从最早未完成 `memoryId` 恢复；验证方式是报告 `temporal-memory-recovery`；验证：场景③通过；证据：temporal-memory-recovery passed，add 1→1
- [x] 8.5 覆盖边界：匿名 401、他人项目 404、不存在运行 404、非法轮次 400、未到达 409、停库 503 保留 Cookie、跨运行/跨平台来源未解析；验证方式是边界场景与报告字段；验证：边界场景通过；证据：temporal-memory-boundaries passed
- [x] 8.6 断言零真实外呼与证据模式：`calls.real=0`、`calls.double>0`、`graphProvider=stub`、`callScope` 来自 provider 边界逐运行持久化值；验证方式是伪造计划数不改变计数；验证：零外呼断言；证据：temporal_memory_evidence calls.real=0、double=24
- [x] 8.7 连续性：同一隔离 Compose 项目连续两次执行 Change 10 验收均通过，无需人工清理残留运行/记忆任务；验证方式是连续两次 `make verify CHANGE=...`；验证：连续两次通过；证据：连续两次 ALLOW_DIRTY=1 make verify CHANGE=2026-11-25-update-temporal-memory 均 passed
- [ ] 8.8 新增 CI Change 10 独立离线 job：调用同一 `make verify CHANGE=...`，上传独立 artifact，断言 `tested_sha`、`dirty=false`、`result=passed`、`calls.real=0`、三场景齐全、`snapshotUntouched=true`、`overlap=0`、`growth=0`、`reconciled=true`、未知状态可见且未换键；验证方式是 CI 运行与失败演练；验证：CI job 通过

## 9. 配置、文档与人工边界

- [x] 9.1 新增 `SIMULATION_MEMORY_*` 配置并同步 `.env.example`、`docker-compose.yml`、`app/config.py` 与 README 配置表；默认 `auto` 在无 Zep 且未允许 stub 时为 `unconfigured`，不发起外呼；验证方式是配置一致性测试；验证：配置同步测试；证据：配置已同步 .env.example / compose / config.py / README
- [x] 9.2 更新 README 第三阶段说明：轮次图、角色记忆、记忆状态、重试入口、stub 默认与真实 Zep 显式边界；验证方式是命令与示例可照抄执行；验证：README 可执行；证据：README 增加时序记忆与接口说明
- [x] 9.3 更新 `AGENTS.md`：运行级图谱隔离、材料快照只读、按轮/角色两套截断语义、执行入口记忆读取证据、重复提交/查账/未知状态/恢复规则；验证方式是逐条与 spec/design 对齐；验证：规则对齐；证据：AGENTS.md 增加时序记忆规则
- [x] 9.4 准备人工走查清单（不归档）：轮次切换、图例、来源回链、刷新恢复、迟到响应、未确认/暂不可用、中英文、三种布局；验证方式是 `docs/acceptance/` 模板字段齐全且本轮不新增记录；验证：清单待签收；证据：walkthrough.md 已准备，状态待签收
- [x] 9.5 确认私有样本边界：25 页报告、私有材料正文与截图只留在仓库外或已忽略目录，不进入夹具、日志、报告、截图或 CI 产物；验证方式是运行后扫描 `runtime/` 与报告；验证：私有边界检查；证据：Change 10 验收未使用私有材料或私有路径

## 10. 规格与验收映射

| 能力 | 覆盖场景 |
| --- | --- |
| `simulation-temporal-memory` | 运行级图谱隔离与材料只读、行动/记忆/episode/事件映射、按轮截断、角色前序记忆读取、增量投影与状态、重复提交、写入未知/接手/恢复、来源分层、访问边界 |
| `dual-platform-run` | 详情记忆摘要、记忆状态不改变动作终态、重跑运行记忆各自独立、按轮视图平台映射、同运行回链与未解析目标 |
| `background-job-queue` | `simulation.memory` 类型与操作键、三步事务、并发重试、接手、不写行动/终态、终态后新任务 |
| `project-workbench` | 第三阶段轮次时间线、平台筛选、三源图例、来源行动回链、同步状态、刷新恢复、迟到响应、中英文/布局 |
| `reproducible-verification` | 默认未触发、三核心场景、快照不变、重跑隔离、重复不增长、接手/未知/不可达、边界、零真实外呼、CI artifact |

## 11. 三个核心验收场景（操作、预期与证据）

| # | 操作 | 预期观察值 | 证据位置 |
| --- | --- | --- | --- |
| ① | 原创材料建图 → 准备 → 冻结 3 轮 → 运行；选中第 2 轮一条评论；依次查看 `round=1/2/3`；点击来源回链 | 第 1 轮不含该 `actionId`/`memoryId`，第 2/3 轮含；回链同一 `runId`、`platformKey`，显示轮次、模拟时间、`sourceEventSeq`、`episodeUuid`；同轮多行动独立；材料/角色/推演来源分层；冻结快照计数与哈希不变 | `temporal_memory_evidence.scenarios[0]`、`GET /memory`、`simulation_memory_*` 表、浏览器走查 |
| ② | 观察每轮图增长；平台 A 第 3 轮、平台 B 第 2 轮时检查 B 第 2 轮角色；刷新/切换轮次；同 `selectionId` 重跑 | B 第 2 轮 `memoryRefs` 只含 B 第 1 轮与材料事实；不含 A 第 3 轮/B 第 2 轮/未来；`simulation_action_memory_reads` 与计划一致；刷新稳定；重跑新 `runId`/`graphId`、`overlap=0`、旧运行可读 | `temporal_memory_evidence.scenarios[1]`、`isolation`、`simulation_round_plans.request_payload`、`simulation_action_memory_reads`、浏览器走查 |
| ③ | 重复提交同一行动；episode 已写入但本地未确认时中断；图服务不可达时继续运行；恢复后重试 | 重复后记忆/episode/节点/边/来源计数不增长；接手 `attempts>1`、先查账补记、只留一个 episode；未知 `write_unconfirmed` 可见且不换键；不可达时 `memory_status=unavailable`、待同步数可见；恢复从最早未完成 `memoryId` 继续 | `temporal_memory_evidence.scenarios[2]`、`simulation_memory_syncs`、`simulation_memory_episodes`、运行详情 `memory`、worker 日志（仅标识/原因码）、浏览器走查 |