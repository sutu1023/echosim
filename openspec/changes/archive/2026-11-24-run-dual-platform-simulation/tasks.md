# Tasks

## 0. 交付边界

| 类别 | 内容 |
| --- | --- |
| 本轮交付 | 从冻结选择启动运行、双平台原生动作与隔离环境、worker 四步执行与按平台轮次游标、引擎记录读回与目标原帖定位、停止与终态冻结、中断接手补记、单平台失败的部分结果、第三阶段运行面板、默认零外呼与 Change 09 离线验收 |
| 依赖输入 | Change 08 的 `frozenSelection`（`selectionId`、`buildId`、`snapshotId`、`question`、轮数、角色、平台映射、初始话题、首发顺序）；Change 06 的双平台探针与引擎镜像；Change 07 的租约/记账纪律 |
| 留给后续 | 报告生成（Change 10 之前）、深度互动、时序记忆写回（Change 10）、真实外网平台与账号凭据、动作 CSV 导出 |
| 作者亲测 | 私有项目中用原创材料走查时钟、行动流、目标原帖、停止与重跑、旧运行回看、中英文与三种布局；真实本地 OASIS 证据另用显式命令产出 |

## 1. 数据模型与前向迁移

- [x] 1.1 新增 `app/migrations/0013_dual_platform_runs.sql`：扩展 `runs_kind_check` 加入 `simulation.run`，为 `environment_frozen_selections` 增加 `(project_id, selection_id)` 唯一约束，创建 `simulations`、`simulation_platforms`、`simulation_rounds`、`simulation_actions` 及索引；验证方式是在空数据卷与已有 Change 01–08 数据卷上各启动一次，迁移只执行一次且旧表数据不变；验证：migration 0013 applied on empty + existing 0012 schema
- [x] 1.2 补齐约束测试：非法 `kind`、`platform_key` 与 `native_platform` 映射不一致、`round_index < 1`、重复 `engine_record_id`、重复 `action_seq`、非法 `native_action`、非法 `engine_mode`、跨项目选择外键；验证方式是 `.venv/bin/python -m unittest tests.test_migrate` 覆盖重复迁移与非法插入；验证：迁移断言 + 非法 engine_mode/平台映射插入被拒绝
- [x] 1.3 新增运行持久化服务 `app/services/simulation.py`：创建运行与 job/run、按选择去重、读取详情与分页动作、按平台聚合计数、写入轮次与动作、停止与终态、清理记录；验证方式是服务层单元测试覆盖 202/200 去重、聚合计数、游标越界与终态写入拒绝；验证：服务层单测：202/200 去重、聚合计数、终态重写、停止冻结
- [x] 1.4 为运行记录增加复合外键校验：选择、build、快照与准备必须属于同一项目；验证方式是数据库测试断言跨项目/跨 build 插入被拒绝且不留下孤立 job/run；验证：复合外键 + 跨项目选择 404 + 无记录断言

## 2. 启动与读取接口

- [x] 2.1 新增 `app/routers/simulations.py`：开始、列表、详情、动作分页与停止五个端点，全部先 `require_user` 再 `resolve_owned_project`；验证方式是路由测试断言匿名 401 不访问数据库、他人项目 404 与不存在项目响应完全一致；验证：路由单测：匿名 401（不访问数据库）、他人项目 404 完全一致
- [x] 2.2 实现开始请求的冻结选择校验：缺少/非法/他人/不完整分别返回 `selection_missing`/`selection_invalid`/404 `推演选择不存在`/`selection_invalid`，且不创建任何记录；验证方式是在四类请求后统计 run/job/simulation 行数为 0；验证：缺选择 404/409、非法 UUID 422、无运行记录
- [x] 2.3 实现 runId 语义：活动运行 200 复用、并发开始一个 202 一个 200、终态后 202 新运行、默认读取活动优先其次最近、`?run=` 打开旧运行；验证方式是并发请求测试与数据库唯一索引冲突测试；验证：202/200/终态新 runId/旧运行可读；并发由 jobs_active_operation_idx 唯一索引裁决
- [x] 2.4 实现详情与动作响应体：`simulatedAt`、`simulatedStart`、每平台 `roundsCompleted/actionCount/byAction/failure`、总计数、`partial`、`failedPlatforms`、`canStop`、`targetResolved` 与分页 `nextAfter`；验证方式是对照设计文档字段表逐项断言；验证：详情与动作字段断言（页面测试 + verify）
- [x] 2.5 处理数据库不可达：接口返回 503 且不清除 Cookie；验证方式是停止数据库后调用详情与停止接口，断言 Cookie 未被清除且恢复后可继续读取；验证：停库 503 保留 Cookie 路由测试

