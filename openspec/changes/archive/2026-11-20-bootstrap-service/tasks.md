# Tasks

## 1. 工程骨架与依赖锁定

- [x] 1.1 创建 `app/`、`web/`、`scripts/`、`docs/acceptance/`、`.github/workflows/`、`tests/` 目录及占位入口文件，运行 `find` 确认目录结构与设计一致
- [x] 1.2 编写 `web/package.json`、`vite.config.ts`、`tsconfig.json`、`.nvmrc`（Node 22），安装 Vue 3 + TypeScript + Vite 并提交 `package-lock.json`，运行 `npm ci && npm run build` 确认生成 `web/dist/index.html`
- [x] 1.3 编写 `app/requirements.txt` 与 `app/requirements-dev.txt`，使用精确版本锁定 FastAPI、Uvicorn、SQLAlchemy、asyncpg、pydantic-settings 与测试用 httpx，在临时 venv 中 `pip install -r app/requirements-dev.txt` 并成功导入各包
- [x] 1.4 添加 `web/package.json` 的 `typecheck` 脚本（`vue-tsc --noEmit`）并运行通过，确认 TypeScript 严格模式无错误

## 2. 后端配置与健康检查

- [x] 2.1 实现 `app/config.py`：从环境读取 `DATABASE_URL`、`HEALTH_DB_TIMEOUT_SECONDS`、`WEB_DIST_DIR` 并带开发默认值，运行 `python3 -c "from app.config import settings; print(settings.web_dist_dir)"` 确认配置可加载且缺省值符合设计
- [x] 2.2 实现 `app/db.py`：SQLAlchemy async engine + asyncpg、`pool_pre_ping`、2 秒连接/命令超时、应用关闭时 `dispose`，编写最小连接冒烟命令并在 PostgreSQL 可用时输出查询成功
- [x] 2.3 实现 `app/health.py` 的 `probe_database()` 与 `/health` 路由：成功返回 200 `{"status":"ok","database":"ok"}`，失败或超时返回 503 `{"status":"degraded","database":"unavailable"}`，异常只写日志不回显 DSN，使用 `unittest.mock.patch` 的单元测试分别验证 200、503 与响应体不含凭据，运行 `python3 -m unittest discover -s tests` 通过
- [x] 2.4 实现 `/livez` 存活路由（不访问数据库、GET 200），单元测试断言数据库探测失败时 `/livez` 仍为 200 且 `/health` 为 503，运行测试通过
- [x] 2.5 为 `/health` 添加非 GET 方法 405 的测试，并确认探测路径只执行 `SELECT 1`、无写操作，运行测试通过

## 3. 前端首页

- [x] 3.1 实现 `web/src/App.vue` 与入口文件：展示 `EchoSim` 与“材料驱动的双平台推演工作台”，挂载时 `fetch('/health')` 并以可读状态展示可用/降级/不可达，运行 `npm run build` 后在 `web/dist` 中检索到这两个字符串
- [x] 3.2 添加最小样式与语义结构（`main`、`h1`、状态区域 `aria-live`），运行 `npm run typecheck` 与 `npm run build` 均通过，并用 `python3 -m http.server` 预览构建产物确认无控制台构建错误
- [x] 3.3 确认前端所有请求使用相对路径、无硬编码 `localhost` 或后端端口，使用 `rg "localhost|:8000|http://" web/src` 无匹配

## 4. 同域静态服务与容器镜像

- [x] 4.1 实现 FastAPI 静态托管：API 路由优先、`/assets` 映射 `dist/assets`、catch-all 对已存在文件返回文件否则回退 `index.html`，并拒绝解析后位于 `dist` 之外的路径；单元测试覆盖首页、静态资源、未知路径回退与目录穿越，运行测试通过
- [x] 4.2 编写多阶段 `Dockerfile`（Node 22 构建 → Python 3.11 运行）与 `.dockerignore`，执行 `docker build -t echosim-skeleton:dev .` 成功，并确认镜像内存在 `web/dist/index.html`
- [x] 4.3 以该镜像单独启动容器（挂占位静态目录）验证容器内 `/livez` 返回 200，确认镜像默认命令、端口 8080 与健康检查配置正确

