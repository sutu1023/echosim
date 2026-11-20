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
