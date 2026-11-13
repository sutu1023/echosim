# Proposal

## Why
EchoSim 需要可重复启动和验证的工程基线，才能逐步实现小说创作闭环。当前只有初始化配置，尚无运行代码或可执行验收标准。

## What Changes
- 建立产品边界、领域词汇、工程规范及自动检查入口。
- 提供前端状态页、分层 API、PostgreSQL 连接检查、可优雅退出的空闲 worker。
- 外置必需配置；提供免认证健康检查、统一错误响应及结构化日志。
- 提供 Compose、Render Blueprint、持续集成、验证记录和版本基线。
- 本次不实现登录、用户模型配置、材料处理、GraphRAG、推演、章节与 MCP 工具；不执行云资源购买或正式部署。
- 放弃一次生成全部业务代码的方案，采用可独立验收的骨架变更。

## Capabilities
### New Capabilities
- `system-runtime`: 服务启动、健康状态、请求边界、日志和 worker 生命周期。

### Modified Capabilities
无。

## Impact
新增 backend、frontend、部署文件、自动检查和规范；无既有接口或数据迁移。运行依赖 PostgreSQL；不要求模型密钥即可启动骨架。
