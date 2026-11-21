# reproducible-verification Specification

## Purpose

用一条可重复执行的验收命令，把“实际被测代码”与干净克隆、CI 结果和人工验收绑定在同一证据链上，避免环境差异或 SHA 漂移造成不可信的结论。

## Requirements

### Requirement: 验收命令可重复执行

`make verify` SHALL 支持在无人工清理的情况下连续执行多次，每次都完整跑完验收场景并给出确定结果，结束时把环境留在可用状态。

#### Scenario: 连续两次执行均通过

- **WHEN** 在满足前置条件的环境中连续执行两次 `make verify`
- **THEN** 两次均以退出码 0 结束
- **AND** 第二次执行前无需人工停止容器、删除数据卷或修复上一次留下的状态
- **AND** 结束时数据库处于运行状态且首页返回 200

#### Scenario: 失败后仍可再次执行

- **WHEN** 某次 `make verify` 因任一场景失败而以非零退出码结束
- **THEN** 报告中记录失败的具体场景与原因
- **AND** 数据库被尽力恢复到运行状态，使下一次 `make verify` 仍可执行

### Requirement: 验收结果绑定被测 SHA

每次验收 SHALL 生成机器可读报告，记录本次实际测试的 Git 提交 SHA；当工作树存在未忽略的改动时，报告必须标记为脏，且默认拒绝把该结果当作干净克隆证据。

#### Scenario: 干净工作树生成可追溯报告

- **WHEN** 在无未提交改动的工作树执行 `make verify`
- **THEN** 在稳定路径 `runtime/verify/last-run.json` 生成报告，包含完整提交 SHA、`dirty: false`、开始与结束时间、逐场景结果和最终退出码
- **AND** 报告中的 SHA 等于执行时 `HEAD` 指向的提交

#### Scenario: 脏工作树默认拒绝

- **WHEN** 工作树存在未忽略的未提交改动且执行 `make verify`
- **THEN** 命令以非零退出码结束并说明工作树不干净
- **AND** 仅在显式允许时继续执行，此时报告记录 `dirty: true` 与 `HEAD` SHA，且不得宣称结果等价于干净克隆

### Requirement: 验收覆盖关键可观察场景

一次成功的验收 SHALL 实际验证启动、健康检查故障与恢复、后端进程不重启、账户隔离用户路径、停库期间的鉴权边界，以及公共控制台与工作台深链接可访问，而不是只做静态检查。布局、语言、截图与浏览器历史行为 MUST 由人工走查记录，不得写入自动化通过结论。

#### Scenario: 报告包含全部验收场景

- **WHEN** `make verify` 成功结束
- **THEN** 报告逐项记录并通过：首页 `/` 可访问且包含 EchoSim、`/health` 返回 200、停止数据库后返回 503、恢复数据库后返回 200、后端进程标识在故障前后不变、账户隔离用户路径通过、控制台与工作台深链接可访问、连续执行可重复
- **AND** 停止数据库到返回 503 的实测耗时被记录且不超过 5 秒

#### Scenario: 隔离用户路径逐步通过

- **WHEN** 验收使用两个真实账号 A 与 B 回放隔离路径
- **THEN** 报告至少包含以下可观察结果：A 登录成功、A 创建项目后刷新列表仍可见且详情返回 200、A 退出后旧会话访问私有接口返回 401、B 的列表与历史均不含 A 的项目、B 访问 A 的项目 ID 与访问不存在 ID 的状态码和响应体相同、A 重新登录后仍能打开原项目
- **AND** 每个结果作为独立场景记录在报告中

#### Scenario: 停库期间的鉴权边界被验证

- **WHEN** 数据库被停止且 A 的会话 Cookie 已捕获
- **THEN** 无 Cookie 的私有请求返回 401，携带 Cookie 的私有请求在 5 秒内返回 503 且响应不清除 Cookie
- **AND** 数据库恢复后同一 Cookie 的请求返回 200
- **AND** 上述结果各自记录为独立场景

#### Scenario: 控制台与工作台深链接可访问

- **WHEN** 验收已创建至少一个项目并读取其项目 ID
- **THEN** 对 `/`、`/projects` 以及 `/projects/<项目 ID>/workbench/evidence-graph`、`roles-environment`、`dual-platform-run`、`report`、`interaction` 的 HTTP GET 均返回 200 与包含 EchoSim 的 HTML 文档
- **AND** `/api/projects` 等 API 路径仍返回 JSON 或既有状态码，没有被 SPA 回退吞成 HTML
- **AND** 这些结果记录为独立的路由可访问性场景

#### Scenario: 自动验收与浏览器走查分离

- **WHEN** 自动化报告记录了控制台与工作台路由可访问
- **THEN** 报告不宣称已验证三种布局、中英文切换、截图或浏览器后退行为
- **AND** 这些结论只写入 `docs/acceptance/` 的人工走查记录，并与同一 Git SHA 对齐

### Requirement: CI 与干净克隆测试同一 SHA

