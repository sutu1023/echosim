# Proposal

## Why

当前健康页只能证明 API 和数据库可用，不能验证真实模型调用。用户希望在本地前端输入 OpenCode Go KEY，明确判断后端能否调用指定模型；公网部署暂缓，M0 仍未完成。

## What Changes

- 新增仅开发环境显式启用的临时模型连接测试页和后端接口。
- 固定 OpenCode Go 地址及 deepseek-v4.1-flash，用户仅输入当次 KEY；不持久化、不回显、不记录，完成后清空输入。
- 固定短提示、非流式、60 秒总超时、无自动重试；展示成功、耗时和可用 token 用量，分类呈现失败。
- 不包含公网、登录、持久配置、自由聊天、embedding、SSE、创作能力或备份恢复。不是 P13 的完整实现。

## Capabilities

### New Capabilities

- `model-connection-test`: 本地开发环境中的临时模型连接测试、凭据生命周期及结果判定。

### Modified Capabilities

无。根状态页和健康检查保持现有契约；新增独立开发页面，不开放作品等业务资源。

## Impact

涉及 backend 的 api/services/domain/adapters、配置和 main 组装，frontend 独立页面与开发代理，依赖锁文件、测试及运行说明。无数据库 schema、worker 或 Render 资源变更。补充产品当前边界、追溯和验证记录；不声称完成模型质量评测或 M0。