## 3. 双平台引擎契约与替身引擎

- [x] 3.1 新增引擎契约 `app/engines/duet_contracts.py`：平台映射、允许动作白名单、轮次请求/结果、动作记录、目标引用、理由码与执行结果校验；验证方式是纯契约测试覆盖非法平台、非法动作、空内容、缺失目标与越界轮次；验证：契约单测：非法轮数/映射/前缀/首发话题、动作覆盖
- [x] 3.2 新增确定性替身引擎 `app/engines/duet_double.py`：按运行标记、轮次、角色与冻结话题生成动作，写入 `post`/`comment`/`trace`/`sim_round` 表与 `actions.jsonl`，同输入两次执行结果一致；验证方式是单元测试断言两平台动作、轮次标记、目标原帖与确定性；验证：替身引擎单测：原生表、确定性、目标原帖
- [x] 3.3 新增引擎子进程入口 `app/engines/duet_runner.py`：按 `--platform`、`--runtime`、`--marker` 启动，逐轮读取请求、执行、写引擎记录并输出 `round_done`；验证方式是子进程端到端测试断言数据库行与 stdout 摘要一致；验证：runner 子进程端到端（替身 + 引擎镜像真实 OASIS）
- [x] 3.4 新增 worker 侧独立读回 `app/services/simulation_engine.py`：只读 SQLite 按运行标记与轮次读取动作，核对原生动作、角色、目标与内容标记；验证方式是测试覆盖记录缺失、字段不符、跨轮次混入与重复读回幂等；验证：独立读回：标记校验、原生目标校验、幂等
- [x] 3.5 实现轮次激活规则：模拟小时、`activeHours`、`platformKeys`、`orderIndex`、首发角色与首发话题、每轮动作上限；验证方式是纯函数测试覆盖首发角色不在活跃时段仍行动、无候选轮次、动作数上限与顺序；验证：轮次激活单测：首发强制、活跃时段、上限、首发小时

## 4. worker 四步执行、轮次游标与接手

- [x] 4.1 实现 `execute_simulation` 四步：`bind`、`environment`、`advance`、`publish`；每步复用锁定任务行、数据库实际时钟、租约令牌、步骤唯一约束与事件事务；验证方式是单元测试覆盖正常四步、发布前崩溃与步号推进；验证：四步执行 + 事件 1..10 连续（worker 单测 + verify）
- [x] 4.2 在 `app/worker.py` 按 `runs.kind=simulation.run` 分派，保持测试任务、材料解析、建图与准备分支不变；验证方式是单元测试断言五类 kind 分派正确且互不串线；验证：app/worker.py 与 app/simulation_worker.py 分派，端到端跑通
- [x] 4.3 实现 `advance` 的轮次事务：读回 → 写动作 + 轮次 + 平台计数 + 事件同事务提交，`(simulation_id, platform_key, engine_record_id)` 幂等；验证方式是重复读回测试断言动作数、事件数与计数不变；验证：轮次事务幂等：重复读回不新增动作/事件
- [x] 4.4 实现接手：读取引擎 `sim_round` 与动作，比对已提交轮次，`committed` 补记、无标记则继续、不可读回则 `action_unconfirmed`；验证方式是模拟 worker 停止与租约过期，断言 `attempts>1`、无重复动作、计数与引擎一致；验证：接手：attempts=2、补记后与干净运行计数完全一致
- [x] 4.5 实现终止与超时：单轮预算、单运行动作上限、轮次不超过冻结值、平台失败原因码；验证方式是超时、超限与失败注入测试断言稳定原因码且不越界；本轮状态：本轮只做了实现与停止/失败注入验证；单轮超时与动作上限没有专门用例，留待下一轮补齐；验证：单轮超时与共享动作上限：`test_round_timeout_books_confirmed_actions_once`、`test_shared_budget_blocks_before_engine_call`，核对原生库与业务库无超额/重复
- [x] 4.6 实现终态清理：终态事务后清理运行目录并记录清理结果，清理失败只记录原因码；验证方式是模拟清理失败断言终态不变且报告含 `platform_*_cleanup`；本轮状态：终态清理已实现，但清理失败路径没有专门注入用例；验证：清理在终态后执行；`test_cleanup_failure_keeps_terminal_state` 注入 `SIMULATION_CLEANUP_FAIL`，终态/计数不变并记录 cleanup

