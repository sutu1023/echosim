# Design

## Context
当前无业务代码。动因与边界见 proposal.md；完整产品基线单独记录在 docs/product.md，不能把规划能力视为已实现。

## Goals / Non-Goals
Goals: 同一镜像可运行 API 或空闲 worker；可复跑自动检查；明确失败和停止行为。
Non-Goals: 本次不建立业务表，不调用模型，不提供登录、MCP 或对象存储接口；不购买或启动云服务。

## Decisions
1. 后端按 api → services → domain 分层；adapters 实现 domain 中的依赖探针接口。组装入口负责注入，业务层不依赖 FastAPI 或数据库驱动。相较路由直连数据库，故障注入与依赖方向可验证。
2. 使用异步 PostgreSQL 探针，每次健康请求建立有界连接并执行 SELECT 1；骨架不使用连接池，减少未使用资源和生命周期复杂度。生产业务引入前另行设计池容量。
3. API 的 readiness 探测数据库，数据库暂时故障不导致进程退出，下一请求可恢复；配置错误则 fail-fast。worker 仅探测和记录状态，不宣称可运行后台业务任务。
4. 前端由 FastAPI 同域提供构建产物；Vite 开发服务器代理 /health。只挂载 /assets 与 /，避免 SPA fallback 把未知 API 变成 200。
5. 固定 Python 3.12，Python 和 Node 依赖分别锁定 uv.lock 和 package-lock.json。多阶段 Docker 构建，非 root 运行。Compose 数据库不发布宿主端口。
6. Render Blueprint 提供 Web、worker 和托管 PostgreSQL 的部署描述。对象存储与模型配置随业务变更加入；本次健康状态只代表 API 与数据库，不代表 worker、模型或未来完整系统。
7. 主干要求自动 check 通过并与最新 main 对齐，禁止强推和删除；个人通过零审批数的 PR 合入。

## Risks / Trade-offs
- 每次探测建连接增加开销 → 骨架仅用于低频检查，超时上限明确；业务阶段再设计连接池。
- 本地通过不代表 Render 可用 → 明确记录公网验证待执行，禁止伪造部署证据。
- 未配置模型仍可启动 → 状态页清楚标记业务未实现；不把骨架健康解释成模型可用。
- 依赖版本变化 → 依赖锁定，CI 使用 frozen/ci 安装，更新时重跑检查。

## Migration Plan
新仓库无既有数据迁移。构建并运行检查、健康探针成功后合入；保留版本标签用于回退镜像。Compose named volume 独立保留，常规停止不删除数据卷。正式部署前配置备份与恢复演练。
