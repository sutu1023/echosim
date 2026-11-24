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

### Requirement: 默认验收包含轻量材料回归

不带 `CHANGE` 的 `make verify` SHALL 在既有 Change 01–04 场景之外执行一条轻量材料场景，因为 GitHub CI 实际运行这个入口：所有者上传一份自制小 TXT 后，worker 必须把材料处理到 `ready`，材料列表可见且至少返回一个分块，匿名读取材料列表返回 401。该场景 MUST 在无人工清理的情况下连续两次执行都通过。

重型场景（20 MiB 边界、PDF 页码与来源、跨账户隔离、worker 接手与取消）MUST 只在 `CHANGE=2026-11-21-ingest-seed-documents` 中执行，MUST NOT 让默认流程依赖私有样本或大文件。

#### Scenario: 默认流程的材料冒烟连续两次通过

- **GIVEN** 一个可重复执行的环境
- **WHEN** 连续两次执行不带 `CHANGE` 的 `make verify`
- **THEN** 两次都以退出码 0 结束，报告逐项记录材料上传、`ready`、列表可见、分块数至少为 1
- **AND** 第二次执行前无需停止容器、删除数据卷或修复上一次留下的材料
- **AND** 两次报告都记录匿名材料列表返回 401

#### Scenario: 默认流程不宣称覆盖重型材料场景

- **WHEN** 审查不带 `CHANGE` 的报告
- **THEN** 报告不包含 20 MiB 边界、PDF 物理页码、跨账户材料访问或 worker 接手的结论
- **AND** 这些结论只出现在变更专属报告中

### Requirement: 材料入库变更专属验收场景与证据

`make verify` SHALL 支持 `CHANGE=2026-11-21-ingest-seed-documents`，在默认场景之上追加材料入库场景，并在报告 `change` 字段记录该标识。`CHANGE=2026-11-21-track-background-jobs` MUST 保留其原有全部断言与结果；未注册的 `CHANGE` 值 MUST 仍以退出码 2 结束。

材料场景 MUST 使用脚本在运行时自制的夹具，不提交二进制样本：三页 PDF（第 1、3 页已知正文、第 2 页空白）、含重复页眉与打印路径的 PDF、含混合换行与 emoji、以及正文合法路径的小 TXT/Markdown、GB18030 中文 TXT、恰好 20 MiB 的有效文件、20 MiB + 1 字节文件、空文件、无文字层 PDF、用于页数上限的 3 页与 4 页 PDF。夹具 MUST 在本次执行结束后清理，MUST NOT 提交到仓库或上传为 CI 产物。

报告 MUST 新增机器可读的 `document_evidence`，至少记录：`documentId`、解析任务 `jobId`/`runId`、`pageCount`、`chunkCount`、第 3 页分块的 `pageNo` 与来源 URL、原件 `sha256`、重复上传前后的材料数与分块数、取消与接手前后的 `attempts` 与状态、各失败场景的原因标识。

#### Scenario: 三页 PDF 的页码与空白页被断言

- **GIVEN** 变更专属场景上传自制的三页 PDF
- **WHEN** 解析完成
- **THEN** 报告记录 `pageCount=3`、状态为就绪，第 2 页分块数为 0，第 3 页分块数为正
- **AND** 第 3 页分块的 `pageNo=3`，其文本等于第 3 页已知正文
- **AND** 含空白页的该 PDF 被正常导入，而不是被判定为失败或总页数为 2

#### Scenario: 来源链接指向原件第 3 页

- **GIVEN** 三页 PDF 已就绪
- **WHEN** 验收读取第 3 页分块的来源地址并下载原件
- **THEN** 来源地址以 `#page=3` 结尾，原件响应为 `application/pdf` 且字节的 SHA-256 等于上传值
- **AND** 对原件字节做结构检查时，第三个页对象包含第 3 页已知正文
- **AND** 报告不宣称已验证浏览器 PDF 阅读器实际停在第 3 页（该结论只写入人工验收记录）

#### Scenario: PDF 页眉页脚与打印路径清理被断言

- **GIVEN** 变更专属场景上传一份每页带相同页眉、页脚含打印路径的 PDF
- **WHEN** 解析完成
- **THEN** 分块文本不包含这些重复页眉、页脚与打印路径
- **AND** 每个分块的 `pageNo` 与清理前一致，正文行未被删除

#### Scenario: 文本清洗与段落定位被断言