## 5. 停止、终态与单平台失败

- [x] 5.1 实现停止接口与取消事务：job `canceled` + `simulations.phase='canceled'` + 终态事件 + 结束时间同事务，已写入引擎的那一轮先读回再记账；验证方式是运行中停止测试断言 `canceled`、终态 `seq` 与计数冻结；验证：停止：canceled、计数冻结、重复停止 cancelApplied=false
- [x] 5.2 实现终态写入拒绝：`_book_round()` 与事件追加验证“运行未终态 + 租约有效”，迟到结果被丢弃；验证方式是终态后调用记账函数断言影响 0 行、计数与 `lastSeq` 不变；验证：记账与事件守卫：终态后影响 0 行；取消事务含运行终态
- [x] 5.3 实现完成与取消竞争：只有先提交的一方生效且只有一个终态事件；验证方式是用数据库锁与真实事务复现两侧竞争，断言终态唯一；本轮状态：完成/取消竞争未做专门并发用例（现有取消与终态测试不覆盖该竞争）；验证：`test_terminal_race_keeps_one_winner_in_both_orders`：两个真实事务按两种顺序 + 并发，只有一个终态事件
- [x] 5.4 实现单平台失败：失败平台停止、另一平台继续、运行 `failed` + `failedPlatforms` + `partial=true`；验证方式是注入 `platform_a_action`/`platform_b_record` 断言另一平台完成且其结果可读；验证：strip_marker 注入：平台 A failed、平台 B 3 轮保留
- [x] 5.5 实现两个平台都失败与 `engine_unavailable`、`action_unconfirmed` 的终态表达；验证方式是故障注入测试断言原因码、`failedPlatforms` 与不产生替身记录；本轮状态：action_unconfirmed 与 engine_unavailable 已覆盖；两平台同时失败未单独构造；验证：`test_both_platforms_fail_after_first_round`：两平台第 2 轮同时不可确认，failedPlatforms、各自原因与第 1 轮已确认动作均保留

## 6. 事件与页面状态

- [x] 6.1 定义运行事件载荷并接入 `run_events`：只含运行/选择/build/快照标识、轮次、平台、计数与原因码，不含动作正文；验证方式是事件序列测试断言每平台每轮一条、终态一条、零正文；验证：事件载荷与连续性（verify 断言 10 条连续、无正文）
- [x] 6.2 实现 `WORKER_RUN_KINDS` 领取过滤：未声明的 job 保持 `queued`，默认值覆盖五类；验证方式是领取测试断言 kind 过滤、默认值与未知 kind；验证：两 worker 在线实测：double 由替身 worker、oasis 由真实 worker 领取
- [x] 6.3 前端新增 `web/src/simulation/state.ts`：`runId`+`selectionId` 守卫、详情优先事件补齐、时钟与 `action_seq` 排序、终态冻结、停止请求态、计数只取详情值；验证方式是 `cd web && npm test` 的纯函数测试覆盖迟到响应、重复事件、计数漂移与终态；验证：state 单测：runId 守卫、去重排序、事件唤醒、终态冻结
- [x] 6.4 重写 `web/src/views/DualPlatformRunPanel.vue`：冻结摘要与开始入口、时钟、两条行动流、目标原帖跳转、按平台统计、运行日志、停止、旧运行选择、引擎模式徽标与七种状态；验证方式是 `npm test` 页面级测试与 `npm run typecheck`；验证：页面测试：时钟/行动流/统计/停止/旧运行/503
- [x] 6.5 补齐中英文文案与动作标签：界面词“发帖/转发/点赞/发主题/评论/投票赞成/投票反对”与英文对应，项目名、材料名与角色名保持原值；验证方式是 `i18n` 一致性测试与 `npm test`；验证：中英文动作与引擎标签单测 + 页面文案
- [x] 6.6 保留阶段链接查询参数：`layout`、`lang`、`build`、`preparation`、`selection`、`run`；验证方式是 `stage-links` 测试断言切换布局/语言与阶段导航不丢参数；验证：链接保留 selection/run，页面测试断言切换运行后 query

