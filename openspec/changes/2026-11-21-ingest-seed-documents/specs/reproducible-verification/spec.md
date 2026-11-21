# Spec Delta

## ADDED Requirements

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