- **GIVEN** 变更专属场景上传一份含混合换行、连续空白、emoji 与正文合法路径的 TXT 或 Markdown
- **WHEN** 解析完成
- **THEN** 分块文本中换行已统一、连续空白已折叠
- **AND** 正文中的合法路径与 URL 按原样保留
- **AND** 每个分块的 `sourceStart`/`sourceEnd` 在 `/source` 原文中切出的内容与摘录开头一致，含 emoji 时高亮不偏移
- **AND** 报告记录清洗前后的行数与分块数

#### Scenario: GB18030 原件与原文视图被分别断言

- **GIVEN** 变更专属场景上传一份 GB18030 中文 TXT
- **WHEN** 分别请求原件与 `/source`
- **THEN** 原件字节的 SHA-256 与上传值一致，响应声明禁止类型嗅探
- **AND** `/source` 返回 UTF-8 文本且中文可读，分块偏移在该文本中可切回原文

#### Scenario: 大小与空文件边界各自给出原因

- **GIVEN** 变更专属场景依次上传恰好 20 MiB 的有效文件、20 MiB + 1 字节文件与空文件
- **WHEN** 三个请求完成
- **THEN** 恰好 20 MiB 的首次上传新增一份材料并最终进入 `ready`
- **AND** 20 MiB + 1 字节返回 413，空文件返回 400，这两次拒绝都不新增材料记录
- **AND** 存储目录中不留下被拒文件对应的残片，报告记录三次请求的状态码与原因标识

#### Scenario: 配置变化后历史失败原因保持解析时上限

- **GIVEN** 变更专属场景以 `DOCUMENT_MAX_PAGES=3` 启动 worker，上传 4 页 PDF 并失败
- **WHEN** 恢复默认上限 2000 后重新读取同一材料的列表与详情
- **THEN** 两者都返回解析时上限 3 与实际页数 4
- **AND** 材料列表的当前 `limits.maxPages` 为 2000，证明历史值与当前配置已分离
- **AND** 报告记录解析时上限、当前上限与实际页数

#### Scenario: 页数上限用测试配置验证

- **GIVEN** 变更专属场景以较小的 `DOCUMENT_MAX_PAGES`（例如 3）启动 worker
- **WHEN** 分别上传 3 页与 4 页 PDF
- **THEN** 3 页文件进入 `ready`，4 页文件失败且原因标识为“页数过多”，说明中包含上限 3
- **AND** 跑完后恢复默认配置

#### Scenario: 无文字层 PDF 给出明确原因

- **GIVEN** 变更专属场景上传一份整份没有可提取文字的 PDF
- **WHEN** 解析完成
- **THEN** 材料状态为失败，原因标识为“没有文字层”
- **AND** 该材料分块数为 0，列表中显示的原因不是“成功”或空白

#### Scenario: 重复上传只产生一套材料与分块

- **GIVEN** 账户 A 在同一项目中对同一文件已成功上传一次
- **WHEN** A 再次上传字节完全相同的文件
- **THEN** 第二次返回 200 并标记为重复，材料标识与首次相同
- **AND** 该项目的材料数量与分块总数与重复上传前一致，解析任务数量不增加

#### Scenario: 匿名与跨账户访问被一致拒绝

- **WHEN** 变更专属场景以匿名请求材料列表、原件与分块，并以账户 B 请求账户 A 的原件与分块
- **THEN** 匿名请求均返回 401 JSON，且不返回文件流
- **AND** B 的请求返回与随机材料标识相同的 404 响应体，响应中不含 A 的文件名、页数或分块内容

#### Scenario: worker 停止时取消排队任务立即生效

- **GIVEN** worker 已停止，一份材料刚上传且 job 仍为 `queued`
- **WHEN** 账户 A 取消该任务
- **THEN** job 立即为 `canceled`，并有一条 `canceled` 终态事件
- **AND** 材料为 `failed` 且原因为“已取消”，分块数为 0
- **AND** 重新上传同一文件得到新的材料与任务

#### Scenario: 完成先提交时取消不改写材料

- **GIVEN** 材料已 `ready`，其解析任务为 `succeeded`
- **WHEN** 再次取消该任务
- **THEN** 响应返回 `succeeded`，材料保持 `ready`，分块数不变

#### Scenario: 慢解析期间续租并被断言

- **GIVEN** 变更专属场景以测试用的解析延迟启动 worker，使单步耗时超过租约
- **WHEN** 解析期间轮询任务详情
- **THEN** 任务保持 `running` 且不出现 `awaitingTakeover`，`attempts` 保持为 1
- **AND** 在延迟窗口内停止 worker 后，任务在租约到期时进入等待接手，恢复后 `attempts` 增加
- **AND** 接手完成后分块数与一次性解析一致，无重复序号

