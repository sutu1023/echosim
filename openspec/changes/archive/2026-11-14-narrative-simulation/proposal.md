# Proposal

## Why

当前固定三角色三轮、简短行动/对白/结果的推演缺乏可读性和自然互动；已有单事件违规评测不能证明完整故事推进。创作者需要能实时跟随、动作与对白清晰分离的连贯剧情。

## What Changes

- **BREAKING** 新任务取消固定九事件与轮次，三名已确认角色在一个场景内动态行动；目标12–18事件，硬上限24，允许阶段性收束或未完待续。
- 新事件为3–6段有序动作旁白/对白，约200–400字作为质量目标；校验通过后完整持久化，前端再逐字播放，支持立即显示、回看时暂停跟随。
- 隐藏参与者清单、轮次与可见性标签，保留姓名和段落种类；后台仍严格隔离角色知识。
- 增加推进、因果、重复与完整任务评测；保留停止、重连、租约、授权与失败历史。
- 不包含采纳、图谱、运行中指导、章节、更多角色或跨场景；旧九事件历史继续可读。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `role-simulation`: 动态调度、丰富事件、校验后逐字播放、完整叙事验收，替代固定三轮合同。

## Impact

影响 services/simulation.py、domain/story.py 或独立事件契约、simulation_store.py、api/simulation.py、worker期限配置、追加迁移、Simulation.tsx及样式/测试、evaluate_simulation.py。扩大数据库sequence约束与读取游标；新旧事件需版本化兼容。无需新增外部服务。
这是三个change的第一项，后续依次为simulation-adoption与story-graph；单项完成不代表整轮交付。实现分支为codex/2026-11-14-narrative-simulation。