## 5. Compose 编排与本地命令

- [x] 5.1 编写 `docker-compose.yml`：`app` 与 `db` 服务、`depends_on` 健康条件、`pg_isready` 与应用 `/livez` 健康检查、命名卷、端口 `127.0.0.1:${ECHOSIM_PORT:-8080}:8080`；运行 `docker compose config` 无错误且端口映射符合设计
- [x] 5.2 编写 `.env.example`，列出 `ECHOSIM_PORT`、`POSTGRES_USER/PASSWORD/DB`、`HEALTH_DB_TIMEOUT_SECONDS` 及用途注释，值为开发占位符；运行 `rg -i "password|secret|token" .env.example` 确认无真实凭据
- [x] 5.3 编写 `Makefile` 的 `up`（缺 `.env` 时从样例生成、`up -d --build --wait`）、`down`（`down --remove-orphans`）、`reset`（`down -v`），运行 `make down && rm -f .env && make up` 后 `curl -fsS http://127.0.0.1:8080/` 返回包含 `EchoSim` 的 HTML
- [x] 5.4 验证幂等启动与回环绑定：再次执行 `make up` 退出码为 0 无冲突；用 `docker compose port app 8080` 或 `ss -ltn` 确认仅绑定 `127.0.0.1:8080`；`curl -i http://127.0.0.1:8080/health` 返回 200 与约定 JSON
- [x] 5.5 验证停止与再启动：`make down` 后端口释放、容器与网络移除，`make up` 再次成功且数据卷保留（`docker volume ls` 仍见命名卷）
- [x] 5.6 按从零开始的顺序实跑 README 快速开始草稿（`make down`、删除 `.env`、`make up`、打开首页、`make down`），在 `README.md` 中写入前置条件、命令、配置表、验收入口与排障（端口占用、旧数据卷凭据不一致、`make reset`），确认每条命令可直接复制执行

## 6. 验收脚本

- [x] 6.1 实现 `scripts/verify.py` 基础框架：`--expect-sha`、`EXPECTED_SHA`、`ALLOW_DIRTY`、`--help`、读取 `git rev-parse HEAD`、脏工作树检测、UTC 时间与原子报告写入 `runtime/verify/last-run.json`，运行 `python3 scripts/verify.py --help` 与脏工作树用例（创建未忽略临时文件时非零退出并提示）通过
- [x] 6.2 实现验收场景：启动并等待首页含 `EchoSim`、`/health` 200、记录 app 容器 `Id/Pid/StartedAt/RestartCount`、停 `db` 后 503（≤5 秒）且 `/livez` 200、恢复 `db` 后 200、身份不变、首页仍 200；在 `finally` 中恢复数据库，运行 `make verify` 一次通过并人工核对报告逐场景 `passed`
- [x] 6.3 校验报告契约：断言 `runtime/verify/last-run.json` 含 `tested_sha`（等于 `git rev-parse HEAD`）、`dirty=false`、`result=passed`、起止时间、全部场景、停库耗时 ≤5 秒、退出码 0；用 `python3 -c` 编写一次性断言命令并执行通过
- [x] 6.4 验证可重复性：连续执行两次 `make verify`，两次均退出 0、报告时间戳更新、第二次结束后首页 200 且数据库运行，无需人工清理
- [x] 6.5 验证失败路径与恢复：用 `EXPECTED_SHA=0000000000000000000000000000000000000000 make verify` 触发 SHA 不一致失败，确认非零退出、报告记录失败原因、`db` 仍为运行状态，随后正常 `make verify` 通过

## 7. GitHub Actions

