# EchoSim

面向个人创作者的小说剧情推演与逐章创作系统，结合轻量 GraphRAG、角色智能体与情节架构智能体。

**当前版本：运行骨架。** 提供中文状态页、API 健康检查、数据库探针与空闲 worker。尚未实现账号、模型配置、材料导入、推演和章节创作。完整规划见 [产品基线](docs/product.md)。

## 从零启动

需要 Git、Docker Engine 与 Docker Compose v2+、Python 3、Make。启动流程不需要 Node、uv 或模型密钥；前后端依赖在镜像内安装。

```sh
git clone https://github.com/sutu1023/echosim.git
cd echosim
make configure
make up
```

`make configure` 创建仅本机可读的 `.env`，写入明确的开发配置和随机数据库密码，已有文件绝不覆盖。也可复制仅含键名的 `.env.example`，参照下表手动填写。`make up` 构建并启动 API、worker、PostgreSQL，等待健康检查完成。

打开 [本地状态页](http://localhost:8081)，或验证：

```sh
curl --fail http://localhost:8081/health
```

成功返回 `status: ok`、`dependencies.database: up`、版本、UTC 时间及请求标识。数据库不可达返回 503/degraded；恢复后下一次请求可成功。此状态不代表模型、worker 或业务能力已经就绪。

```sh
make down                 # 停止服务，保留数据库卷
```

不要用 `docker compose down -v` 停止需要保留数据的实例。

## 配置

所有键必须显式提供。生成脚本写入下列开发值；应用自身不提供缺失配置兜底。

| 键 | 开发值/限制 |
| --- | --- |
| APP_ENV | development；允许 development/test/production |
| APP_VERSION | 0.0.1；1–64 个字母、数字、点、下划线或连字符 |
| PORT | 8081；1–65535，宿主和容器使用同一端口，仅绑定宿主回环地址 |
| HEALTH_TIMEOUT_SECONDS | 3；1–10 秒 |
| WORKER_POLL_SECONDS | 10；1–60 秒 |
| POSTGRES_DB | echosim |
| POSTGRES_USER | echosim |
| POSTGRES_PASSWORD | 脚本生成随机十六进制值；手动填写时使用 URL 安全字符 |

Compose 从数据库键构造 `DATABASE_URL` 传给 API 与 worker；直接运行 Python 时需自行提供此 PostgreSQL URL。必需值缺失、为空或非法时启动失败。数据库用户名、密码或库名变更不会自动重写既有数据卷，应使用数据库管理操作同步，不能仅修改 `.env`。

## 开发与验证

本机开发需要 Node.js 22.12+、uv 0.13.6 与 OpenSpec 1.14.3；Python 3.12 由 uv 管理。

```sh
npm install -g @fission-ai/openspec@1.14.3
make setup
make check
make audit
make smoke
```

- `make check`：Python 格式/静态检查、离线接口与进程测试、前端状态测试与构建、分层和跟踪文件检查、规约严格校验。失败即非零退出。
- `make audit`：Python 与 npm 依赖漏洞检查，需要网络；Python 不允许已知漏洞，npm high/critical 阻断。
- `make smoke`：独立项目、随机端口、独立数据卷；真实测试数据库正常→停机→恢复、状态页、未知 API、非 root 与 worker 停止。结束仅删除自身临时资源，不读取本地 `.env`。

修改前端时保持 Compose API 运行，执行 `npm --prefix frontend run dev`，打开其输出地址；默认代理到 `http://127.0.0.1:8081`，其他端口用 `API_ORIGIN` 显式设置。

## 结构与约定

```text
backend/src/echosim/     api、services、domain、adapters 与组装入口
frontend/src/           状态页与健康客户端
openspec/               当前规约与变更记录
docs/standards/         八类可检查工程规范
docs/adr/               关键架构取舍
docs/verification/      场景追溯与真实验证记录
```

规则入口 [AGENTS.md](AGENTS.md)，领域语言见 [GLOSSARY.md](GLOSSARY.md)。变更名为 `YYYY-MM-DD-kebab-case`，分支 `codex/<变更名>`。提交信息说明改动及规约标识；主干必须通过 `check`，禁止强推和删除。代码、容器层和版本历史不得包含密钥。

## 部署

[render.yaml](render.yaml) 描述同域 Web、独立 worker 与 PostgreSQL；这是部署准备文件，**尚未创建 Render 资源或完成公网验证**。默认区域 Singapore 和资源级别仅是可修改的初始配置，导入前核对账号、价格与区域。自动部署关闭。对象存储与模型能力后续接入。

部署顺序、健康检查、备份目标与回退要求见 [运维规范](docs/standards/operations.md)。骨架不创建业务表，回退应用版本不删除数据库卷。

## 许可

尚未选择开源许可证；公开源码不表示授予额外使用许可。
