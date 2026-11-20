# Spec Delta

## Purpose

用一条可重复执行的验收命令，把“实际被测代码”与干净克隆、CI 结果和人工验收绑定在同一证据链上，避免环境差异或 SHA 漂移造成不可信的结论。

## ADDED Requirements

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

一次成功的验收 SHALL 实际验证启动、健康检查故障与恢复、以及后端进程不重启等场景，而不是只做静态检查。

#### Scenario: 报告包含全部验收场景

- **WHEN** `make verify` 成功结束
- **THEN** 报告逐项记录并通过：首页可访问并包含 EchoSim、`/health` 返回 200、停止数据库后返回 503、恢复数据库后返回 200、后端进程标识在故障前后不变、连续执行可重复
- **AND** 停止数据库到返回 503 的实测耗时被记录且不超过 5 秒

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
