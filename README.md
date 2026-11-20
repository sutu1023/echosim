# EchoSim

材料驱动的双平台推演工作台。

当前版本是可启动、可复现的工程骨架：同域提供前端首页与数据库就绪检查，`make verify` 生成绑定 Git SHA 的验收报告。回放、上传、登录与推演等业务能力尚未实现。

## 前置条件

- Docker Engine 与 Docker Compose v2（`docker compose version` 可用）
- GNU Make 4.x
- Python 3.11+（仅 `make verify` 使用，且只用标准库）
- Git
- 端口 `127.0.0.1:8080` 可用

本地前端开发额外需要 Node.js 22（见 `.nvmrc`），仅用于 `npm ci && npm run build` 等命令，`make up` 的容器构建自带 Node。

## 快速开始

```bash
git clone <repo-url> echosim
cd echosim
make up
```

`make up` 在缺少 `.env` 时自动从 `.env.example` 生成，然后构建镜像并等待应用与数据库健康。

浏览器打开 <http://127.0.0.1:8080/>，应看到标题 `EchoSim`、副标题“材料驱动的双平台推演工作台”，以及服务状态徽标（数据库可用时显示“可用”）。

健康检查：

```bash
curl -i http://127.0.0.1:8080/health
```

数据库可用时返回 `200` 与 `{"status":"ok","database":"ok"}`；数据库不可用时返回 `503` 与 `{"status":"degraded","database":"unavailable"}`。

每次请求都会带 `X-Request-ID` 响应头：合法的请求头会被复用，否则生成随机 ID。探测失败时服务端日志包含同一个 `request_id`，便于关联：

```bash
curl -s -D- -H 'X-Request-ID: accept-123' http://127.0.0.1:8080/health
curl -s -o /dev/null -H 'X-Request-ID: accept-123' http://127.0.0.1:8080/health
docker compose logs app | grep 'request_id=accept-123'
```

停止环境：

```bash
make down
```

`make down` 停止并移除容器与网络、释放 `127.0.0.1:8080`，但保留 PostgreSQL 数据卷；之后 `make up` 可直接再次启动。

## 配置

所有配置项都在 `.env`（由 `.env.example` 复制）中，均可省略并使用下列默认值。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `ECHOSIM_PORT` | `8080` | 宿主机发布端口，始终只绑定 `127.0.0.1` |
| `POSTGRES_USER` | `echosim` | 本地 PostgreSQL 用户名 |
| `POSTGRES_PASSWORD` | `echosim_dev_password` | 本地 PostgreSQL 密码（占位值，禁止用于真实环境） |
| `POSTGRES_DB` | `echosim` | 本地 PostgreSQL 数据库名 |
| `HEALTH_DB_TIMEOUT_SECONDS` | `4` | `/health` 数据库探测整体超时（秒），需保证 5 秒内返回 503 |

覆盖端口示例：

```bash
ECHOSIM_PORT=8090 make up
```

`.env` 与 `.env.*`（`.env.example` 除外）已被 `.gitignore` 忽略，禁止提交任何真实凭据。

## 验收

```bash
make verify
```

`make verify` 会构建并启动环境，依次验证：首页包含 `EchoSim`、`/health` 返回 200、停止数据库后 5 秒内返回 503 且 `/livez` 仍为 200、恢复数据库后重新返回 200、后端容器身份（ID/PID/启动时间/重启次数）全程不变，最后把结果写入 `runtime/verify/last-run.json`，结束时恢复数据库运行。该命令可连续执行多次，无需人工清理。

验收规则：

- 工作树存在未忽略改动时默认拒绝执行。提交后再验收，或显式使用 `ALLOW_DIRTY=1 make verify`（报告会记录 `dirty: true`，不能作为干净克隆证据）。
- 指定期望 SHA 时校验失败即退出：

```bash
EXPECTED_SHA=$(git rev-parse HEAD) make verify
```

- 机器可读报告字段包括 `result`、`tested_sha`、`dirty`、`started_at`/`finished_at`、逐场景 `scenarios[]` 与 `app` 容器身份。
- CI 成功与失败运行都会上传 `verify-report-<sha>`；报告缺失会让工作流失败。可用手动触发执行受控失败演练：

```bash
gh workflow run verify --ref <branch> -f expected_sha=0000000000000000000000000000000000000000
```

  该运行会失败，并上传记录 SHA 不一致原因的失败报告，用于证明失败路径可观测。
- `scripts/verify.py` 对未预期异常也会写入 `verify-crash` 失败报告，保证 CI 有产物可下载。

CI 在干净检出上执行同一命令，并断言报告中的 `tested_sha` 等于触发提交 SHA；工作流名称 `verify`，报告作为构建产物上传。

人工验收记录放在 [`docs/acceptance/`](docs/acceptance/README.md)，与被 CI 上传的 `runtime/verify/` 产物严格分离；Pi 验收技能见 [`.pi/skills/echosim-acceptance/SKILL.md`](.pi/skills/echosim-acceptance/SKILL.md)。

## 目录约定

```
app/                 FastAPI 后端（配置、数据库、健康检查、静态托管）
web/                 Vue 3 + TypeScript + Vite 前端
tests/               Python 单元测试（unittest）
scripts/verify.py    验收脚本（Python 标准库）
docs/acceptance/     人工验收记录与模板
.github/workflows/   CI 工作流
runtime/verify/      make verify 生成的报告（已忽略，不提交）
```

## 排障

**`make up` 报端口被占用**：确认 `127.0.0.1:8080` 未被其他进程使用。

```bash
ss -ltn 'sport = :8080'
```

释放占用或换端口（`ECHOSIM_PORT=8090 make up`）。

**改了 `POSTGRES_PASSWORD` 后数据库认证失败**：PostgreSQL 只在初始化空数据卷时应用密码，旧数据卷会保留旧凭据。确认可以丢弃本地数据后重建：

```bash
make reset
make up
```

`make reset` 等价于 `docker compose down -v`，会删除数据卷，属于破坏性操作。

**`/health` 返回 503**：查看数据库容器状态与日志。

```bash
docker compose ps
docker compose logs db
```

应用进程本身可用时 `/livez` 仍返回 200；503 表示数据库不可用而非进程死亡。

**构建需要重新拉取依赖**：`make up` 会重新构建镜像，首次运行需要访问 npm 与 PyPI。

**单元测试**：

```bash
.venv/bin/python -m unittest discover -s tests
```

（先执行 `uv venv --python 3.11 .venv && uv pip install -r app/requirements-dev.txt`，或使用任意 Python 3.11 虚拟环境。）

## 工程规则

贡献与自动化约定见 [`AGENTS.md`](AGENTS.md)。

## 非目标

本阶段不实现回放、上传、鉴权、推演引擎、生产部署、nginx 反向代理或多环境编排。
