# 私有作品联合验证

日期：2026-11-13（北京时间）。基线提交 1a0d367，分支 codex/2026-11-13-private-works；验证对象为本轮未提交工作区及其构建容器，未创建发布标签。

## 范围与拆分

按 delivery.md 与原 tasks 1.1 的规模约束，将账号/会话/快捷登录拆为前置 2026-11-13-user-auth；private-works 保留作品创建、列表、详情、幂等与运行入口迁移。两个 change 联合交付同一轮已确认范围，未删减 Scenario。

业务归属以用例划分：Accounts、账号 SQL、登录/退出/快捷 API、账号命令与登录界面属于 user-auth；Works、作品 SQL、作品 API 与作品列表/详情属于 private-works。通用 HTTP/数据库包装与组装在同一工作树集成，迁移和验收共同运行。后续独立提交/归档保持认证先于作品。

## 先失败再实现与发现的修复

- 新服务用例首次运行因 domain/accounts 尚不存在失败；接口用例因 create_app 尚无 accounts 参数而六项失败；随后实现使其通过。
- 前端新用例首次因 Workspace 尚不存在失败；实现后覆盖真实行为。
- 浏览器发现快捷登录成功后返回登录页出现不必要的“请输入用户名和密码”。先增加复现断言，得到一项失败，再修复快捷登录不触发密码字段校验、成功后清理校验状态。最终版本在正常本地容器复核无该提示。
- 首次 make check 发现 pytest 从仓库根目录意外收集依赖真实数据库的合同测试。明确离线入口为 backend/tests，并将全部合同测试加入 make smoke；未 skip 任何用例或放宽断言。真实测试由脚本启动独立临时数据库，完成后清理。
- Argon2id 依赖初次安装遇到 TLS EOF，使用系统证书成功安装；未关闭证书验证。argon2-cffi 25.1.0 及其依赖已写入锁文件。

## 自动门禁

- 最终 `make check`：80 项后端测试、20 项前端测试通过；ruff lint/format、TypeScript、前端生产构建、仓库结构、OpenSpec 全量 strict 通过（两个活动 change、两份主规约）。
- `make audit`：Python/npm 未发现已知漏洞；仅项目 editable 包按既有命令跳过包审计，不跳过第三方依赖。
- 最终 `make smoke`：先执行真实 PostgreSQL 合同测试 1 项综合用例成功，再执行隔离 Compose echosim-smoke-a9719c19，迁移、readiness、停库/恢复、页面、未登录 401、未知路由 404、test 环境快捷开关关闭、非 root、worker SIGTERM 均通过。自身容器与卷已清理。
- PostgreSQL 综合用例涵盖：空库缺 schema 拒绝、并发/重复迁移、失败迁移事务回滚、账号唯一、Argon2id、八路并发同键只生成一个作品、同键异内容冲突、跨账号同键隔离、相同时间戳分页、作品事务失败回滚、快捷切换数据库失败回滚、重置并发签发、重新连接恢复会话/幂等、持久限速并发阈值及到期边界。
- 安全测试覆盖 production Secure/HttpOnly/SameSite cookie、不回显 token、跨源/缺失 Origin/缺失 CSRF、伪造归属、JSON 边界、标题码点、日志标记不泄露。production/test/禁用开关均直接请求快捷 API 得到 404。

## 真实浏览器与本地部署

使用独立 Compose 项目 echosim-private-review、独立卷与 http://localhost:8082 验收；仅使用本轮测试账号和作品。

1. 显式执行 `manage demo`，A 一键登录后为空列表；创建“春山 · A 的独立故事”成功进入详情。Tab 可到创建按钮，截图观察到清晰焦点。
2. 切换 B 后列表为空；直接访问 A 作品地址显示“资源不存在”。切回 A 能看到原作品。
3. 用 `manage create browser-check` 通过标准输入创建临时账号，浏览器正常用户名密码登录成功；创建“重启后保留的作品”。
4. 实际重启该项目 db 和 api，刷新详情仍保持登录且作品存在。
5. `manage reset browser-check` 后刷新旧页面跳到登录页；新密码登录后作品仍在。多会话全部失效另由单元/真实数据库用例覆盖。
6. 正常开发环境已用最终源码重建，http://localhost:8081/login 可用；保留已有模型诊断开关，开启本地快捷登录并幂等初始化 A/B。现场检查 A 登录与退出成功、返回切换页面不再显示错误校验提示；正常开发数据库与卷未清理。

交付页面截图：[登录与 A/B 入口](images/2026-11-13-private-works-login.png)。临时 browser-check 及验收作品仅在隔离 review 数据库，不写入正常开发作品列表。

收尾追加：重复 submit 期间保留原创建请求标识的组件测试已加入（前端最终 20 项）；最终容器构建与 smoke 重新通过。合同测试脚本改为通过环境传递临时数据库密码，避免失败异常回显 argv；ruff 与真实数据库合同测试再次通过。隔离 review 项目与临时凭据文件已清理。

## 限制与未执行

- 公网部署仍暂缓；Render 的 PUBLIC_ORIGIN 与迁移模板仅为配置准备，未做真实公网验收。
- 备份恢复、实际读屏语音、长时间运行与大容量压力未执行；没有声称 M0/M1 完成。
- 会话七天边界、限速窗口通过可注入时钟验证，不声称实际等待七天。
- 仍有现有 Starlette TestClient 弃用提示，未屏蔽。
- 本地 HTTP 省略 Secure 的环境限定例外写入 security.md；production 的 Secure 与 HTTPS 要求由自动测试验证。
- 未修改、删除或回滚已采纳剧情；该能力仍未实现。此次未调用真实模型、不消耗模型额度。

## 归档与提交检查

2026-11-13 按 user-auth → private-works 顺序同步主规约并归档，18/18 任务完成。system-runtime R3 原英文声明同步后触发主规约 500 字符限制，已等义改为中文声明，保留全部错误码、授权要求与 Scenario；主规约 strict 校验通过。提交前重新运行 make check，沿用同一实现版本已通过的真实数据库、容器、浏览器和依赖审计结果；此次未修改业务实现。