#### Scenario: 未知 CHANGE 仍然失败

- **WHEN** 以未注册的 `CHANGE` 值执行 `make verify`
- **THEN** 命令以退出码 2 结束并在报告中说明“未注册的变更”
- **AND** 材料场景不被记为通过

### Requirement: 前向迁移与旧任务兼容

`0003` 迁移 SHALL 只做前向变更，并在同一事务内扩展 `runs.kind` 约束：保留 `test.simulation`、加入 `document.ingest`。在已经存在 Change 04 任务的旧数据卷上执行升级后，旧任务 MUST 仍可读取，服务重启后 MUST 仍可读取，且新的材料解析任务 MUST 能正常执行。迁移 MUST 可在已有数据卷上重复安全启动。

#### Scenario: 含旧任务的旧数据卷可升级

- **GIVEN** 一个已经执行过 `0002` 并留有 Change 04 测试任务的数据卷
- **WHEN** 启动新版本，容器入口应用 `0003`
- **THEN** 迁移记录包含 `0003`，旧的 `test.simulation` 任务详情、事件与副作用仍可读取
- **AND** 重启 `app` 与 `worker` 后旧任务仍可读取

#### Scenario: 升级后可执行材料解析任务

- **WHEN** 在同一数据卷上上传并解析一份材料
- **THEN** 新任务以 `document.ingest` 执行并到达成功终态
- **AND** 再次执行 `make migrate` 输出“无待应用迁移”

### Requirement: 私有样本只用于人工验收

25 页私有样本 MUST 只在浏览器中人工验收，MUST NOT 进入 Git、CI 产物、验收报告或自动化场景。样本 MUST 保存在仓库外的本机目录或仓库已忽略的 `private-inputs/`，MUST NOT 被任何命令复制到 `runtime/`、提交或构建产物中。自动化脚本与测试数据 MUST NOT 引用该样本的文件名、路径或正文片段。

人工验收 MUST 由项目作者在自己的 Mac 浏览器完成：核对总页数，并从第 1 页与第 3 页的摘录点击来源，确认原件打开后实际落在对应物理页。人工验收记录 MUST 包含被测 `tested_sha`、日期、验收人、该样本的总页数、两次跳转的实际落页与截图位置，并引用同一 SHA 的 `make verify CHANGE=2026-11-21-ingest-seed-documents` 报告。记录 MUST 在浏览器签收后才写入 `docs/acceptance/`，`make verify` 与 CI MUST NOT 创建、修改或删除该记录。

#### Scenario: 自动化不包含私有样本

- **WHEN** 审查仓库、`make verify` 场景与 CI 产物
- **THEN** 找不到该私有样本的文件、路径、文件名或正文片段
- **AND** 报告只包含自制夹具的证据

#### Scenario: 人工记录按 SHA 对齐

- **GIVEN** 验收人在 Mac 浏览器上传该私有样本并核对总页数与第 1、3 页落页
- **WHEN** 验收人写入记录
- **THEN** 记录包含 `tested_sha`、总页数、两次跳转的实际落页与截图位置
- **AND** 记录引用的 `make verify` 报告具有相同 SHA 且 `dirty: false`

#### Scenario: 自动化不宣称浏览器落页已验证

- **WHEN** `make verify CHANGE=2026-11-21-ingest-seed-documents` 通过
- **THEN** 报告只声明总页数、分块页码、来源 URL 与原件字节一致
- **AND** 不包含“PDF 阅读器已停在第 3 页”“截图已核对”之类的结论

### Requirement: Change 06 离线验收不依赖真实凭据与外部网络

`make verify` 与 `make verify CHANGE=2026-11-21-connect-engine-providers` SHALL 在没有真实模型密钥、没有 Zep 密钥、也没有模型服务或图谱服务网络连接的条件下完成。默认 `make verify` MUST 继续执行 Change 01–05 的全部既有场景；Change 06 专属场景 MUST 使用本地测试替身与纯契约校验增加引擎接入的离线回归，MUST NOT 读取 `.env` 中的真实密钥、MUST NOT 安装候选引擎依赖、MUST NOT 发起真实模型、图谱或仿真平台调用。

#### Scenario: 默认验收在没有引擎凭据时仍通过