CI SHALL 在干净检出上执行与本地相同的 `make verify`，并校验报告中的 SHA 与本次触发对应的提交 SHA 完全一致，不一致即判定失败。

#### Scenario: 成功 CI 与干净克隆 SHA 一致

- **WHEN** 向仓库推送提交或在拉取请求上触发 `verify` 工作流
- **THEN** 工作流在干净检出上执行 `make verify`，并读取 `runtime/verify/last-run.json`
- **AND** 当报告 SHA 不等于本次触发 SHA 时工作流失败
- **AND** 工作流无论成功或失败都上传该报告作为可下载的构建产物，供与干净克隆结果比对

### Requirement: 受控失败运行产出可下载报告

CI SHALL 提供确定性的受控失败入口（手动触发并可覆盖期望 SHA），用于验证失败路径；受控失败运行时工作流 MUST 以失败结束，且 MUST 上传记录失败场景与原因的报告。

#### Scenario: 期望 SHA 不一致的受控失败

- **WHEN** 手动触发 `verify` 工作流并指定与检出 HEAD 不一致的期望 SHA
- **THEN** 工作流以失败结束
- **AND** 报告中 `result` 为 `failed`，并记录 SHA 不一致的具体原因
- **AND** 该报告可作为构建产物下载

### Requirement: 人工验收单独记录

人工验收 SHALL 记录在版本库内的独立位置，与 CI 产物分离；记录通过 SHA 与当次被测代码及 CI 结果关联，且不被自动化流程创建或覆盖。

#### Scenario: 人工验收记录不落 CI

- **WHEN** 验收人完成浏览器人工验收
- **THEN** 在 `docs/acceptance/` 新增一条记录，包含被测 SHA、日期、验收人、三个验收场景的结果与证据位置
- **AND** `make verify` 与 CI 均不创建、修改或删除该目录下的记录
- **AND** 通过相同的 SHA 可以把人工记录、干净克隆结果与 CI 产物三者对应起来

### Requirement: 隔离用户路径可重复验收

`make verify` SHALL 在同一个被测 SHA 上回放账户隔离用户路径，并保证重复执行不因账号或项目残留而失败；准备账号与命名项目 MUST 使用幂等操作，不得要求人工介入。

#### Scenario: 重复执行隔离场景

- **WHEN** 在已存在验收账号与验收项目的环境中再次执行 `make verify`
- **THEN** 命令以退出码 0 结束，不因邮箱重复或项目重名而失败
- **AND** 每次执行都使用可区分的新项目名称，报告记录本次创建的项目标识

#### Scenario: 账号准备失败时明确失败

- **WHEN** 用于验收的两个账号无法创建或更新
- **THEN** 验收以非零退出码结束，并以独立场景记录失败原因
- **AND** 不把后续接口检查记为通过

### Requirement: 会话测试与浏览器走查分离

自动化验收 SHALL 用服务端会话测试证明“仅凭 Cookie（不依赖浏览器内存状态）即可访问”，把浏览器关闭重开的行为留给人工走查；两者 MUST 分别记录，自动化结果不得宣称验证了浏览器进程行为。

#### Scenario: 服务端会话测试使用显式 Cookie

- **WHEN** `make verify` 需要验证会话不依赖客户端内存
- **THEN** 它用登录响应中的 Cookie 构造一个新的 HTTP 客户端，并断言会话查询与项目详情返回 200
- **AND** 报告把该结果标注为服务端会话测试，而不是浏览器重开测试

#### Scenario: 浏览器重开由人工走查记录

- **WHEN** 验收人按 README 的走查步骤完全关闭并重开浏览器
- **THEN** 访问私有页面跳转登录页，重新登录后可以访问
- **AND** 该结果写入 `docs/acceptance/` 的人工记录，不写入 `make verify` 报告

### Requirement: 变更专属验收场景与任务证据

`make verify` SHALL 支持通过 `CHANGE=<change-id>` 选择变更专属场景，并在报告中记录所选变更。未指定 `CHANGE` 时 MUST 继续执行 Change 01–03 的全部既有场景，并额外执行本变更的任务底座基础场景；指定 `CHANGE=2026-11-21-track-background-jobs` 时 MUST 在基础场景之上追加断线续传、游标优先级、worker 中断恢复、旧租约令牌回归、并发重复开始、取消、受控失败与终态重连场景。未知的 `CHANGE` 值 MUST 以非零退出码结束并说明原因，MUST NOT 静默跳过场景。

任务场景的结果 MUST 写入可机器读取的证据：`jobId`、`runId`、`attempts`（中止前与接手后）、断开前与重连后的**两段实际序号**、重连游标、最终事件序号列表、副作用总数与去重后的步骤序号、旧令牌续租与写入的拒绝结果。报告 MUST 继续满足既有 `tested_sha`、`dirty`、逐场景结果的契约。

#### Scenario: 默认执行保留既有回归

