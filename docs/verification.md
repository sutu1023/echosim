# M0 骨架验证记录

变更：`2026-10-28-bootstrap-echosim`。日期：2026-10-28。范围为本地运行骨架、状态页、规范与门禁。模型调用0次，真实模型费用0元。最终机器结果以PR的 `quality-gate` 和对应验证附件为准。

本机完整 `make check` 已通过：29项后端、7项组件、14项双浏览器测试；漏洞为0阻断、7项批准例外。最终CI对应提交将再验证。

## 已验证能力

- 后端真实HTTP、独立PostgreSQL、配置失败、默认拒绝、响应与业务码、日志脱敏及留存、迁移往返；另有安全例外准确匹配、到期、严重级别升级和新增漏洞拒绝用例。
- 7项组件测试与14项Chromium/WebKit浏览器用例，覆盖四状态、恢复、键盘、未知页面、375/768/1280px；已人工检查桌面及手机截图。
- 真实API/PostgreSQL采样30次，p95低于500ms警告线。仅代表本机M0健康接口，不推断业务或公网性能。
- 候选镜像实际修改构建标识，响应及image_id均不同；切换后回退原镜像成功。容器停止与真实在途HTTP请求收到SIGTERM后完成均有证据，未超过30秒；Uvicorn信号重放保留实际退出码。
- 删除一项规范、加入无追踪场景均被门禁发现；真实隔离代码变异使子测试失败。构造的无效PAT被实际Gitleaks阻断，扫描报告脱敏。
- 合成数据pg_dump、Node内置AES-256-GCM加密、拒绝篡改、隔离数据库恢复核对成功；密钥不落盘。不是M4生产灾备验收。
- uv/pnpm锁文件、Python/Node安装许可清单、基础镜像摘要与全部运行镜像扫描。4项许可例外为EX-004；Caddy的7条HIGH按维护者批准的EX-005限期准入，不能称为零漏洞。

## 证据位置

`make check`生成的`artifacts/`不进入Git，CI作为脱敏附件保留90天。关键文件：pytest.xml、traceability.json、screenshots/、api-latency.json、runtime-verification.json、build-provenance.json、inflight-shutdown.json、mutation.json、restore-drill.json、licenses.json、trivy-*.json、vulnerability-decisions.json、gitleaks-*.json。版本标签与PR关联；关键证据需作为里程碑附件长期保存，不仅依赖90天CI保留期。

## 独立审查与返工

临时只读AI审查已发现并促成修复：环境标签不足以证明版本回退，改用实际不同镜像；空闲停机不足以证明排空，增加在途请求；日志只有容器轮转未落实7天，改应用留存；SPDX括号组合判断改递归解析；名义变异改真实修改与子测试；补真实数据库延时采样；Caddy默认错误日志可能泄露请求，改全局过滤并故障验证；修复新FastAPI路由匹配与测试线程收集竞态。最终独立复核确认扫描策略精确匹配、到期和错镜像阻断，未发现新的代码合并阻断项；最终PR提交仍须CI通过。

漏洞实扫进一步触发：Python从Debian改Alpine；PostgreSQL以C版su-exec替换Go启动器且不改数据库版本；Caddy移除非必要curl。官方Caddy仍含高危Go依赖，形成明确评估后由维护者单独批准EX-005，新增机器到期与范围阻断，不把许可批准扩展成安全批准。

## 外部或后续待验

同学／教师独立复现、真实手机、公网服务器域名、HTTPS、生产蓝绿、独立异地备份与RPO/RTO尚未验收。SSE、推演容量、模型预算、业务清理等按M1–M4启用。EX-005北京时间11月4日零点或进入M1/公网即失效，下一开发会话优先处理。干净克隆的本机复现另存机器报告，不能冒充同学复现。

## 仓库交付

[PR #1](https://github.com/sutu1023/echosim/pull/1) 的完整CI通过后Squash到main（33e3036）。干净克隆cf82c25通过make init/check/up，使用独立项目名和18382端口，探针200、数据库就绪；随后只清理该验收栈。关键机器证据长期保存在 [里程碑记录](evidence/v0.0.1-skeleton.json)，完整脱敏附件见该PR的Actions。规约已同步三项主规格并归档，最后的标签在归档PR通过后创建。