- **WHEN** 环境中不存在模型与 Zep 密钥，且模型与图谱服务不可达
- **THEN** 不带 `CHANGE` 的 `make verify` 仍以退出码 0 结束
- **AND** 报告继续包含 Change 01–05 的首页、`/health` 200/503/200、`/livez`、容器身份、账户隔离、停库鉴权、任务事件、材料冒烟与工作台深链接等既有场景
- **AND** 报告记录真实引擎调用次数为 0，且运行过程不构建 `engine` profile 或安装候选引擎依赖

#### Scenario: Change 06 专属离线场景通过

- **WHEN** 以 `CHANGE=2026-11-21-connect-engine-providers` 执行 `make verify`
- **THEN** 命令在无真实密钥与无外部网络时以退出码 0 结束
- **AND** 报告 `change` 字段等于 `2026-11-21-connect-engine-providers`
- **AND** 报告新增离线引擎证据，至少覆盖模型字段与来源校验、EchoSim User-Agent、移除 `response_format` 重试、402/403-1010/普通 403/401 的精确状态码与原因码、未知错误码不回显、无关元数据 1010 不误判、图谱受理/索引/命中状态机、服务端已受理但响应丢失且首次搜索未命中、双平台独立判定、单平台失败保留另一平台证据、清理失败与缺配置依赖分别判定

#### Scenario: 离线场景使用测试替身而非真实服务

- **WHEN** Change 06 离线场景执行
- **THEN** 报告记录 `mode=offline-contract`、真实调用次数为 0、测试替身调用次数大于 0
- **AND** 每个替身场景都断言请求契约、状态转换与失败分类
- **AND** 报告不包含真实密钥、真实服务地址或私有样本内容

#### Scenario: 缺配置按依赖分别判定

- **WHEN** 离线场景分别模拟只缺 Zep 配置、只缺模型配置与两者都缺
- **THEN** 缺失依赖对应的项为 `not_run`/`missing_config`，其它可独立运行的项仍按自己的契约执行
- **AND** 本地 OASIS 动作不因缺少模型或图谱密钥而被跳过
- **AND** 总结果为非零，不把部分独立项通过写成整体通过

#### Scenario: 模型 HTTP 错误分类保留精确状态码

- **WHEN** 离线场景分别模拟 HTTP 402、403 且带 Cloudflare 1010、普通 403 与 401
- **THEN** 原因码分别为 `billing_required`、`edge_blocked`、`auth_failed` 与 `auth_failed`
- **AND** 每项报告保留精确 `httpStatus` 与 `httpStatusClass`，实际调用次数为 1
- **AND** 普通 403 只有无关元数据含 1010 时仍为 `auth_failed`
- **AND** `providerErrorCode` 只包含允许列表内的错误码；假 Key 等未知回显值留空且不进入序列化报告
- **AND** 报告不包含原始响应正文或请求头全集

### Requirement: Change 06 离线入口保留未知变更与 SHA 规则

`make verify CHANGE=<未知值>` MUST 继续以退出码 2 结束，并说明“未注册的变更”。Change 06 报告 MUST 继续记录 `tested_sha` 等于执行时 `HEAD`、`dirty` 与逐场景结果；工作树存在未忽略改动时 MUST 沿用既有拒绝执行规则，除非显式允许并记录 `dirty: true`。

#### Scenario: 未注册变更仍明确失败

- **WHEN** 以未注册的 `CHANGE` 值执行 `make verify`
- **THEN** 命令以退出码 2 结束并在报告中说明“未注册的变更”
- **AND** 不把任何 Change 06 场景记为通过

#### Scenario: 报告继续绑定被测 SHA

- **WHEN** 在干净提交上执行 `make verify CHANGE=2026-11-21-connect-engine-providers`
- **THEN** 报告中的 `tested_sha` 等于执行时 `HEAD`，`dirty` 为 `false`
- **AND** 报告保留既有的逐场景结果、开始与结束时间、最终退出码
- **AND** 离线引擎证据与同一份报告一起写入 `runtime/verify/last-run.json`

### Requirement: 离线通过不得替代真实联调结论

`make verify` 与 Change 06 离线报告 MUST 明确其只证明适配层契约、状态机与失败分类，MUST NOT 声称模型、图谱或双平台已在真实服务上联通。真实联调结论 MUST 只来自同 SHA 的 `mode=live` `make smoke-live` 报告，并仍需人工签收；离线报告 MUST NOT 被写入 `docs/acceptance/` 或替代人工记录。

#### Scenario: 自动化报告不宣称真实联通

- **WHEN** Change 06 离线验收通过
- **THEN** 报告只声明离线契约与测试替身结果
- **AND** 不包含“模型 API 已调用成功”“Zep 已检索命中”“OASIS 双平台已运行”之类结论
- **AND** 真实调用计数为 0

