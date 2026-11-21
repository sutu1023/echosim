# Spec Delta

## Purpose

让一次联通验收能够把一条含唯一事实的 episode 写入固定验收专用图谱，并在有截止时间的等待后用 `graph.search_episodes` 证明“检索到的正是本次写入的事实”，而不是只证明写入接口返回了成功。

## ADDED Requirements

### Requirement: 固定验收专用图谱与每次运行唯一标识

系统 SHALL 从服务端配置读取 Zep 密钥、验收专用图谱标识与可选服务地址，其中 `ZEP_ACCEPTANCE_GRAPH_ID` MUST 对应图谱的 `graph_uuid`。系统 MUST 使用预先配置的固定验收专用图谱，MUST NOT 写入任何用户项目图谱，也 MUST NOT 在每次运行时动态创建新的验收图谱。每次运行 MUST 生成不同的 `runId`，并把该标识写入 episode 事实、幂等键与检索查询。缺少图谱密钥或图谱标识时 MUST 把图谱项标记为未执行且不发起写入。

#### Scenario: 缺少图谱配置时不写入

- **WHEN** Zep 密钥或 `ZEP_ACCEPTANCE_GRAPH_ID` 至少一项缺失
- **THEN** 图谱项标记为 `not_run`，原因码为 `missing_config`
- **AND** 对图谱服务的真实写入与检索调用次数均为 0
- **AND** 命令以非零状态结束

#### Scenario: 每次运行使用固定图谱与不同运行标识

- **WHEN** 图谱项开始执行
- **THEN** 写入目标是配置中的同一个 `graph_uuid`
- **AND** 本次运行的 `runId` 与其它运行不同，并出现在 episode 事实与检索查询中
- **AND** 不创建新的验收图谱，也不写入用户图谱

### Requirement: 使用 graph.episode.add 与唯一 idempotency_key 写入

系统 SHALL 使用 `zep-cloud==4.1.0` 的 `graph.episode.add` 向配置的 `graph_uuid` 写入一条 episode。每次新的逻辑写入 MUST 在重试循环前生成一次 `str(uuid.uuid4())` 作为唯一 `idempotency_key`；同一次逻辑写入的所有重试 MUST 复用原键、原图谱与原请求体，MUST NOT 因超时、连接中断或搜索未命中而换键或改写请求体。写入成功后 MUST 保存服务端返回的 episode UUID。

#### Scenario: 写入成功后保存 episode UUID

- **WHEN** `graph.episode.add` 接受写入并返回 episode UUID
- **THEN** 报告记录 `accepted=true`、`episodeId`、`idempotencyKey`、受理时间与写入耗时
- **AND** 图谱状态从 `submitting` 进入 `accepted`
- **AND** 本次运行只写入一条验收 episode

#### Scenario: 幂等键是 UUIDv4 且在重试中保持不变

- **WHEN** 第一次写入超时或返回 5xx，系统在预算内再次尝试同一逻辑写入
- **THEN** 第一次请求的 `idempotency_key` 是可解析的 UUIDv4
- **AND** 第二次请求使用与第一次相同的 `idempotency_key`、同一 `graph_uuid` 与同一请求体
- **AND** 报告记录两次尝试共用同一个幂等键
- **AND** 不生成第二个幂等键，不写入第二个逻辑事实

#### Scenario: 两次独立运行使用不同幂等键

- **WHEN** 同一个运行标识执行两次独立的逻辑写入
- **THEN** 两次写入各自在重试循环前生成不同的 UUIDv4 幂等键
- **AND** 两次写入不会因为键相同而被服务端视为同一次逻辑写入

#### Scenario: 写入返回明确错误状态

- **WHEN** 图谱服务返回 400、401、403、422 或其它明确错误
- **THEN** 报告按错误类别记录 `invalid_request`、`auth_failed` 或 `write_failed`
- **AND** 不进入索引等待，不把图谱项标记为通过

### Requirement: 写入响应丢失时先核对且不得以搜索未命中换键

