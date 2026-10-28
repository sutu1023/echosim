# 回响 EchoSim

面向学生与普通体验者，观察多种虚拟角色如何围绕真实新闻讨论、回应与改变观点。

## 当前阶段

M0：规约、八类86条规范和运行骨架。业务按 M1–M4 增量交付；当前没有推演、新闻导入、会话、SSE或真实模型调用。首次可运行基线之后主干必须保持可启动。

## 从零启动

前置：Git、Docker Engine/Desktop 与 Compose、Python 3（脚本仅标准库）、make。无需本地安装 Node、uv 或真实模型 Key。

```sh
git clone https://github.com/sutu1023/echosim.git
cd echosim
make init
make prepare
make up
make status
```

`make init` 生成仅本机可读的随机数据库口令和 `.env`，并安装本仓库提交钩子，不覆盖已有配置。也可按 `.env.example` 手工填写。默认访问 **http://127.0.0.1:18380**；如端口被占用，修改 `.env` 的 WEB_PORT。不会复用其他项目的容器或数据库卷。

首次 prepare 下载摘要锁定的镜像、安装依赖并构建隔离测试镜像，需要网络和数GB磁盘。常规 up 一条命令启动 PostgreSQL、API和同源静态入口；`make dev` 使用全容器代码热更新。`make down` 停止运行栈但保留数据卷。

## 验证命令

| 命令 | 内容／联网情况 |
|---|---|
| `make test` | 使用与当前源码指纹一致的已准备镜像，隔离数据库、后端、组件、Chromium/WebKit、布局、版本切换、停止及合成恢复；运行不访问公网 |
| `make check` | 规约追踪、重新构建、测试、机密、许可及高危漏洞门禁；需联网构建和更新漏洞库 |
| `make docs-check` | 标准完整性与场景追踪，标准库运行；报告在 artifacts/traceability.json |
| `make secrets` | 非忽略工作区和完整历史扫描，包含真实扫描器负向样例；报告脱敏 |
| `make licenses` | 实际已安装Python/Node工具链许可清单，未知或未批准许可失败 |
| `make security` | 锁文件及运行镜像高危/严重漏洞，扫描故障仍失败 |
| `make logs` | 今日应用单行JSON日志，不输出凭证、正文与原始IP |
| `make live-smoke` | M0不适用，明确非零返回“未执行”；M3实现限额真实检查 |

修改源码后先重新 `make prepare` 再 `make test`，避免测旧镜像。`make check` 自动处理准备。退出码0为已执行检查通过，非零为失败／未执行，不得跳钩子或放宽门禁。报告与截图在忽略的 `artifacts/`，CI作为脱敏附件保留90天；里程碑摘要入 docs/verification。

测试使用专用随机项目名、数据库和内部网络，结束后清理自己的临时服务。测试数据是合成数据，费用为零。独立同学／教师复现、真实手机及公网部署仍须另行留证，AI或本机结果不能替代。

## 规范、规约与证据

- [八类86条规范](docs/standards/README.md)、[统一数值](config/policy.json)、[例外](docs/standards/exceptions.md)
- [产品边界与路线](docs/product.md)、[运维与恢复](docs/operations.md)、[依赖许可](docs/dependencies.md)
- OpenSpec采用spec-driven，先严格校验proposal/design/specs/tasks再实现；场景编号自动关联测试。版本固定在锁文件。
- AGENTS.md 是Codex入口，仓库技能覆盖规约、测试、独立审查、复现和发布。

M0健康接口：`GET /livez` 仅存活；`GET /health` 查询数据库就绪，成功200、依赖异常503。返回 code/message/data/request_id，data含版本、构建标识、UTC时间、依赖。探针免认证但显式登记，其他入口默认拒绝；API文档在M0不公开。

## 后续里程碑

M1 新闻与角色；M2 假模型、SSE、取消和回放；M3 DeepSeek、回顾、引用、费用与临时BYOK；M4 分享清理、HTTPS、蓝绿、异地备份与外部试用。未申请服务器域名，本地运行不等同正式发布。

## 用户与场景

| 任务 | 用户 | 触发 | 完成结果 |
|---|---|---|---|
| 准备推演 | 匿名创建者 | 导入新闻、确认背景与角色 | 可启动配置 |
| 观察讨论 | 创建者 | 启动并观看或取消 | 发言、回复、状态可查 |
| 理解结果 | 创建者 | 打开回顾 | 观察可跳转到模拟发言 |
| 管理记录 | 创建者 | 打开我的推演 | 仅管理当前身份记录 |
| 查看分享 | 访客 | 打开分享链接 | 只读查看有效内容 |

## 协作与完成定义

主干为 `main`，开发分支使用 `codex/`，每次变更通过 PR。提交信息注明改动与规约依据。先写规约再实现，同一 PR 交付；门禁与独立审查通过后允许 Codex Squash 合并常规变更。

完成要求：本轮场景满足、适用检查通过、文档同步、审查关闭、证据可查。未执行事项必须明确列出。预算增加、安全例外、破坏性数据操作和正式发布由维护者单独决定。

## 许可

MIT，见 [LICENSE](LICENSE)。选题 PDF、真实凭证与参考站点素材不入库。

本轮机器验证与外部待验：[验证记录](docs/verification.md)。限定安全例外与失效条件：[EX-005评估](docs/security-review.md)。