#### Scenario: 真实联调结论等待 live 报告与人工签收

- **WHEN** 验收人准备签收真实联通
- **THEN** 必须引用同 SHA 的 `mode=live` 报告，其中模型、图谱、平台 A、平台 B、清理与依赖基线全部为 `passed`，且 `dirty=false`
- **AND** 人工验收记录写入 `docs/acceptance/`，由验收人签收
- **AND** `make verify`、CI 与 `make smoke-live` 均不创建或修改该人工记录

### Requirement: CI 同时保留默认验收与 Change 06 离线检查

CI SHALL 保留现有默认 `make verify` 工作，并增加独立的 Change 06 离线检查，调用 `make verify CHANGE=2026-11-21-connect-engine-providers`。两次检查 MUST 使用不同名称上传报告产物，并分别断言 `tested_sha` 与触发 SHA 一致、`dirty=false`、`result=passed`；Change 06 专属检查 MUST 额外断言 `calls.real=0`。CI MUST NOT 注入模型或 Zep 密钥、MUST NOT 执行 `make smoke-live`、MUST NOT 构建 live 引擎镜像。

#### Scenario: 默认与专属离线检查分别产出报告

- **WHEN** CI 在干净检出上运行
- **THEN** 默认 `make verify` 与 `make verify CHANGE=2026-11-21-connect-engine-providers` 分别执行
- **AND** 两份报告使用不同 artifact 名称上传，产物缺失即失败
- **AND** 两份报告都断言 `tested_sha`、`dirty=false`、`result=passed`

#### Scenario: 专属离线检查断言无真实调用

- **WHEN** 审查 Change 06 的 CI 报告
- **THEN** 报告中的 `calls.real` 为 0，`mode=offline-contract`，测试替身调用次数大于 0
- **AND** 工作流没有注入模型或 Zep 密钥
- **AND** 工作流没有执行 `make smoke-live`，也没有构建 `Dockerfile.engine` 或启用 live profile

### Requirement: Change 07 业务建图离线验收

`make verify CHANGE=2026-11-22-build-evidence-graph` SHALL 在默认验收场景之上追加业务建图专属场景，并 MUST 在无真实模型或 Zep 密钥、无外部网络、只使用原创小材料与测试替身的条件下完成。专属场景 MUST 覆盖：项目所有者用就绪材料发起建图、等待索引、增量绘图、完成、同快照计数、节点与边来源、PDF 页码与文本偏移、重复点击、项目图谱创建响应丢失、写入响应丢失且起初没有 episode UUID、worker 中断接手、索引超时与处理超时、外部依赖不可达、发布前崩溃、取消与完成竞争、规模上限、匿名 401、跨项目 404、空问题、未就绪材料与超过 5 份材料拒绝。

验收报告 MUST 在 `graph_evidence` 中记录 `buildId`、`jobId`、`runId`、观察到的阶段序列、快照版本序列、节点数、边数、来源校验结果、材料关系映射结果、接手前后 `attempts`、重复点击结果、失败原因码、测试替身调用次数与 `calls.real=0`。业务建图场景 MUST NOT 读取或写入验收专用图谱，MUST NOT 使用私有材料、私有样本文件名、外部品牌名、链接、代码、截图或回放数据。

默认 `make verify` MUST 保持既有场景与零真实外呼；Change 07 场景 MUST 可连续两次在同一 SHA 上通过，且 `tested_sha` 等于执行时 `HEAD`、`dirty=false`。CI MUST 复用同一 `make verify` 入口，为默认报告与 Change 07 报告使用不同 artifact 名称，并分别断言 `tested_sha`、`dirty=false`、`result=passed`；Change 07 专属检查额外断言 `graph_evidence.calls.real=0`。

#### Scenario: 默认验收不触发业务建图外呼

- **WHEN** 在没有真实密钥的环境执行默认 `make verify`
- **THEN** 既有场景全部通过，且不发起真实模型或 Zep 调用
- **AND** 报告不因新增建图能力改变既有 `/health`、`/livez`、会话、项目隔离、材料解析与测试任务语义

#### Scenario: Change 07 用替身完成全链路

- **WHEN** 在同一 SHA 上执行 `make verify CHANGE=2026-11-22-build-evidence-graph`
- **THEN** 报告 `graph_evidence` 记录等待索引、增量绘图与完成三个阶段及至少一次快照版本
- **AND** 节点数、边数与快照响应来自同一版本，至少一个 PDF 来源带物理页码、一个文本来源带原文偏移
- **AND** 测试替身调用次数大于 0，真实调用次数为 0
- **AND** 退出码为 0，`tested_sha` 等于 `HEAD`，`dirty=false`

