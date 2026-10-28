# 依赖与许可策略

默认准入：MIT、Apache-2.0、BSD-2-Clause、BSD-3-Clause、ISC、PSF-2.0、PostgreSQL。未知许可阻断，其他许可需逐份书面用途、条件、替代与决定。

直接用途：FastAPI/Starlette ASGI接口（MIT/BSD），Pydantic配置校验（MIT），SQLAlchemy数据库连接（MIT），asyncpg PostgreSQL驱动（Apache-2.0），Alembic迁移（MIT），uv依赖管理（MIT/Apache-2.0）；React状态页（MIT），Vite构建（MIT），TypeScript类型（Apache-2.0），Vitest/Testing Library组件测试（MIT），Playwright浏览器测试（Apache-2.0）；Ruff静态检查（MIT），pytest测试（MIT），urllib/uvicorn真实HTTP接口测试（标准库/BSD-3-Clause）；Gitleaks机密扫描（MIT）、Trivy依赖镜像扫描（Apache-2.0），OpenSpec规约（MIT），Caddy入口（Apache-2.0），PostgreSQL（PostgreSQL）。

锁文件记录直接与间接版本。`make licenses` 生成实际安装依赖许可清单并校验，报告不含运行数据。含多种许可必须核验组合，不自动把未知标记为MIT。浏览器及镜像包含第三方声明，保留分发所需公告。最终安装清单作为验证附件。首次许可判断在核验后补充，不以本概览替代机器清单。

维护者已批准4项限定工具链例外，见 [逐包评估](license-review.md)（EX-004）。不更改依赖自身许可。