## 7. 真实本地 OASIS 运行路径

- [x] 7.1 新增 `app/engines/duet_oasis.py`：复用 `camel-oasis` 的 `twitter`/`reddit` 平台与 Chat Completions 传输，执行同一轮次契约并写同一引擎表；缺少依赖时抛 `engine_unavailable`，不回退替身；验证方式是假 platform 与 import 缺失测试；验证：真实 OASIS 引擎镜像内执行 4 类原生动作并读回
- [x] 7.2 重构导入边界：`app/services/worker.py` 的建图/模型 provider import 改为函数内惰性导入，新增 `app/simulation_worker.py` 只处理 `simulation.run`；验证方式是导入图测试断言不加载 `zep_cloud` 与建图模块；验证：app/simulation_worker.py 不加载建图依赖；默认 worker 行为不变
- [x] 7.3 新增 `Dockerfile` 的 `worker-simulation` 目标（基于 `runtime`，安装 `app/requirements-engines.txt`），不修改 `app`、`worker-graph` 目标与既有依赖文件；验证方式是构建后断言三个目标依赖互不覆盖；验证：Dockerfile 新增 worker-simulation 目标，未改动既有目标
- [x] 7.4 新增 Compose `worker-simulation` 服务与 `simulation` profile，`WORKER_RUN_KINDS=simulation.run`、`SIMULATION_ENGINE=oasis`；`make up` 默认不启用；验证方式是默认与 `SIMULATION=1` 两种编排检查；验证：Compose simulation profile + WORKER_RUN_KINDS/WORKER_SIMULATION_ENGINE
- [x] 7.5 记录该镜像依赖的“解析 → 安装 → 导入 → 最小运行”四阶段结果；未完成时报告写 `dependency=unverified`；验证方式是在目标 Linux aarch64/Python 3.11 容器内执行并保存结果；本轮状态：worker-simulation 依赖组合未完成四阶段验证，标记 dependency=unverified；验证：`make simulation-worker-check` 在 Linux aarch64/Python 3.11 镜像内完成解析→安装→导入→最小运行；`make up SIMULATION=1` 启动专用 worker 并由 `ECHOSIM_LIVE_MODE=worker make simulate-live` 跑通普通启动 API
- [x] 7.6 新增 `make simulate-live` 与 `scripts/simulate_live.py`：沿用 `ALLOW_DIRTY` 约定、运行时生成原创微型夹具、在隔离镜像内跑一次双平台运行、读回两个引擎数据库并写 `runtime/simulation-live/report.json`（`mode=live`、`engine=oasis`、`calls.real>0`、`tested_sha`、`dirty`）；验证方式是脏工作树拒绝与报告字段断言；验证：make simulate-live 产出 mode=live 报告（engine=oasis, externalModelCalls=0）

## 8. 验收、报告与 CI

- [x] 8.1 在 `scripts/verify.py` 注册 `2026-11-24-run-dual-platform-simulation`：默认验收追加“无冻结选择不触发运行”与 `simulation_evidence` 未触发写法；验证方式是不带 `CHANGE` 与带 `CHANGE` 两种运行；验证：SIMULATION_CHANGE_ID 注册；默认 simulation-not-triggered 通过
- [x] 8.2 实现时延/故障开关：`SIMULATION_ROUND_DELAY_MS` 放大停止与接手窗口，替身故障注入用于单平台失败；验证方式是场景中读取环境变量后能稳定复现；验证：SIMULATION_ROUND_DELAY_MS / SIMULATION_FAULT / DUET_CHILD_SLEEP_MS 实测
- [x] 8.3 实现 Change 09 场景：原创材料准备出 6 个角色（样例值，断言取实际冻结角色数）并冻结 3 轮、双平台动作读回、目标原帖与三方计数、停止后计数稳定、同选择重跑新 `runId`、旧运行可读、接手补记、单平台失败、边界与未知 `CHANGE` 退出码 2；验证方式是报告逐项记录 `simulation_evidence.runs[]`；验证：Change 09 五个场景全部 passed（含接手与部分失败）
- [x] 8.4 断言零真实外呼与证据模式：`calls.real=0`、`calls.double>0`、`engine=double`、`callScope` 列出被统计的 `runId`，计数取自引擎调用边界的逐运行持久化值；验证方式是伪造计划轮数不改变计数的测试；验证：calls.real=0、calls.double=5、engine=double 断言
- [x] 8.5 更新 `.github/workflows/` 以保留默认 `make verify` 与 Change 09 独立离线检查，使用不同 artifact 名称并断言 `tested_sha`、`dirty=false`、`result=passed`、`calls.real=0`；验证方式是在 CI 上各跑一次；本轮状态：CI 工作流尚未新增 Change 09 独立离线检查；验证：.github/workflows 新增 verify-change-09 job、独立 artifact 与 report 字段断言
- [x] 8.6 断言连续两次执行通过、无需人工清理残留运行与引擎目录；验证方式是在同一隔离 Compose 项目连续执行两次 Change 09 验收；验证：连续两次 make verify CHANGE=... 均通过

