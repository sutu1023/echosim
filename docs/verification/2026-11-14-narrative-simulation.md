# Narrative simulation 实现验证

**最终验收通过。** 最终评分为对峙篇 8/10、修复篇 8/10、披露篇 8/10。前两篇沿用创作者确认的 v6 结果，披露篇保留 v6 的 7/10 失败记录，在相同输入上重新推演后独立复评。见[披露篇复评与全文](2026-11-14-disclosure-review.md)和[三篇原稿与确认评分](2026-11-14-narrative-review-v6.md)。 三个变更的 39 项任务已完成。下文保留各批次当时的结果。


状态：核心行为已实现，完整任务质量门槛尚未通过。

## 已验证行为

`test_narrative.py` / `test_simulation.py` 验证3–6段、新旧读兼容、独立角色、动态调度、提前结束/24条上限及秘密过滤。新增测试证明作者概要、大纲和他人背景不进入角色上下文。`test_zzz_simulation.py` 在隔离数据库证明24成功/25拒绝、旧九条原ID及内容保留、迁移重放、SSE读取基础、排队停止不产生可采纳结果、在途停止保留完整事件并进入pending、跨用户404。`NarrativePlayer.test.tsx` / `Simulation.test.tsx` 覆盖历史完整显示、逐字队列、立即显示、减少动画、滚动与去重。

## 真实模型结果

- `results/narrative-eval-v3.json`：首次18案例，goal-3误拒，三个完整任务分别在1/9/0条事件后失败（事实冲突或上游错误）。
- `results/narrative-eval-v3-recheck.json`：区分调度计划与角色正文后，15项短案例全通过；三个完整任务在4/3/7条后失败，未达到全任务验收。
- `results/narrative-full-v3-observed.json`：收紧作者概要/大纲隔离，并明确场景道具的另一次完整任务尝试；三个任务分别在1/0/0条后出现上游错误。此批改变了完整场景的道具说明，不与旧批冒充同一输入。
- `results/narrative-network-diagnosis.json` / `narrative-network-status.txt`：限定在首事件后主动停止的诊断，四次HTTP200，首事件68.17秒、337字、6段。它不是一次完整任务质量通过证据。
- 实际模型为 `deepseek-v4.1-flash`，配置版本、完整已产生事件与失败保留在JSON中。尚无三次通过的完整任务，五维人工评分和80%字数门槛未完成，不勾选任务4.1。

## 真实浏览器

新建专用测试作品“ 三项联合验收 · 灯室交接 ”，观察确认、初始图谱、动态阶段和姓名对白/旁白。首轮1条事件后上游失败，保留阅读、无采纳入口，图谱仍为版本0。第二轮在1条完整事件后主动停止，显示整次不可逆采纳入口；模型在途错误未丢弃已校验事件。刷新历史直接完整显示，界面不显示参与者/visibility标签。尚需完整任务逐字播放、键盘与无障碍的全面人工验收。

## 评测负载说明

早期多组脚本与真实worker并行调用同一供应商，峰值可能超过单组Semaphore。短请求诊断成功而并行批次集中失败，不能仅凭这些结果判定产品单任务稳定性；后续必须串行复跑并记录正常负载。失败记录仍保留。

## 限制

模型语义校验仍会误拒，外部网络/供应商失败会结束任务；不能用当前样例证明角色互动质量已达约定阈值。已修复终态仍显示生成阶段、模型服务错误缺乏明确文案的问题。迁移回退说明见 `docs/story-creation.md`。

## 环境与门禁

- 2026-11-14，本机 macOS、Docker Compose 开发环境，使用一次性 PostgreSQL 做集成测试，不使用现有数据库跑破坏性合同。
- `make check`：后端单测、前端组件测试/类型/构建、仓库检查和10项严格OpenSpec校验通过；最终计数见联合记录。
- `make smoke`：真实 readiness、断库/恢复、业务身份边界、前端、非root与SIGTERM通过；最终复跑记录见联合结果。
- `make audit` 首次因 PyPI TLS EOF 失败，原样保留；重试后 Python/Node 均无已知漏洞。未改变证书校验或放宽门禁。
- 未提交、未同步主规约、未归档。不能把自动测试或模型自评当成人工剧情质量验收。

