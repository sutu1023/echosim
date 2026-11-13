# 本地模型连接测试验证

日期：2026-11-13。基线提交：116edab，分支 codex/2026-11-13-model-connection-test，验证对象为本次未提交工作区实现。

## 已执行

- make check：55 项后端测试、13 项前端测试通过；Python lint/format、TypeScript、生产构建、仓库分层和 OpenSpec strict 均通过。
- make audit：Python/npm 未发现已知漏洞。
- make smoke：隔离 Compose 项目验证真实数据库正常、停机、恢复，根页面、业务 404、非 root 与 worker SIGTERM；临时资源已清理。
- APP_ENV=development MODEL_CONNECTION_TEST_ENABLED=true docker compose up --build -d api：已启动本地诊断；docker compose port api 8081 显示 127.0.0.1:8081。
- 浏览器 http://localhost:8081/dev/model-test 显示固定供应商/地址/模型、密码输入；空输入按钮禁用，输入测试标记后 Tab 可到按钮且焦点可见；刷新清空，首页服务正常。未将测试标记提交给供应商。状态使用 role=status/aria-live，未进行真实读屏语音验收。
- 后端假依赖覆盖环境禁用、跨源、输入、上游错误、空/畸形/过大响应、用量、总期限；连接测试验证拒绝非公网 IP、连接固定到已校验数字地址以及不跟随跳转。

## 限制与待验证

- 真实 OpenCode Go 调用已由用户在前端执行并确认成功；服务端成功记录见下文。未读取用户 KEY。
- 参数固定：deepseek-v4.1-flash，配置 opencode-go-test-v1，提示 v1，max_tokens=256，temperature=provider-default，60 秒，无自动重试。该输出上限已在本次真实调用中产生有效最终文本，不构成普遍容量保证。
- 公网、SSE、备份恢复及创作质量评测未完成；不代表 P13 完整实现或 M0 完成。
- 首次依赖安装遇到 PyPI TLS EOF，使用本地缓存完成安装，后续审计与容器构建成功。
- 保留已有 TestClient 弃用提示，未屏蔽；httpx 的网络后端接入使用内部 pool seam，由锁文件和固定地址测试覆盖，升级依赖需复验。

## 本地网络修复

用户首次测试报告“供应商或网络故障”。诊断发现 Clash Verge TUN Fake-IP 将 opencode.ai 解析为 198.18.0.5，触发非公网地址拒绝，尚未进入供应商鉴权。已备份本机 Clash Verge 全局扩展与生成配置，将 opencode.ai 加入 fake-ip-filter，并通过本地 Unix 控制套接字重载（204）。容器重新解析得到公网 IPv4/IPv6，使用应用 PublicBackend 的 TCP 连接检查成功。未放宽应用地址检查，未读取 KEY；真实模型成功仍待用户重试。

## 供应商协议修复

网络恢复后，无凭据诊断实际返回 HTTP 400 / MissingSessionID，要求 x-opencode-session。根据官方 https://opencode.ai/docs/console/go/#where-can-i-use-it 补充真实 User-Agent EchoSim-Connection-Test/0.0.1 与每次独立诊断的 UUID 会话头。先添加请求头断言复现 6 项失败，再修复；make check 重新通过（54 后端、13 前端）。本地容器已重建。使用明确无效的诊断 KEY 调用实际适配器，返回 MODEL_AUTH_FAILED，证明已越过会话头错误进入鉴权；不代表用户 KEY 或生成能力已验证。Go 文档主要面向编程智能体，不将本次连通性测试当作未来小说创作适用性确认。

## 最终真实调用证据与规约核对

用户确认前端“连接成功”。对应服务日志时间 2026-11-13T13:06:13.677540+00:00（北京时间 21:06:13），事件 model_connection_test，请求标识 014597378a6f4dd4bd369261be8826f7，耗时 2895.51 ms。调用版本为本次工作区实现（基线 116edab，已含会话头修复），固定模型 deepseek-v4.1-flash。日志不含 KEY 或原始回复。

核对发现旧日志格式器丢弃模型/配置/用量字段；已先添加 test_safe_diagnostic_metadata 复现失败，再扩展明确的安全字段白名单，同时为失败调用记录安全参数和错误码。本次历史调用的 token 数无法恢复，未编造；修复后字段通过自动化验证，不要求重新消耗用户额度证明同一模型调用。

验证结论：Completeness — 13/13 任务；Correctness — R1–R6 均有实现与场景映射，真实成功有用户确认及日志；Coherence — API/services/domain/adapters 依赖与设计一致。日志遗漏已修复。功能核对无剩余缺陷；最终容器回归现已通过（见后续恢复记录）。限制：历史 token 数缺失，实际读屏语音未验证，供应商未来创作用途适用性未确认；公网/SSE/备份恢复不在本轮，M0 仍未完成。

## 最终门禁阻断（日志修复后）

make check 再次通过，55 后端 + 13 前端；依赖未变化，沿用本轮已通过的 audit。make smoke 重跑两次均在拉取 python:3.12-slim-bookworm manifest 时收到 Docker Hub EOF，尚未执行到容器场景；单独 docker pull 同样失败。此前 smoke 通过记录保留为此前版本证据，不当作最终工作区验证。任务 4.1 重新打开，暂未同步主规约或归档。

本地 API 重建也因同一 manifest EOF 失败，仍运行已真实调用成功的版本，日志白名单修复尚未进入容器；源码修复和自动测试已完成。网络恢复后执行 make smoke 和 APP_ENV=development MODEL_CONNECTION_TEST_ENABLED=true docker compose up --build -d api，再关闭 4.1 并归档。

## 网络恢复与最终收尾

2026-11-13 北京时间 21:09–21:10 再次诊断：宿主访问 Docker Registry /v2/ 返回预期 401，Docker daemon 使用 http.docker.internal:3128 代理；docker pull python:3.12-slim-bookworm 成功，摘要 sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df。此前 EOF 属临时网络/代理链路故障表现，未能确定具体断点；未改 Dockerfile、镜像源、TLS 或检查标准。

最终 make smoke 成功（隔离项目 echosim-smoke-b4ee62c3）：readiness、数据库停机/恢复、前端、业务路径关闭、非 root、worker SIGTERM 全部通过；临时卷及容器已清理。正常本地 API 重建成功，/health 返回 ok；容器内确认 model/usage 日志白名单已安装。最终 make check 为 55 后端 + 13 前端通过，audit 在本轮依赖版本下已通过。任务全部完成，主规约同步后归档。前面的网络阻断记录为历史过程，已解除。