#### Scenario: 项目图谱创建响应丢失

- **WHEN** 专属场景模拟 `graph.create` 已受理但客户端丢失响应，或响应已返回但数据库尚未保存绑定
- **THEN** 接手者使用同一项目绑定、同一幂等键与同一请求参数重试
- **AND** 最终只绑定一个项目图谱，不产生第二个图谱或第二个创建幂等键
- **AND** 报告记录创建尝试次数与最终绑定数

#### Scenario: 写入响应丢失且起初没有 episode UUID

- **WHEN** 专属场景模拟 episode 写入已受理但客户端没有拿到 UUID
- **THEN** 系统用本次 `buildId` 标记与内容哈希检索并恢复 UUID
- **AND** 不生成新幂等键，不重复写入
- **AND** 报告区分“最终确认”与“预算耗尽 `write_unconfirmed`”

#### Scenario: 重复点击与中断接手在报告中可核对

- **WHEN** 专属场景重复提交同一建图并停止、恢复 worker
- **THEN** 报告记录重复提交返回同一 `buildId`/`jobId`/`runId`
- **AND** 接手前后 `attempts` 递增，最终 episode 数、节点数、边数与未中断执行一致
- **AND** 不出现重复 episode、重复节点或重复事件

#### Scenario: 索引超时、处理超时与写入未知被分别覆盖

- **WHEN** 专属场景分别模拟已受理后未匹配、已匹配但无可用边、写入响应丢失且无法确认
- **THEN** 报告分别记录 `index_timeout`、`processing_timeout`/`graph_incomplete`、`write_unconfirmed`
- **AND** 三者不合并为同一个原因码，不把未完成计为通过

#### Scenario: 同批旧记录与正确记录

- **WHEN** 专属场景让检索同时返回旧 build 记录与本次正确记录
- **THEN** 只有本次 UUID、`buildId` 标记与内容哈希都匹配的记录进入快照
- **AND** 旧记录的节点、边与数量不计入本次结果

#### Scenario: 节点与边分页读回

- **WHEN** 专属场景构造超过一页的节点或边读回
- **THEN** 系统按游标读完所有页并按 `uuid_` 去重
- **AND** 报告中的节点数、边数与来源映射来自同一读回集合

#### Scenario: 发布前崩溃与取消竞争

- **WHEN** 专属场景在第 6 步准备候选快照后、发布事务提交前停止 worker，或让取消与发布竞争
- **THEN** 发布前崩溃不暴露最终快照，接手后可完成发布
- **AND** 取消先提交时只保留最近已发布部分快照，不产生完成事件
- **AND** 发布先提交时任务保持 `succeeded`，不追加取消终态
- **AND** 数据库中每种情况只有一条终态事件

#### Scenario: 规模上限与拒绝原因

- **WHEN** 专属场景提交 6 份材料，或构造超过 200 节点、400 边、每项 5 条来源的结果
- **THEN** 材料超限返回 422 `too_many_documents`
- **AND** 节点/边/来源超限以 `graph_too_large` 或 `too_many_sources` 失败
- **AND** 报告记录实际数量与上限，不发布截断后的完成快照

#### Scenario: 原文定位边界被覆盖

- **WHEN** 专属场景使用含 emoji、连续空格、CRLF 的原创文本，以及含重复页眉的多页原创 PDF
- **THEN** 文本来源按解码后完整原文的 UTF-16 偏移定位，PDF 来源按对应物理页的页内偏移定位
- **AND** 已入库材料无需重新解析即可定位
- **AND** 歧义引文以 `ambiguous_source` 失败，不取第一次匹配

#### Scenario: 第二次建图不串入第一次完成图

- **WHEN** 专属场景在同一项目连续开始两次建图，第二次仍在等待索引
- **THEN** 默认读取返回第二次 build，不显示第一次完成快照
- **AND** 第一次的迟到响应被 `buildId` 不匹配丢弃
- **AND** 显式按第一次 `buildId` 查询时仍可读取第一次完成图

#### Scenario: 空问题、未就绪材料与匿名/跨项目边界

- **WHEN** 专属场景提交空问题、选择未就绪材料、匿名读取或用户 B 读取用户 A 的 build
- **THEN** 分别得到 422、409、401 与 404 `项目不存在`
- **AND** 不新增 build、job、run、节点或边
- **AND** 响应不包含节点、边、来源、材料名称或建图元数据

