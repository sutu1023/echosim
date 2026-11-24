# Spec Delta

## ADDED Requirements

### Requirement: Change 08 角色与环境准备离线验收

`make verify CHANGE=2026-11-24-prepare-agent-environment` SHALL 在默认验收场景之上追加角色与环境准备场景，并在同一被测 SHA 上写入 `environment_evidence`。专属场景 MUST 使用运行时生成的原创微型材料、真实 PostgreSQL、真实 worker、真实租约与事件流，只用测试替身替换模型调用；MUST NOT 读取私有材料、私有样本或真实模型密钥。

报告 MUST 记录 `preparationId`、`buildId`、`snapshotId`、`question`、角色数量与类型、中文/英文快照类型筛选结果、来源定位、材料提及/背景/表达话题/关联话题/模拟设定的分离结果、旧 `claimNature` 为空时的未知结果、分批预览与批次标识、轮数模式与生效轮数、确认时的 `settingsRevision`、冻结选择标识、首发角色与话题对应关系、普通开始与显式重试结果、接管结果、失败原因码，以及 `calls.real=0` 与大于 0 的替身调用计数。未知 `CHANGE` MUST 仍以退出码 2 结束；同一环境连续两次执行 MUST 都通过，且报告 `tested_sha` 等于执行时的 `HEAD`。

#### Scenario: 默认验收不触发准备真实外呼

- **WHEN** 运行默认 `make verify`
- **THEN** 既有健康、会话、项目隔离、材料、任务、图谱与测试任务场景全部保留
- **AND** 不发起角色画像真实模型调用，准备真实调用计数为 0
- **AND** 报告不把准备离线通过写成真实模型已联通

#### Scenario: Change 08 用原创材料完成全链路

- **WHEN** 运行 Change 08 专属验收
- **THEN** 上传原创小材料并完成建图，得到一个可继续的最终快照
- **AND** 开始准备返回 202，worker 依次提交 `bind`、`select`、`profile`、`compose`、`publish`
- **AND** 准备进入 `ready`，准备详情返回角色、来源、平台映射、话题、首发话题、激活顺序与 72 轮
- **AND** 真实模型替身收到本次绑定的 `question`
- **AND** 报告记录 `calls.real=0` 与替身调用次数大于 0

#### Scenario: 中文快照筛选与三个反例被断言

- **WHEN** 使用 `summary=null`、中文显示类型的原创快照，包含个人、群体、只出现在终点的机构、媒体机构、有出边的事件、待核实说法与平台渠道
- **THEN** 个人、群体、机构与媒体机构进入角色
- **AND** 只出现在终点的机构仍入选，有出边的事件与待核实说法不入选
- **AND** 媒体机构与平台渠道被分开，平台渠道不进入角色
- **AND** 报告记录未重新建图且 `summary=null` 不影响筛选

#### Scenario: 材料实际支持什么被分别断言

- **WHEN** 读取角色详情
- **THEN** 节点引文只显示为材料提及
- **AND** 有归属支持原句的话题显示为角色表达/主张，只有关联的显示为“材料涉及的话题”
- **AND** 无背景或关注证据时显示“材料未说明”
- **AND** 旧边 `claimNature` 为空时保留未知，不默认成其他材料陈述
- **AND** 模型补充的关注点标记为模拟设定

#### Scenario: 第一批画像可预览且第三阶段不可用

- **WHEN** 专属验收构造至少两批角色，第一批已通过来源校验，第二批仍在生成
- **THEN** 准备详情返回第一批 `previewRoles`，页面可展开并显示“准备中”
- **AND** 事件只包含批次标识、角色标识与计数，不含角色正文
- **AND** 确认接口返回 `preparation_not_ready` 或等价未就绪，第三阶段入口不可用
- **AND** 刷新后仍恢复第一批预览；worker 接手后不重复生成该批

#### Scenario: 预览失败或取消后的页面行为

- **WHEN** 准备在第二批生成时失败或被取消
- **THEN** 页面显示失败/取消原因与显式重试入口
- **AND** 第一批预览只读显示为“未完成预览”，`canContinue=false`
- **AND** 不显示平台、话题、顺序或冻结选择
- **AND** 重试返回 202 并创建新的准备、job 与 run，旧准备保持终态

#### Scenario: 普通开始、显式重试与接手被断言

