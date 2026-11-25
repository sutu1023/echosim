# dual-platform-simulation Specification

## Purpose
让 OASIS 的两个本地模拟平台各自建立最小环境，分别执行原生发帖与原生评论动作，并从各自引擎记录中读回平台、角色、原生动作、目标帖与结果，用两份独立证据证明双平台联通。

## Requirements

### Requirement: 两个平台使用固定原生映射与独立最小环境

系统 SHALL 使用稳定别名 `platform_a` 与 `platform_b` 报告两个平台，其中 `platform_a` MUST 对应原生 `twitter`，`platform_b` MUST 对应原生 `reddit`。系统 MUST 为两个平台分别创建独立的最小仿真环境，每个环境 MUST 只包含本次验收所需的角色、场景与运行标识，且 MUST NOT 与另一个平台共享可变数据库、进程、临时目录或运行状态。报告 MUST 同时记录稳定别名、原生平台键与运行标识，并断言两个原生平台键分别是 `twitter` 与 `reddit`。平台 A 的环境创建失败 MUST NOT 阻止平台 B 继续执行；共享运行时的导入失败、依赖缺失或配置缺失 MUST 单独归因为 `shared_setup`。

#### Scenario: 两个平台分别建立环境并映射到原生平台

- **WHEN** 双平台项开始执行
- **THEN** 平台 A 与平台 B 各自获得独立的运行标识、临时目录与引擎连接
- **AND** 报告记录 `platform_a` 的 `nativePlatform=twitter` 与 `platform_b` 的 `nativePlatform=reddit`
- **AND** 两个环境的角色与场景只包含本次验收夹具，不读取项目材料

#### Scenario: 平台 A 环境失败不阻断平台 B

- **WHEN** 平台 A 的环境创建失败
- **THEN** 平台 A 标记为失败，原因码为 `platform_a_setup`
- **AND** 平台 B 仍继续创建环境、建立目标帖、执行评论动作并读回记录
- **AND** 双平台总判定为失败，但报告保留平台 B 的独立证据

#### Scenario: 共享依赖失败单独归因

- **WHEN** 两个平台共用的引擎包无法导入或运行所需依赖缺失
- **THEN** 两个平台都标记为未执行或失败，原因码为 `shared_setup`
- **AND** 不把共享依赖问题伪装成平台 A 或平台 B 的独立动作失败

### Requirement: 平台 A 执行原生 create_post

平台 A SHALL 在本地 `twitter` 模拟环境中由指定角色执行一次原生 `create_post` 动作，内容 MUST 包含本次运行标识，MUST NOT 使用空内容、纯占位符或与平台规则不符的动作类型。平台 A 的动作尝试 MUST 有总时限，默认不超过 60 秒。

#### Scenario: 平台 A 执行原生发帖

- **WHEN** 平台 A 的最小环境准备完成
- **THEN** 指定角色执行 `create_post`，内容包含本次运行标识
- **AND** 报告记录 `platform_a`、`nativePlatform=twitter`、`nativeAction=create_post`、角色、运行标识与动作耗时

#### Scenario: 平台 A 动作被平台规则拒绝

- **WHEN** `twitter` 本地平台拒绝 `create_post` 或返回校验错误
- **THEN** 平台 A 标记为失败，原因码为 `platform_a_action`
- **AND** 平台 B 的动作与判定不受影响
- **AND** 不把被拒绝的动作计为有效行动记录

### Requirement: 平台 B 先建立目标帖再执行原生 create_comment

平台 B SHALL 在本地 `reddit` 模拟环境中先建立本次夹具专用的目标帖，再由指定角色对该目标帖执行一次原生 `create_comment`。目标帖 MUST 包含本次运行标识，并 MUST 记录其帖子标识；目标帖的建立属于平台 B 的环境准备，MUST NOT 替代 `create_comment` 作为平台 B 的证据动作。平台 B 的动作尝试 MUST 有总时限，默认不超过 60 秒。

#### Scenario: 平台 B 建立目标帖并执行原生评论