#### Scenario: 未知 CHANGE 仍以退出码 2 失败

- **WHEN** 执行 `make verify CHANGE=not-a-change`
- **THEN** 命令以退出码 2 结束并写入“未注册的变更”
- **AND** 不执行任何业务建图或真实外部调用

#### Scenario: 两次连续运行结果一致

- **WHEN** 在同一 SHA 上连续两次执行 Change 07 验收
- **THEN** 两次都退出码 0，`tested_sha` 一致，`graph_evidence` 的阶段、来源与接手结果一致
- **AND** 第二次运行不需要人工清理上一轮生成的 build、快照或 worker 状态

### Requirement: 人工验收与私有样本边界

本变更的人工验收 SHALL 由项目作者在自己的浏览器中完成两轮：

1. 使用作者仅通过自己的私有项目上传的 25 页报告发起真实建图，观察等待索引、增量绘图与完成三种状态；完成后抽查 3 个材料节点和 3 条材料关系，确认详情能返回支持它们的原文位置。
2. 使用另一份作者原创的校园接驳车材料重新建图，核对实体类型、关系类型、节点或边确实随材料改变。

私有 25 页报告、正文、外部品牌名、链接、代码、截图与回放数据 MUST 留在仓库外；自动化、CI、测试夹具与报告 MUST NOT 引用其文件名、路径、正文或截图。仓库内只使用 EchoSim 自己的文案、代码、资产与运行时生成的原创小材料。人工验收记录只能在浏览器签收后写入 `docs/acceptance/`；验收前只准备待签收清单，不归档、不合并，也不把私有样本复制到 `runtime/`。

#### Scenario: 第一轮观察三种建图状态

- **WHEN** 项目作者用私有 25 页报告发起真实建图
- **THEN** 页面依次可观察等待索引、增量绘图与完成
- **AND** 完成时节点数、边数与画面来自同一最终已发布快照
- **AND** 截图中不包含凭据、完整私有正文或外部品牌信息

#### Scenario: 第一轮抽查 3 个材料节点和 3 条材料关系

- **WHEN** 作者抽查 3 个材料节点和 3 条材料关系并打开来源
- **THEN** 每条详情至少返回一条支持它的材料来源
- **AND** PDF 来源打开到对应物理页，文本来源高亮对应原文区间
- **AND** 来源位置与页面显示的引文一致

#### Scenario: 第二轮换材料后图谱随之改变

- **WHEN** 作者用另一份原创校园接驳车材料重新建图
- **THEN** 实体类型、关系类型、节点或边至少有一项随材料改变
- **AND** 新图谱不沿用上一份材料的节点、边或来源
- **AND** 新图谱仍可追溯到该原创材料中的原文位置

#### Scenario: 私有样本不进入仓库与自动化

- **WHEN** 审阅变更提交、测试夹具、验收报告与 `runtime/`
- **THEN** 不包含私有 25 页报告的文件名、路径、正文、外部品牌名、链接、代码、截图或回放数据
- **AND** 自动化只使用运行时生成的原创小材料与测试替身
- **AND** `docs/acceptance/` 只在人工签收后写入，自动化流程未创建或修改该目录

### Requirement: 私有样例 25 页报告真实全量建图验收

本变更的最终验收 SHALL 在现有私有样例项目（武大项目）工作台完成一次真实全量建图：使用与旧 build `f4c9a593-aa36-49e3-84cf-125ccaa88c21` 相同的已上传 25 页报告和同一个建图问题，新建 build，并完成新的本体规划、全部原文分块写入、索引、读回与发布。旧 build 的状态、快照、来源与 26 对段首文本节点 MUST 保持不变，不得覆盖或删除。

验收记录 MUST 包含本次实际数字：覆盖页数、写入原文块数、图谱服务读回节点数与边数、最终发布节点数与边数。若读回节点/边数明显高于最终发布数，验收 MUST 先定位损失发生在节点归并、边去重、支持句定位还是快照上限，并修复后再签收；MUST NOT 用截断、凑数或手工补图接近参考 demo。

新图 MUST 满足以下三点，缺一不可：

- 从报告中选出一件有明确原文依据的事，新图能看出涉及哪些主体、各自采取了什么行动，以及谁作出回应；每条关键关系都能点回支持该说法的原句。
- 同一主体出现在不同页、且材料能确认是同一对象时，用户能从一个节点看到它参与的不同关系，并分别看到各页来源。
- 图上保留材料原意：`media_report`、`party_claim`、`official_decision` 分别显示为媒体报道、当事方主张、机构正式决定；原文只说“某方称”时，不得显示为机构正式决定或已证实事实。