写入发生超时、连接中断或 5xx 时，系统 MUST NOT 换用新的幂等键或改写请求体重写；MUST 使用原 `idempotency_key`、原 `graph_uuid` 与原请求体保留同一逻辑写入。系统 MUST 用 `graph.search_episodes` 在同一图谱核对本次 `runId` 与唯一事实。搜索未命中 MUST NOT 被当作“肯定未写入”的证据，也 MUST NOT 成为换键重写的依据；预算内仍无法确认时 MUST 判定为 `write_unconfirmed`。写入与核对的总时限 MUST NOT 超过 60 秒。

#### Scenario: 服务端已受理、响应丢失、首次搜索未命中

- **WHEN** 服务端实际已受理写入，但客户端丢失响应，随后第一次 `graph.search_episodes` 尚未返回本次 episode
- **THEN** 图谱状态保持 `accepted` 或 `indexing`，报告记录 `writeResponseLost=true` 与 `firstSearchMiss=true`
- **AND** 不换幂等键、不改变请求体、不写入新的逻辑事实
- **AND** 在截止时间内继续用同一 `runId` 搜索，直到本次 episode 精确命中或判定 `write_unconfirmed`

#### Scenario: 响应丢失后同一幂等键重试并最终命中

- **WHEN** 响应丢失后第一次搜索未命中，预算仍允许，系统使用原幂等键与原请求体重试
- **THEN** 重试后通过 `graph.search_episodes` 找到本次 episode UUID、`runId` 与唯一事实
- **AND** 图谱项判定为通过，报告记录重试次数与同一个幂等键
- **AND** 固定图谱中该 `runId` 对应的逻辑事实只有一条

#### Scenario: 预算耗尽仍无法确认

- **WHEN** 响应丢失后多次搜索均未命中，且写入与核对预算耗尽
- **THEN** 图谱项判定为失败，原因码为 `write_unconfirmed`
- **AND** 报告记录写入尝试次数、同一幂等键与搜索次数
- **AND** 不把未确认的写入计为已受理，也不改写为成功

### Requirement: 使用 graph.search_episodes 精确核对当前记录

系统 SHALL 使用 `graph.search_episodes` 在同一个 `graph_uuid` 内检索，并同时核对 episode UUID、`runId` 与唯一事实。固定验收图谱中可能同时存在旧记录；只要本次 episode 精确命中即可通过，MUST NOT 因旧记录存在而误判失败。只有旧记录而没有本次 episode 时 MUST 继续等待或按 `index_timeout` 失败，MUST NOT 用旧记录冒充本次结果。

#### Scenario: 旧记录与本次记录同时返回

- **WHEN** `graph.search_episodes` 返回多条记录，其中包含旧运行记录与本次 episode
- **THEN** 只要本次 episode UUID、`runId` 与唯一事实同时匹配，图谱项判定为通过
- **AND** 报告记录命中本次 episode，不把旧记录计为本次证据

#### Scenario: 只返回旧记录

- **WHEN** 检索只返回旧运行记录，没有本次 episode UUID
- **THEN** 图谱状态保持 `indexing`，不把旧记录计为命中
- **AND** 在截止时间内继续检索，到达上限后按 `index_timeout` 判定失败

#### Scenario: 运行标识出现在错误 UUID 上

- **WHEN** 检索返回的记录包含本次 `runId` 与唯一事实，但 episode UUID 与写入返回的 UUID 不一致，且整批候选中没有精确命中
- **THEN** 图谱项判定为失败，原因码为 `stale_or_foreign_hit`
- **AND** 报告记录期望 UUID 与实际 UUID，不记录完整 episode 正文

#### Scenario: 错误 UUID 在前、精确命中在后

- **WHEN** 本次检索同时返回一条错误 UUID 的候选和一条 UUID、运行标识与唯一事实都匹配的候选
- **THEN** 系统必须扫描完整批候选，后面的精确命中仍然通过
- **AND** 前面的错误候选不得提前触发 `stale_or_foreign_hit`

#### Scenario: 元数据标记不能单独构成命中

- **WHEN** 检索候选项只有元数据标记，正文不含本次运行标识或唯一事实
- **THEN** 该候选不得判定为命中，元数据只能用于筛选候选
- **AND** 若没有其它精确命中，按索引等待规则继续等待或超时失败

#### Scenario: 正文有标记但事实错误