- **WHEN** 专属验收对同一 build 连续普通开始，并在失败/取消后重试，再让 worker 中断接手
- **THEN** `preparing`/`ready` 普通开始返回 200 与同一身份
- **AND** 失败/取消后普通开始返回 `preparation_retry_required`，显式重试返回 202 与新身份
- **AND** 接手保持 `preparationId`、`jobId`、`runId` 不变，`attempts>1`
- **AND** 数据库不产生重复准备或重复角色

#### Scenario: 两页并发确认被断言

- **WHEN** A 读到 revision 2 的 40 轮，B 保存 3 轮形成 revision 3，A 用 revision 2 确认，随后 A 重新读取并用 revision 3 确认
- **THEN** 第一次确认返回 `settings_conflict`，数据库没有冻结记录
- **AND** 重新读取后确认成功并冻结 3 轮
- **AND** 相同 revision 3 重复确认返回同一 `selectionId`
- **AND** 报告记录冲突前后 `settingsRevision`、轮数与冻结记录数

#### Scenario: 轮数计算、保存与读回被断言

- **WHEN** 首次读取准备配置，随后保存手动 40 轮，再次读取
- **THEN** 首次返回 72 小时、60 分钟与 72 轮
- **AND** 第二次返回手动 40 轮，刷新后仍为 40
- **AND** 手动 3 轮被接受
- **AND** 报告记录自动轮数、手动轮数、生效轮数与 `settingsRevision`

#### Scenario: 冻结选择内容被断言

- **WHEN** 用户确认一个已就绪准备
- **THEN** 冻结选择包含 `question`、`buildId`、`snapshotId`、角色、轮数、平台映射、初始话题的 `origin`/`claimNature`、首发角色与话题对应关系
- **AND** `platform_a/twitter` 与 `platform_b/reddit` 保留为映射
- **AND** `create_post`/`create_comment` 未被写成两个平台唯一允许的动作
- **AND** 重复确认返回同一 `selectionId`

#### Scenario: 无角色与无效配置被分别拒绝

- **WHEN** 使用只有事件、说法或无来源节点的快照开始准备，或提交零时长、非法轮数、不能整除的自动轮数
- **THEN** 无角色返回 `no_role_candidates`，不创建准备、job 或 run
- **AND** 零时长返回 `duration_invalid`，非法轮数返回 `rounds_invalid`，不能整除返回 `rounds_not_integral`
- **AND** 页面显示具体原因，`canContinue=false`，确认与进入第三阶段均不可用

#### Scenario: 匿名、他人项目与资源不存在被一致拒绝

- **WHEN** 匿名访问准备接口，或使用他人项目/他人 build/不存在准备标识
- **THEN** 匿名返回 401 且不访问准备数据
- **AND** 他人与不存在资源分别返回一致的 404 `项目不存在`、`图谱不存在`、`准备不存在`
- **AND** 响应不包含角色、来源、材料正文或内部路径

#### Scenario: 模型不可用进入可恢复失败

- **WHEN** 测试替身被显式关闭且真实模型配置缺失
- **THEN** 准备以 `model_unavailable` 失败，不发布最终角色或冻结选择
- **AND** 页面显示失败原因与显式重试入口
- **AND** 重试创建新的准备与 run，旧失败记录仍可诊断

#### Scenario: 换一份原创材料后角色变化

- **WHEN** 同一项目换一份原创材料完成新 build 并准备角色
- **THEN** 新准备的角色名称、类型或数量与第一份材料不同
- **AND** 旧准备的角色不被复用，冻结选择仍引用旧 `snapshotId`

#### Scenario: 未知变更仍以退出码 2 失败

- **WHEN** 使用未注册的 `CHANGE` 运行验收
- **THEN** 脚本以退出码 2 结束并列出可用变更
- **AND** 不写入可通过的验收报告

#### Scenario: 私有样例只用于人工走查

- **WHEN** 作者在私有项目中用私有材料走查角色详情、来源、轮数与确认
- **THEN** 私有 PDF、含正文截图、真实画像与模型原始输出留在仓库外
- **AND** `docs/acceptance/` 只记录脱敏结论、SHA、任务标识与必要证据链接
- **AND** 自动化与 CI 不引用该材料、文件名、路径、正文、截图或角色结果
- **AND** 自动化只使用运行时生成的原创微型材料