- **WHEN** 平台 B 的最小环境准备完成
- **THEN** 夹具建立一条包含本次运行标识的目标帖，并返回目标帖标识
- **AND** 指定角色对该目标帖执行 `create_comment`，评论内容包含本次运行标识
- **AND** 报告记录 `platform_b`、`nativePlatform=reddit`、`nativeAction=create_comment`、角色、`targetPostId`、运行标识与动作耗时

#### Scenario: 目标帖建立失败

- **WHEN** 平台 B 无法建立本次夹具的目标帖
- **THEN** 平台 B 标记为失败，原因码为 `platform_b_setup`
- **AND** 不执行 `create_comment`，不把缺少目标帖的评论计为通过
- **AND** 平台 A 的动作与判定不受影响

#### Scenario: 平台 B 动作被平台规则拒绝

- **WHEN** `reddit` 本地平台拒绝 `create_comment` 或返回校验错误
- **THEN** 平台 B 标记为失败，原因码为 `platform_b_action`
- **AND** 平台 A 的动作与判定不受影响
- **AND** 不把被拒绝的动作计为有效行动记录

### Requirement: 动作结果必须从引擎记录读回

每个平台的动作结果 SHALL 通过独立的读回步骤从该平台引擎记录中获得，MUST NOT 只使用动作调用返回值或进程内对象作为证据。读回记录 MUST 至少包含平台标识、原生平台键、角色、原生动作、运行标识与执行结果；平台 B 的记录 MUST 还包含目标帖标识。平台通过条件 MUST 同时满足：记录存在、稳定别名与原生平台键一致、角色与配置一致、原生动作与预期一致、记录内容包含本次运行标识、结果表示成功；平台 B 还必须满足评论记录对应的目标帖就是本次夹具建立的目标帖。

#### Scenario: 平台 A 从引擎记录读回行动证据

- **WHEN** 平台 A 的 `create_post` 执行完成
- **THEN** 读回步骤从 `twitter` 引擎记录中找到对应条目
- **AND** 记录中的稳定别名、原生平台键、角色、原生动作、运行标识与结果全部匹配
- **AND** 平台 A 判定为通过，报告记录该记录标识

#### Scenario: 平台 B 从引擎记录读回并对应目标帖

- **WHEN** 平台 B 的 `create_comment` 执行完成
- **THEN** 读回步骤从 `reddit` 引擎记录中找到对应评论条目
- **AND** 记录中的稳定别名、原生平台键、角色、原生动作、运行标识与结果全部匹配
- **AND** 评论记录对应的目标帖标识等于本次夹具建立的目标帖标识
- **AND** 平台 B 判定为通过，报告记录评论记录标识与 `targetPostId`

#### Scenario: 只有动作返回值没有引擎记录

- **WHEN** 动作调用返回成功，但读回步骤找不到匹配的引擎记录或字段不一致
- **THEN** 该平台判定为失败，原因码为 `platform_a_record` 或 `platform_b_record`
- **AND** 不把动作调用返回值当作实际行动记录
- **AND** 另一个平台仍按自己的记录独立判定

#### Scenario: 评论记录指向其它目标帖

- **WHEN** 平台 B 的评论记录存在，但目标帖标识不等于本次夹具建立的目标帖
- **THEN** 平台 B 判定为失败，原因码为 `platform_b_record`
- **AND** 报告保留实际目标帖标识与期望目标帖标识，不把该评论计为本次证据

### Requirement: 动作超时先核对引擎记录再决定是否重试

动作调用超时、连接中断或返回不确定状态时，系统 MUST 先读回该平台的引擎记录，确认动作是否已经生效。若记录已存在且匹配，MUST 判定为通过且 MUST NOT 重发动作；若记录明确不存在且预算仍允许，MAY 最多重试 1 次；若无法确认是否生效，MUST 判定为 `action_unconfirmed`，不得盲目重试造成重复动作。平台 B 的目标帖在超时核对时 MUST 一并核对，避免把旧评论或其它帖子评论计为本次证据。

