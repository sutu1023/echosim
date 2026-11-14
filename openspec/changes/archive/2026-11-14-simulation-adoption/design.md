# Design

## Context

见proposal.md及ADR0004。Stories.submit只绑定确认设定，work_story_heads只有candidate_id/confirmed_id，story_tasks.kind只允许preparation/simulation，StoryWorker的else分支默认推演。需要扩展而不是让采纳任务误入推演。前置narrative-simulation提供段落级visibility和稳定源事件。新capability管理正式故事事实；story-graph负责这些事实的初始提取与可视化，不独立合并剧情。

## Goals / Non-Goals

Goals：从未采纳候选到唯一主线的单一原子提交；继续推演读取授权后的主线记忆；无分支交互。
Non-Goals：章节采纳、正文编辑、回滚、人工修正图、多个候选等待选择。基础结构不预建分支树。

## Decisions

### 1. 分离执行状态与结果决策

保留任务queued/running/stopping/succeeded/failed/stopped；另存decision=pending/checking/adopted/discarded/ineligible。有事件且成功或明确用户停止才pending；失败/零事件直接ineligible。作品头记录pending_simulation_id，终态发布与设置pending指针同事务，不能在两个请求之间产生新任务空隙。

submit在作品锁内先处理相同幂等重放，再检查活跃任务和pending。采纳或放弃才能清空pending；放弃只改决策、不删历史。新模拟输入记录canon_revision，旧历史无此字段始终ineligible。没有分支选择，也不允许“先开B再决定A”。

### 2. 新增状态与事实契约

新增主线头（work/user、revision、confirmed_version_id、pending_simulation_id）、采纳记录（唯一source_simulation_id、base/new_revision）、正式事件引用、角色记忆、版本化story_facts。所有归属复合外键沿用现有模式；事实快照与canon_revision同时发布。

事实由entities与relations组成：实体有稳定id、kind=character/place/object、name、属性；关系含稳定id、subject/predicate/object、当前有效状态；每条事实及每项记忆都包含evidence(version/field或simulation/event/segment)与visibility。人物id锚定确认角色，其他实体服务器分配作品内id；变更使用upsert/retire，不靠字符串同名自动合并。比如钥匙转交是同一实体的持有关系替换。过去关系保留依据但不再标为当前。实体属性也带来源与可见性，不能把秘密藏入公共标签。

主线事实是graph的权威输入，graph读模型是确定性投影而非另一套异步写入；因此同一事务的事实版本就是图谱数据版本。采纳change单独验证时可使用确认设定输入构造事实基线；graph change把基线提前到确认后持久构建并统一复用同一schema，避免循环依赖。

### 3. 采纳先检查、后短事务提交

POST /api/v1/works/{work_id}/simulations/{id}/adoptions，包含expected_canon_revision与acknowledge_irreversible=true、Idempotency-Key，返回202任务。事务内授权、资格/版本检查、作品锁、绑定当前有效模型配置、创建adoption任务并将decision置checking。采纳使用提交时有效配置，不复用可能已清理密文的旧模拟配置。

worker在事务外读取不可变来源，重新抽取事件支持的事实变化和角色记忆，检查既成事实、唯一物品状态、角色知识、来源真实性、无依据推测。每次调用60秒、最多2次重试、1/2秒退避，总任务600秒；结构/引用校验为确定性，语义冲突校验不能绕过。对最多24事件分批提取时只允许完整结果最终发布，不部分采纳。

最终事务重新锁作品，检查授权、fence、pending任务、确认版本与base_revision，原子写正式事件、记忆、完整事实快照、adoption记录和新revision，清pending并将task/decision置成功。任一写失败全回滚。失败回收必须把checking还原pending并释放活跃任务，不能遗留永久占用；重试用新key，旧key继续返回原失败任务。合法重复采纳返回原采纳结果，无第二个revision。

POST .../discards采用幂等键，仅在pending时执行；checking期间返回WORK_BUSY。采纳/放弃竞态通过同一锁决出一个结果。错误采用既有error/request_id约定：RESULT_PENDING、RESULT_INELIGIBLE、CANON_CONFLICT、PREPARATION_FROZEN为409；结构非法422、越权404。

### 4. 连续推演与冻结设定

新模拟从绑定revision获取角色可见的正式事实及记忆，再加入本任务可见段落，不向角色传创作者完整图谱。事实过滤先于拼接/摘要；对于累积上下文超出已验证预算，返回明确容量错误，不截断隐藏事实或做未经校验的全知摘要。首版使用有界近期事件+持久事实及各角色记忆，预算上限通过10次连续任务的固定集验证后记入运行文档；容量不足阻止调用、不伪装无限长篇支持。

首次采纳后生成、改写、重新确认均409；候选和旧确认仍只读。首次采纳前有pending也阻止改写，放弃后可重写。预置作品没有canon时revision=0。首次提交与准备确认竞态沿用同一作品锁。

### 5. 页面与确认

终态展示等待处理区，“采纳本次推演”和“放弃本次结果”；提醒采纳整次且不可撤销，停止任务标注提前结束。用户尚有未播放文本时可显示剩余剧情再确认，不强制等待逐字动画。checking显示异步状态，失败提供安全原因、重试和放弃。按钮禁用只是提示，资格在服务端验证；刷新恢复，跨标签页用最新revision处理409。

## Risks / Trade-offs

- [不可逆误采纳] → 明确整次确认和冲突检查；本轮不增加撤销承诺。
- [模型抽取与身份歧义] → 只接受源支持的事实和稳定ID，歧义阻止采纳，不静默猜测；失败可重试或放弃。
- [秘密经记忆/事实摘要泄漏] → 属性、关系、记忆均保留visibility和来源，先过滤再构造上下文，交叉用户/角色测试分开。
- [规模与500行门槛] → 复用任务、配置、锁和结构化生成，评审业务变更总量；超出时拆出独立事实构建能力，不默许超限。任务清单保持可分组执行但不提前声称满足门槛。

## Migration Plan

在005后追加006迁移扩展kind与主线/决策/事实结构，worker改为显式kind分派。旧任务标记legacy/ineligible，旧作品revision=0，不自动选择旧结果，也不阻塞新任务。部署前停止领取并排空旧worker；新worker先支持所有kind再开放采纳入口。任何回退保留正式数据，禁用新采纳和推演写入，修复后向前迁移；不能让旧代码绕过pending或冻结规则。归档顺序在narrative-simulation之后，本change只新增role-simulation.S10，避免覆盖其S1–S9。