- **WHEN** 检索候选项的正文包含本次运行标识，但不包含约定的唯一事实
- **THEN** 该候选不得判定为命中
- **AND** 只有 UUID、正文运行标识与唯一事实同时匹配的候选才能通过

### Requirement: 索引等待区分已受理、仍在索引与可以检索

系统 SHALL 在写入受理后以固定间隔使用 `graph.search_episodes` 检索，直到本次 episode 精确命中或达到索引截止时间。报告 MUST 区分三种状态：`accepted` 表示写入已受理但尚未检索；`indexing` 表示已受理且至少一次检索未命中；`searchable` 表示检索命中且命中项与本次写入的 episode UUID 和 `runId` 同时一致。默认索引等待上限为 60 秒，到达上限仍未命中 MUST 判定为 `index_timeout` 或 `write_unconfirmed`，不得无限等待。

#### Scenario: 首次检索未命中时报告仍在索引

- **WHEN** 写入已受理，第一次检索没有返回本次 episode
- **THEN** 图谱状态记为 `indexing`，报告记录已等待时间与检索次数
- **AND** 继续在截止时间内按固定间隔检索，不重新写入

#### Scenario: 截止时间后命中并报告可以检索

- **WHEN** 某次检索返回的 episode UUID 与本次写入一致，且内容包含本次运行标识与唯一事实
- **THEN** 图谱状态记为 `searchable`，图谱项判定为通过
- **AND** 报告记录从受理到命中的等待时间、检索次数与命中 UUID

#### Scenario: 索引等待超时

- **WHEN** 到达索引截止时间仍没有返回本次 episode
- **THEN** 图谱状态保持 `indexing`，图谱项判定为失败，原因码为 `index_timeout`
- **AND** 报告记录已等待时间、检索次数与最后一次检索结果类别
- **AND** 不通过换键重写或换用模糊查询来制造命中

### Requirement: 图谱失败有独立分类与有限重试

图谱调用 SHALL 对认证失败、权限不足、非法请求、限流、服务端错误、写入超时、响应丢失、索引超时与检索失败给出彼此可区分的原因码。401 与 403 MUST NOT 重试；429 与 5xx MAY 在总时限内使用同一 `idempotency_key`、同一 `graph_uuid` 与同一请求体重试 1 次；检索阶段的 429 与 5xx 只允许在索引截止时间内继续轮询，不得延长截止时间或触发新的写入。SDK 内置重试也 MUST 计入实际调用次数。所有图谱调用 MUST 在结束时关闭客户端连接。

#### Scenario: 认证失败立即停止

- **WHEN** 写入或检索返回 401 或 403
- **THEN** 图谱项立即判定为失败，原因码为 `auth_failed`
- **AND** 不发起重试，不继续索引等待
- **AND** 报告不包含密钥或 Authorization 头

#### Scenario: 检索阶段限流不延长索引截止时间

- **WHEN** 索引等待期间检索连续返回 429 或 5xx
- **THEN** 系统在剩余截止时间内继续轮询，但不延长原定截止时间
- **AND** 若仍未命中，按 `index_timeout` 或 `search_failed` 判定失败
- **AND** 不因检索失败而重新写入 episode 或更换幂等键

### Requirement: 离线回归覆盖响应丢失与首次搜索未命中

离线契约检查 SHALL 包含“服务端已受理、响应丢失、首次 `graph.search_episodes` 未命中”的回归：测试替身记录服务端已接收写入，但客户端第一次读取响应失败，第一次搜索只返回空或旧记录。该场景 MUST 断言系统保持原 `idempotency_key`、原 `graph_uuid` 与原请求体，不换键重写；后续搜索精确命中时通过，预算耗尽时 `write_unconfirmed`。

#### Scenario: 离线替身模拟响应丢失

- **WHEN** 离线契约检查运行响应丢失场景
- **THEN** 第一次写入在替身侧被记为已受理，但客户端收到超时或连接中断
- **AND** 第一次检索只返回空或旧记录
- **AND** 系统使用同一幂等键继续核对或重试，报告不出现第二个幂等键
- **AND** 场景分别断言“后续精确命中则通过”与“预算耗尽则 `write_unconfirmed`”
