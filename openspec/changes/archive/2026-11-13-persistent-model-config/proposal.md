# Proposal

## Why

当前诊断只临时接收密钥，无法为创作任务提供可重启恢复的用户模型配置。

## What Changes

- 持久生成模型配置、连接验证、密钥加密、用户隔离与配置版本绑定。
- 无前置活动变更；后续 story-preparation 与 role-simulation 依赖本能力。
- 三项联合交付以本地真实浏览器、真实模型跑通整条链路为完成条件；单项完成不代表联合目标或 M1 完成。
- 本轮不包含 embedding、材料导入、GraphRAG、章节生成与采纳、公网部署。

## Capabilities

### New Capabilities

- `persistent-model-config`: 持久生成模型配置、连接验证、密钥加密、用户隔离与配置版本绑定。

### Modified Capabilities

无。既有开发诊断仍保持临时密钥与固定提示的合同；既有认证和作品合同继续适用。

## Impact

模型设置页面、模型服务和适配器、编号数据库迁移及环境主密钥。

无前置活动变更；后续 story-preparation 与 role-simulation 依赖本能力。