- [x] 7.1 编写 `.github/workflows/verify.yml`：`push`/`pull_request`/`workflow_dispatch` 触发、checkout、`EXPECTED_SHA=${{ github.sha }} make verify`、断言报告 `tested_sha` 与 `result`、`always()` 上传 `runtime/verify/` 产物、`always()` 执行 `make down`；用 `python3` 或 `docker run rhysd/actionlint` 校验 YAML 语法与表达式无误
- [x] 7.2 确认 CI 与本地命令无分叉：对比工作流步骤与 `Makefile`/`scripts/verify.py`，确认未重复实现验收逻辑；在本地以 `EXPECTED_SHA=$(git rev-parse HEAD) make verify` 复现 CI 的调用方式并通过
- [x] 7.3 提交并推送后，确认 `verify` 检查成功且下载的产物中 `tested_sha` 与本次提交 SHA 相同；若无法推送，则在 `docs/acceptance/` 之外记录待验收事项并在交付说明中明确未完成项

## 8. 工程规则与验收资产

- [x] 8.1 编写根 `AGENTS.md`：语言、栈与版本、目录约定、`make` 命令、同域/回环/健康语义、真实 `SELECT 1` 要求、禁止提交凭据、脏树与 SHA 验收规则、提交与文档要求；在 `README.md` 链接该文件，确认链接路径存在且规则覆盖上述要点
- [x] 8.2 编写 `docs/acceptance/README.md` 与 `TEMPLATE.md`，字段包含被测 SHA、分支、日期、验收人、环境、三个验收场景结果、CI 链接与证据；运行 `make verify` 前后对比 `docs/acceptance/` 文件哈希，确认自动化流程未写入该目录
- [x] 8.3 编写 `.pi/skills/echosim-acceptance/SKILL.md`：带 `name`/`description` frontmatter，步骤引用真实存在的 `make up`、`make verify`、`make down` 与 `docs/acceptance` 模板，说明如何用同一 SHA 对齐浏览器验收、两次 verify 与 CI 产物；对照现有 `.pi/skills/*/SKILL.md` 结构检查 frontmatter 与命令可执行
- [x] 8.4 使用该技能走查一次完整验收流程（浏览器打开首页、停库观察 503、恢复观察 200、连续两次 verify），按模板生成一份 `docs/acceptance/<date>-<sha7>.md`，确认记录中的 SHA 与报告、CI 一致且结论由验收人署名

## 9. 端到端集成验证

- [x] 9.1 提交全部改动后，在 `/tmp` 做一次干净克隆，按 README 从零执行 `make up` 与两次 `make verify`，确认浏览器/`curl` 可见 EchoSim、两次均通过、报告 SHA 等于克隆的 HEAD
- [x] 9.2 确认干净克隆结果与成功 CI 产物 SHA 相同，并在人工验收记录的“证据”中同时列出克隆 SHA、CI 运行链接与报告文件位置
- [x] 9.3 执行 `make down` 清理本地环境，确认 `127.0.0.1:8080` 已释放且无遗留项目容器
- [x] 9.4 运行 `openspec validate 2026-11-20-bootstrap-service --strict` 通过，并执行 `openspec status --change 2026-11-20-bootstrap-service` 确认所有必需产物为 `done`

## 10. 收尾补强：请求 ID 与受控失败证据

- [x] 10.1 为 `/health` 增加请求 ID：中间件复用合法 `X-Request-ID` 或生成随机值、响应头回显、失败日志记录 `request_id`，响应体契约不变；新增单元测试覆盖回显、非法值替换与日志关联，运行 `python3 -m unittest discover -s tests` 通过
- [x] 10.2 为 CI 增加受控失败入口：`workflow_dispatch` 支持覆盖期望 SHA，成功与失败运行均上传报告并缺失即失败；`scripts/verify.py` 增加未预期异常兜底报告；用 actionlint 校验工作流
- [x] 10.3 在新候选 SHA 上重跑证据链：单元测试、连续两次 `make verify`、GitHub 干净克隆两次 verify、CI 成功运行、受控失败运行并下载校验失败报告
- [x] 10.4 按模板生成新 SHA 的人工验收记录（含新 CI 成功/失败运行、克隆 SHA 与报告位置），保留 `docs/acceptance/2026-11-20-e16ee09.md` 不动，并更新 README/AGENTS 说明