验收 MUST NOT 以节点数、边数、画布密度或截图观感作为通过条件。校园原创夹具只能用于全文重建、节点归并、读回事实定位与来源性质展示的回归，MUST NOT 替代武大新图的最终签收。

私有材料 MUST 沿用现有授权范围：不得新增外部模型/图谱服务，不得扩大报告分享范围。新 build MUST 使用现有授权范围内的真实模型/图谱 provider；MUST NOT 用测试替身图冒充武大新图。未获得将报告发送给外部服务的授权时，MUST NOT 发起新 build，验收记录 MUST 标记为阻塞；不得用离线夹具图冒充武大新图通过。日志、报告与验收记录 MUST NOT 包含报告正文、引文全文、账号密码、截图或完整 provider 响应；只记录 build 标识、问题标识、页码/段落、原因码、阶段计数与人工结论。

#### Scenario: 25 页报告生成新图并记录实际计数

- **WHEN** 项目作者在武大工作台用同一份 25 页报告、同一问题新建 build
- **THEN** 新 build 完成本体规划、全文写入、索引、读回与发布
- **AND** 验收记录包含覆盖页数、写入块数、读回节点/边数与最终节点/边数
- **AND** 旧 build 仍显示原有节点且未被改写

#### Scenario: 读回与展示差异先定位再交付

- **WHEN** 图谱服务读回很多节点/边，但工作台最终展示明显更少
- **THEN** 验收先查明损失环节并修复
- **AND** 修复后重新执行建图或重新发布快照
- **AND** 不以“图太大”或“只展示重点”作为未定位损失的理由

#### Scenario: 跨页主体在一个节点上可核对

- **WHEN** 同一主体在不同页出现，材料能确认是同一对象
- **THEN** 新图只有一个该主体节点
- **AND** 点选该节点可看到不同页各自支持的不同关系与来源
- **AND** 不需要在图上寻找两个分离的同名节点

#### Scenario: 媒体名单与建议撤销回归

- **WHEN** 报告只有媒体名单而没有报道动作，或只写报告作者建议撤销处分
- **THEN** 媒体名单不产生报道关系
- **AND** 建议撤销不显示成已经撤销的正式决定
- **AND** 两条回归不影响其他有效关系

#### Scenario: 未授权外发时验收阻塞

- **WHEN** 现有授权不包含把该报告发送给外部模型或图谱服务
- **THEN** 不发起新 build，不把报告内容发送到任何外部服务
- **AND** 验收记录标记为阻塞，不能以校园夹具或旧图代替武大新图签收

#### Scenario: 默认命令仍零真实外呼

- **WHEN** 执行默认 `make up`、默认 `make verify` 或 CI
- **THEN** 不调用真实模型或图谱服务
- **AND** 离线回归使用现有替身，不把替身结果写入武大新图

### Requirement: 复用真实图谱生成语义修订版本

语义修正验收 MUST 复用基线 build `a788aeb9-f557-468a-bba5-9404d7e88a7e` 的 47 个已确认 episode 与真实图谱读回，按新 build 的 episode 范围重新生成快照；MUST NOT 重新上传报告、重新规划本体或重新写入 episode。基线 build MUST 保持不变，修订版本 MUST 可独立打开对照。

验收 MUST 记录修订前后节点/边数，并在工作台实际显示以下三处文字：

- 第 4 页平台行动显示“女性用户在小红书上维权”，不显示“小红书向女性用户维权”；
- 第 10 页显示“部分账号传播不实信息”及“某事件（报告所称，未证实）”，不把被转述说法画成已发生事件；
- 第 22 页匿名评审显示“报告建议”，不显示“机构正式决定”。

修订 MUST 应用到同类关系，不得按页码、学校名或平台名硬编码。目标是有原文根据的关系方向、限定和性质正确，不得通过批量删除关系或删除端点来通过三个样例。

#### Scenario: 修订版本与基线并存

- **WHEN** 使用基线 episode 与真实图谱生成修订 build
- **THEN** 修订 build 达到 ready，快照完整发布
- **AND** 基线 build 的节点/边数与快照不变
- **AND** 两个 build 可通过各自链接打开对照

#### Scenario: 三处文字在工作台可核对

- **WHEN** 打开修订 build 并查看第 4、10、22 页对应关系
- **THEN** 画布实际显示场景要求的文字
- **AND** 关系详情与画布一致
- **AND** 节点/边数记录可用于判断是否通过删边达成
