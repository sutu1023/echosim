# Proposal

## Why

现有每次推演只读取确认设定，剧情无法连续推进。用户选择直接采纳完整推演，并要求未采纳结果不污染主线、角色记忆或图谱，因此需要唯一主线及一致的采纳边界。

## What Changes

- **BREAKING** 按ADR0004允许直接采纳推演，不再以生成章节为前提。
- 一个作品只能有一个待处理结果；先采纳或放弃，才能继续。成功或主动停止且非空的结果可采纳，失败/空结果不可采纳；放弃永久只读。
- 采纳前生成并校验有来源的事实变化与角色记忆，冲突阻止提交；一次事务写入采纳记录、正式事件、角色记忆及可供图谱读取的结构化事实版本。
- 下一次推演绑定当前主线；首次采纳后锁定设定生成、重写和重新确认；不提供分支、回退、逐条采纳或编辑事件。
- 新增幂等采纳/放弃操作、异步校验状态与不可逆提示；保留旧推演为只读历史。
- graph change只负责初始设定事实提取和图谱读取展示，不另设一套采纳事务；本change提供唯一事实版本契约。

## Capabilities

### New Capabilities

- `simulation-adoption`: 顺序推演、整次采纳/放弃、正式剧情和角色记忆、原子事实变更。

### Modified Capabilities

- `story-preparation`: 首次采纳后冻结设定，待处理推演期间阻止改写/确认。
- `role-simulation`: 新增主线版本绑定与角色可见的跨任务记忆合同，不重写前置change修改的S1–S7。

## Impact

依赖2026-11-14-narrative-simulation；影响Stories.submit/confirm、StoryWorker分派、角色上下文、持久任务kind与作品锁、追加主线/决策/事实/记忆迁移、服务与HTTP端点、推演及准备页。保持api→services→domain和事务外模型调用。
正式事实结构是story-graph的输入；最终联合验收必须覆盖图谱同步。实现分支为codex/2026-11-14-simulation-adoption。
