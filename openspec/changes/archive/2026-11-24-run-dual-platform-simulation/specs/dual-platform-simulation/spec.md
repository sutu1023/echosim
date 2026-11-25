# Spec Delta

## ADDED Requirements

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
