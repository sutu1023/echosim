## Context

Python3.12 FastAPI/SQLAlchemy/PostgreSQL，React/TypeScript/Vite，Caddy同源入口。开发测试全容器，生产单API进程；M4再引入蓝绿运行者心跳以免新实例误终止旧任务。

## Goals / Non-Goals

M0一条命令起服务、探针和四态可验，86规范有阶段标记。非目标为业务表、真实模型、费用、账号与公网发布。

## Decisions

1. API层路由与封装，服务组织健康检查，仓储执行SELECT 1；数据库错误仅显示固定安全信息，不泄露DSN。探针免认证但显式登记，未知路由与未登记路由拒绝。
2. /health成功200，依赖失败503；/livez仅进程响应200。JSON均code/message/data/request_id，健康含version/time/dependencies。无/healthz草稿遗留别名。
3. 配置通过Pydantic集中校验，必需项无字面地址回退，fake模式无真实Key。使用asyncpg而非需新增许可例外的驱动。
4. 状态页中文纸感，自有CSS与组件；真实读取/health，失败显示固定建议；组件测试覆盖空态，生产未知数据不当健康。
5. 事件/费用/会话等只写未来规范，不建空业务表。Alembic空基线明确不表示业务模型已完成。
6. 默认门禁完整，纯说明更正才允许精简。初始化超规模EX-003，独立AI复核与敏感产物检查补偿。

## Risks / Trade-offs

- 基础镜像或漏洞源不可用会阻断检查，记录失败并解决而非跳过。
- 两引擎测试模拟手机布局，真实设备与同学复现另列外部待验。
- 无上一个正式版本，使用本地候选与最终骨架版本演练，不冒充生产回滚。

## Migration Plan

初始README/忽略/MIT先提交，保护main，开发分支规范先行，严格校验后实现；验证/独立审查、归档、PR门禁、Squash、标签、干净克隆复现。
