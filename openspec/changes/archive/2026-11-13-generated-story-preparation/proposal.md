# Proposal

## Why

作品目前只有标题，用户需要输入一句创意即可获得可确认的故事信息与角色。

## What Changes

- 一句话生成完整候选设定，支持自然语言整版重写和显式整版确认；附带该能力需要的持久任务执行。
- 依赖 2026-11-13-persistent-model-config；后续 minimal-role-simulation 消费确认版本及任务执行机制。
- 三项联合交付以本地真实浏览器、真实模型跑通整条链路为完成条件；单项完成不代表联合目标或 M1 完成。
- 本轮不包含 embedding、材料导入、GraphRAG、章节生成与采纳、公网部署。

## Capabilities

### New Capabilities

- `story-preparation`: 一句话生成完整候选设定，支持自然语言整版重写和显式整版确认；附带该能力需要的持久任务执行。

### Modified Capabilities

无。既有开发诊断仍保持临时密钥与固定提示的合同；既有认证和作品合同继续适用。

## Impact

故事准备工作区、任务服务/worker、设定与角色版本迁移、生成提示词与质量评测。

依赖 2026-11-13-persistent-model-config；后续 minimal-role-simulation 消费确认版本及任务执行机制。
