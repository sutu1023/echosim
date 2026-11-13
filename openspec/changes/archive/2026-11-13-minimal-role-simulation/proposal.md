# Proposal

## Why

确认的故事准备需要产生可观察的角色行动与剧情，才能证明核心创作价值。

## What Changes

- 3 名角色、3 轮、单场景的真实角色推演；已校验事件逐条展示，停止与刷新补取、失败保留历史。
- 依赖 2026-11-13-persistent-model-config 和 2026-11-13-generated-story-preparation。
- 三项联合交付以本地真实浏览器、真实模型跑通整条链路为完成条件；单项完成不代表联合目标或 M1 完成。
- 本轮不包含 embedding、材料导入、GraphRAG、章节生成与采纳、公网部署。

## Capabilities

### New Capabilities

- `role-simulation`: 3 名角色、3 轮、单场景的真实角色推演；已校验事件逐条展示，停止与刷新补取、失败保留历史。

### Modified Capabilities

无。既有开发诊断仍保持临时密钥与固定提示的合同；既有认证和作品合同继续适用。

## Impact

推演工作区、角色与架构智能体、事件持久化、SSE、任务停止与模型评测。

依赖 2026-11-13-persistent-model-config 和 2026-11-13-generated-story-preparation。