#### Scenario: 超时但动作已经写入引擎

- **WHEN** 动作调用超时，随后读回发现匹配的引擎记录
- **THEN** 该平台判定为通过，报告记录 `actionTimeout=true` 与 `recordAfterTimeout=true`
- **AND** 不再次发送动作，记录中该运行标识对应的动作数量为 1
- **AND** 平台 B 还记录匹配的 `targetPostId`

#### Scenario: 超时且确认动作未生效

- **WHEN** 动作调用超时，读回明确未找到该运行标识，且该平台预算仍允许
- **THEN** 最多重试动作 1 次，并再次读回记录
- **AND** 若仍无记录，平台判定为失败，原因码为 `platform_a_action` 或 `platform_b_action`
- **AND** 报告记录动作尝试次数；平台 B 不得在缺少目标帖时重试评论

### Requirement: 双平台判定分别成立且资源必须关闭

双平台总判定 SHALL 仅在平台 A 与平台 B 都通过时成立；任一平台失败时 MUST 判定双平台失败并列出失败平台，MUST NOT 用另一个平台的通过抵消。每个平台的环境 MUST 在 `finally` 等价的清理路径中关闭：停止本地进程或服务、关闭数据库与连接、删除本次运行的临时目录，并记录清理结果。清理失败 MUST 被记录为运行失败，不得静默忽略。

#### Scenario: 两个平台都通过

- **WHEN** 平台 A 与平台 B 都从各自引擎记录读回匹配的行动证据
- **THEN** 双平台项判定为通过
- **AND** 报告记录两个平台的稳定别名、原生平台、角色、原生动作、结果与记录标识
- **AND** 平台 B 的报告还包含本次目标帖标识

#### Scenario: 只有一个平台通过

- **WHEN** 平台 A 通过而平台 B 失败，或反之
- **THEN** 双平台项判定为失败，并记录 `failedPlatforms`
- **AND** 通过平台的证据仍保留在报告中，不被删除或改写为失败
- **AND** 命令以非零状态结束

#### Scenario: 清理失败不被忽略

- **WHEN** 动作与读回均成功，但某个平台的进程、连接或临时目录未能关闭
- **THEN** 报告记录该平台的 `cleanup_failed` 与清理耗时
- **AND** 双平台运行判定为失败，不得把残留资源当作已关闭
- **AND** 清理路径对另一个平台仍然执行

### Requirement: 运行证据与连通性探针分开且共用读回纪律

`make smoke-live` 的双平台探针（`simulation.platform_a` 的 `create_post`、`simulation.platform_b` 的目标帖加 `create_comment`）MUST 保持原有语义与判定条件，MUST NOT 因新增双平台运行而改变动作类型、目标帖要求、理由码或通过条件。

双平台运行 MUST 独立于该探针：运行动作、轮次与计数 MUST NOT 写入探针报告项，探针报告 MUST NOT 被当作运行结果或产品数据；反之，产物运行（含离线替身运行）MUST NOT 被写成真实联通签收证据。两者共同遵守“从引擎记录读回、动作调用返回值不构成证据”的纪律，并共用稳定别名与原生平台映射 `platform_a/twitter` 与 `platform_b/reddit`。

#### Scenario: 探针保持原判定

- **WHEN** 执行真实联调的双平台探针
- **THEN** 仍按 `create_post` 与目标帖加 `create_comment` 的动作从各自引擎记录读回
- **AND** 目标帖标识、动作结果与清理结果仍分别判定
- **AND** 探针结论不因运行能力上线而放宽

#### Scenario: 运行结果不冒充联通签收

- **WHEN** 双平台运行在离线替身模式下完成
- **THEN** 该结果不出现在真实联调报告的双平台项中
- **AND** 不被描述为已验证真实本地 OASIS 联通

#### Scenario: 别名与原生映射一致

- **WHEN** 核对探针报告与运行记录的平台标识
- **THEN** 两者都使用 `platform_a/twitter` 与 `platform_b/reddit`
- **AND** 运行使用的平台映射与冻结选择中的映射一致