## 9. 配置、文档与人工边界

- [x] 9.1 新增配置项并同步 `.env.example`、`docker-compose.yml`、`app/config.py` 与 README 配置表：`SIMULATION_ENGINE`、`SIMULATION_DATA_DIR`、`SIMULATION_ACTIONS_PER_ROUND`、`SIMULATION_ROUND_TIMEOUT_SECONDS`、`SIMULATION_ROUND_DELAY_MS`、`SIMULATION_MAX_ACTIONS`、`WORKER_RUN_KINDS`；验证方式是配置一致性测试断言名称、默认值与占位语义；验证：.env.example / compose / config.py / README 同步新增配置
- [x] 9.2 更新 README 的第三阶段说明、`make up SIMULATION=1`、`make simulate-live` 与证据区分；验证方式是配置表与命令示例可照抄执行；验证：README 新增“双平台运行”章节与配置表
- [x] 9.3 更新 `AGENTS.md` 的双平台运行条目：启动只读冻结选择、每平台隔离进程与数据、读回纪律、停止/终态/接手、默认零外呼与真实证据分离；验证方式是逐条与 spec/design 对齐；验证：AGENTS.md 新增双平台运行规则与验收条目
- [x] 9.4 准备人工走查清单（不归档）：时钟、行动流、目标原帖、停止与重跑、旧运行、中英文、三种布局、离线与真实证据区分；验证方式是 `docs/acceptance/` 模板字段齐全且本轮不新增记录；本轮状态：人工走查清单未归档（本栈没有浏览器走查记录）；验证：浏览器走查清单已准备（openspec/changes/.../walkthrough.md，状态：待签收）；尚未由作者签收
- [x] 9.5 确认私有样本边界：25 页私有报告与含正文截图只留在仓库外或 `private-inputs/`，不进入夹具、日志、报告或 CI 产物；验证方式是运行后检查 `runtime/` 与报告无私有路径或正文；验证：运行后检查报告与 runtime 无私有材料路径或正文

## 10. 规格与验收映射

| 能力 | 覆盖场景 |
| --- | --- |
| `dual-platform-run` | 冻结选择启动与拒绝、202/200/新运行语义、双平台原生动作与隔离、引擎进程与数据位置、读回与幂等、接手与 `action_unconfirmed`、时钟与轮次、目标原帖、停止与终态冻结、单平台部分结果、计数三方一致、证据模式、匿名/他人/停库边界 |
| `dual-platform-simulation` | 探针判定不变、运行不冒充联通签收、别名与原生映射一致 |
| `background-job-queue` | 四步与操作键、重复开始一组记录、接手续跑、取消事务与终态写入拒绝、完成与取消竞争、`WORKER_RUN_KINDS`、测试面板隔离 |
| `run-event-stream` | 每轮事件同事务、终态事件一致、先详情后事件、迟到事件丢弃、事件订阅先校验归属 |
| `project-workbench` | 四态与第三阶段七种状态、时钟与行动流、目标原帖跳转、停止、单平台失败、旧运行与刷新、SSE 重连、替身徽标、深链接参数 |
| `reproducible-verification` | 默认未触发与零外呼、冻结 3 轮双平台读回、目标与三方计数、停止与重跑、接手补记、单平台失败、边界与未知变更、真实 OASIS 与私有样本分离 |