## 推理参数与会话修正

实际日志出现事实提取44秒/3845 completion tokens，校验多次接近一分钟且无有效最终输出。核查官方文档：[DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/) 说明默认高推理及low映射；[OpenCode Go](https://opencode.ai/docs/console/go/) 要求同一会话稳定session以优化路由/缓存。实现按后台任务ID传递session，默认deepseek-v4.1-flash明确reasoning_effort=low，其他模型不添加此参数。没有关闭校验、取消超时或透出未校验内容。`test_generation_uses_task_session_and_explicit_low_reasoning` 验证同任务复用、不同任务分离与自定义模型不误加参数。原失败批次为参数修正前，不能与新参数结果混同。

## 最后串行诊断

`narrative-eval-v4-serial.json`、`narrative-full-v4-diagnostic.json`、`narrative-short-v4-diagnostic.json` 保留串行失败，排除了“只因并行负载”的解释。`narrative-format-diagnosis.json` 的共同解题任务生成2事件后MODEL_INVALID_RESPONSE，拒绝记录包含共享旁白泄漏内心的候选；这批使用role-action-v3，没有使用新增地址回退。随后role-action-v4明确字段白名单、禁止复制历史schema_version/segment_id，并保留仅本人可见的内心段要求。

`narrative-network-fallback.json` 使用新地址回退和role-action-v4，仍在首事件前MODEL_UPSTREAM_ERROR；安全诊断为ConnectError/ConnectError，不能声称连接问题已解决。独立TCP/TLS探针两个公网IPv4可建立TLS1.3、IPv6不可达；该探针成功不等于模型请求持续可用。连接实现只尝试经整体公网校验的DNS地址，保留TLS、60秒调用上限和三次有界重试。

评测脚本现在额外记录合成故事的非法结构候选，生产日志仍不写正文或凭据。完整质量验收继续未通过，未放宽安全、结构或人工rubric阈值。

真实传输探针观察到 TCP 建连成功后的 TLS 握手以 SSLEOFError / UNEXPECTED_EOF_WHILE_READING 退出，重新连接后连续收到 HTTP 响应。worker 在同一事件循环内复用带公网地址校验的连接池，退出时关闭。新握手只在写入请求前重试一次。各请求显式绑定密钥及任务会话，池化测试确认响应 Cookie 不会转发给下一用户。隔离 PostgreSQL 覆盖 240 秒事件超时、租约过期后旧 worker 退出且零事件、在途停止，单测覆盖 2700 秒总限。角色现在收到已经校验的行动机会。失败的调度只收到固定重规划提示，最多三次。

真实 Chrome 验证逐字播放、停止、永久放弃与再次启动，且图谱一直保持版本 1。键盘检查覆盖来源打开聚焦与关闭返回。通过拦截合成响应另验 HTML 文本、播放期间终态和采纳控制。见 results/browser-playback-adoption-checks.txt、screenshots/2026-11-14-live-playback.jpg。

最终固定评测采用 architect-v3、role-action-v6、simulation-validation-v4。架构模型收到公开的场景状态，角色获得已校验的行动机会与允许引用的事件序号。角色提示给出单事件长度分配与同类工序合并建议。15个短案例全部符合预期。共同解题18条全部在长度范围，总耗时1161.24秒。秘密披露18条中16条在范围，总耗时875.11秒。对峙首跑14条后出现上游错误，保留失败。正文、拒绝候选、终态及计时见 results/narrative-v6-fixed-18.json，阅读版见 2026-11-14-narrative-review-v6.md。此前一个旧版对峙任务达到24条硬上限，持久化和长度上限正常，但不以此替代自然收束的审阅。

大请求另有请求发送后等待响应时的远端断开。创作调用在后端接收供应商SSE，读取上限1 MiB，只有收到finish_reason与[DONE]才组装正文并进入原校验流程。忽略reasoning_content，保留有效usage。非流式JSON响应仍校验。临时连接诊断保持非流式及无连接重试，worker连接复用原公网/TLS校验。测试覆盖流不完整、推理字段排除、正文拼接、token截断。真实24事件采纳用此传输成功。
