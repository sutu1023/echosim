# Spec Delta

## ADDED Requirements

### Requirement: Change 09 双平台运行离线验收

`make verify CHANGE=2026-11-24-run-dual-platform-simulation` SHALL 在默认验收场景之上追加双平台运行场景，并在同一被测 SHA 上写入 `simulation_evidence`。专属场景 MUST 使用运行时生成的原创微型材料、真实 PostgreSQL、真实 worker、真实租约与事件流，只用替身引擎替换 OASIS 运行时与模型调用；MUST NOT 读取私有材料、私有样本或真实模型密钥，MUST NOT 安装候选引擎依赖。

`simulation_evidence` MUST 记录 `mode`、`engine`、`calls.real`、`calls.double`、`callScope`，以及每个运行场景的 `runId`、`selectionId`、`buildId`、`snapshotId`、冻结轮数、每平台已提交轮次、动作总数与按原生动作类型分解、引擎记录标识、目标原帖链接、停止前后计数、接手前后 `attempts` 与补记结果、失败平台与原因码。`callScope` MUST 列出被统计的真实运行标识，计数 MUST 来自引擎调用边界的逐运行持久化值，MUST NOT 用计划轮数、角色数或事件条数推算。

带 `CHANGE` 与不带 `CHANGE` 的验收 MUST 都保留既有健康、会话、项目隔离、材料、任务、图谱与准备场景。未知 `CHANGE` MUST 仍以退出码 2 结束；同一环境连续两次执行 MUST 都通过，报告 `tested_sha` MUST 等于执行时 `HEAD`。

#### Scenario: 默认验收不触发双平台运行

- **WHEN** 运行默认 `make verify`
- **THEN** 既有场景全部保留
- **AND** 在无冻结选择时开始运行返回 409 `selection_missing`，数据库不新增运行、job 或平台记录
- **AND** 报告 `simulation_evidence` 记为未触发，真实模型与引擎外呼计数为 0
- **AND** 不把离线结论写成真实本地 OASIS 已联通

#### Scenario: 冻结三轮后两个平台都有读回的原生动作

- **WHEN** 运行 Change 09 专属验收：用原创材料完成建图与准备，确认冻结 3 轮后开始运行
- **THEN** 运行为 202，worker 依次提交 `bind`、`environment`、`advance`、`publish`
- **AND** `platform_a/twitter` 与 `platform_b/reddit` 各自提交 3 轮并至少各有一个从引擎记录读回的原生动作
- **AND** 运行详情返回模拟时钟、每平台轮次、动作与按类型分解
- **AND** 报告记录 `engine=double`、`calls.real=0` 与大于 0 的替身调用计数
- **AND** 验收样例为 6 个角色（由原创材料准备的冻结选择决定），断言取实际冻结角色数，不写死 6

#### Scenario: 目标原帖定位与三方计数一致

- **WHEN** 逐个平台核对一条评论或转发与其原帖
- **THEN** 该动作的目标引用指向同一运行、同一平台的原帖动作
- **AND** 引擎记录、运行详情计数与页面读取接口返回的计数一致
- **AND** 报告记录该动作、目标动作、两平台计数与 `byAction` 分解

#### Scenario: 停止后计数稳定并可用同一选择重跑

- **WHEN** 运行中请求停止
- **THEN** 停止返回 200 与同一 `runId`，运行在下一个轮次边界进入 `canceled`
- **AND** 停止后连续两次读取的轮次与动作计数不变，迟到结果不产生新记录
- **AND** 用同一 `selectionId` 重新开始返回 202 与新 `runId`
- **AND** 旧运行仍可通过其 `runId` 读取同一终态、计数与动作

#### Scenario: 引擎已执行未记账时接手补记

- **WHEN** 某轮动作已写入引擎数据库后停止 worker，租约过期后恢复 worker
- **THEN** `attempts` 大于 1，接手先读回该轮并补记，不重发动作
- **AND** 补记后该平台动作总数与引擎记录一致，无重复引擎记录标识
- **AND** 报告记录接手前后计数与补记的轮次

#### Scenario: 单平台失败保留另一平台结果

- **WHEN** 专属验收让一个平台按原因码失败
- **THEN** 另一个平台继续完成剩余轮次并提交记录
- **AND** 运行以 `failed` 结束，记录 `failedPlatforms` 与原因码
- **AND** 成功平台的轮次、动作与按类型计数仍可读取
- **AND** 报告不把该运行计为 `succeeded`

#### Scenario: 访问与错误边界被一致拒绝

- **WHEN** 匿名访问运行接口，或使用他人项目、他人运行、不存在选择与非法 `selectionId`
- **THEN** 匿名返回 401 且不访问运行数据，他人项目与不存在项目返回一致 404 `项目不存在`
- **AND** 他人运行返回 404 `运行不存在`，缺少选择返回 409 `selection_missing`，非法选择返回 422 `selection_invalid`
- **AND** 停库时携带会话的运行读取返回 503 且不清除 Cookie
- **AND** 响应不包含动作正文、角色、路径或内部标识

#### Scenario: 未知变更与重复执行

- **WHEN** 使用未注册的 `CHANGE` 运行验收，或对同一提交连续两次执行 Change 09 验收
- **THEN** 未知变更以退出码 2 结束并列出可用变更
- **AND** 两次执行都以退出码 0 结束，无需人工清理残留运行
- **AND** 两份报告的 `tested_sha` 都等于该提交

#### Scenario: 真实本地 OASIS 证据只来自人工显式运行

- **WHEN** 作者使用原创材料在隔离引擎镜像中显式执行真实本地 OASIS 双平台运行
- **THEN** 证据只来自该运行自身读回的引擎记录，报告标记 `mode=live`、`engine=oasis`、`calls.real>0`
- **AND** 离线替身报告与真实运行报告分别记录，互不替代
- **AND** 私有 25 页样本、含正文截图与真实动作日志留在仓库外，`docs/acceptance/` 只记录脱敏结论、SHA、`runId` 与证据链接
- **AND** 自动化与 CI 不引用该材料、文件名、路径、正文或截图