- **WHEN** 以不带 `CHANGE` 的 `make verify` 执行验收
- **THEN** 报告仍包含首页、`/health` 200/503/200、`/livez`、容器身份不变、账户隔离、停库 401/503、退出失效、重新登录与工作台深链接等 Change 01–03 场景
- **AND** 新增任务基础场景：开始返回 202、状态可查询、确定性任务到达 `succeeded` 且事件 1–10、匿名 401、他人项目 404、停库 503 保留 Cookie、只停 worker 时 `/health` 与 `/livez` 仍为 200 且容器身份不变
- **AND** 报告中的 `tested_sha` 仍等于执行时 `HEAD`

#### Scenario: 两段真实 SSE 序号分别记录

- **WHEN** 以 `CHANGE=2026-11-21-track-background-jobs` 执行验收
- **THEN** 验收脚本建立真实 SSE 连接，**读完 `seq=4` 的 `id:`、`data:` 与帧尾空行后**才关闭连接，记录第一段实际序号
- **AND** 以游标 4 重连直到 `stream-end`，记录第二段实际序号
- **AND** 报告断言第一段为 `[1,2,3,4]`、第二段为 `[5..10]`、拼接后恰好 1–10 且每条一次，并写入 `jobId`、`runId`
- **AND** 另测 `Last-Event-ID` 优先于 `after`、`after=lastSeq+1` 返回 400、终态任务 `after=10` 在 2 秒内收到 `stream-end`

#### Scenario: worker 中断恢复被数据判据核实

- **WHEN** 任务已提交至少 2 步且未终态时停止 worker 容器
- **THEN** 报告记录停止时的 `jobId`、`runId`、已提交步骤与 `attempts`，并断言持久状态仍为 `running` 且 `awaitingTakeover=true`
- **AND** 恢复 worker 后跟踪到终态，副作用查询返回总数 10、步骤序号 1–10 各一次，`attempts` 大于 1
- **AND** 若出现重复步骤或总数超过 10，场景判定失败

#### Scenario: 旧租约令牌写入被真实数据库拒绝

- **WHEN** 变更专属场景执行真实 PostgreSQL 回归：第一次领取提交到第 3 步、强制租约到期、第二次领取提交到第 5 步，再用旧令牌续租并写第 6 步
- **THEN** 续租与写入均影响 0 行，任务进度、事件序号与副作用计数与接手后一致
- **AND** 新持有者完成 6–10，最终事件仍为 1–10 且副作用为 10

#### Scenario: 重复开始与取消/失败被独立断言

- **WHEN** 变更专属场景执行
- **THEN** 两个并发开始请求得到一个 202 与一个 200，指向同一对 `jobId`/`runId`，且数据库只新增一个 job 与一个 run
- **AND** 运行中取消的任务以 `canceled` 终态结束（已提交 3 步时终态事件 `seq=4`），受控失败的任务以 `failed` 终态结束，均 MUST NOT 出现 `completed`
- **AND** 取消与最后一步的竞争重复执行时，每次只存在一个终态事件
- **AND** 上述结果各自记录为独立场景

#### Scenario: 未知 CHANGE 明确失败

- **WHEN** 以未注册的 `CHANGE` 值执行 `make verify`
- **THEN** 命令以非零退出码结束并在报告中说明“未注册的变更”
- **AND** 不把任何场景记为通过

### Requirement: 浏览器行为与自动化结论分离

任务相关的 SSE 中断提示、worker 中断提示、重连过程、布局与语言保持、截图与可观察时限 MUST 由人工在浏览器中走查并记录，MUST NOT 被 `make verify` 记为通过。自动化报告 MUST 明确其只证明服务端断线续传、状态机与副作用计数。

人工验收记录 SHALL 包含被测 `tested_sha`、日期、验收人、`jobId`、`runId`、断开前与最终的实际事件序号、worker 中断时的已提交步骤，并引用同 SHA 的 `make verify` 报告与浏览器证据位置。

#### Scenario: 自动化不宣称页面已验收

- **WHEN** `make verify CHANGE=2026-11-21-track-background-jobs` 通过
- **THEN** 报告只声明服务端 SSE 断线续传、状态转换与副作用计数结果
- **AND** 不包含“页面提示已验证”“布局与语言保持已验证”之类的结论

#### Scenario: 人工记录按 SHA 与任务对齐

- **WHEN** 验收人在浏览器完成断线、重连、worker 中断与失败/取消走查
- **THEN** 记录写入 `docs/acceptance/`，包含 `tested_sha`、`jobId`、`runId` 与实际事件序号
- **AND** 可通过同一 SHA 把人工记录与 `make verify CHANGE=…` 报告对应起来
- **AND** `make verify` 与 CI 均不创建、修改或删除该记录

#### Scenario: 变更专属场景可重复执行且绑定 SHA

- **WHEN** 在同一干净提交上连续两次执行 `make verify` 与两次 `make verify CHANGE=2026-11-21-track-background-jobs`（均携带 `EXPECTED_SHA`）
- **THEN** 四次都以退出码 0 结束，四份报告的 `tested_sha` 都等于该提交
- **AND** 第二次执行前无需人工停止容器、清理数据卷或修复上一次的残留任务
- **AND** 交付说明列出四份报告路径与两次运行的任务证据
