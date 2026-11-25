# Spec Delta

## ADDED Requirements

### Requirement: 第三阶段提供按轮运行时记忆图谱与行动回链

第三阶段工作台 SHALL 在 Change 09 的时钟、行动流与平台统计之外提供运行时记忆图谱：轮次选择器、可选平台筛选、材料/角色发言/推演结果三种图例、节点/边详情与来源行动回链。轮次选择器 MUST 只提供 `1..已完成最大轮`；默认选择最新已完成轮，MUST NOT 提供未来轮次或伪造空轮。

页面 MUST 明确区分“查看第 R 轮结束后的图”和“角色开始第 R 轮前读到的记忆”：前者按 `round_index <= R` 展示，后者只作为行动来源与执行入口证据展示，MUST NOT 把角色未读取的记忆画成角色已读。选择 `platform` 时 MUST 只展示该平台运行记忆；不选择时 MAY 同时展示两平台，但每条来源 MUST 保留平台标识。

点击节点或边的来源 MUST 回到同一 `runId`、同一 `platformKey` 的原行动，显示 `actionId`、角色、轮次、模拟时间、原事件序号与目标；MUST NOT 跳到另一运行、另一平台或伪造行动。目标未解析时 MUST 显示“未在本运行中定位”，MUST NOT 显示为可点击链接。

#### Scenario: 切换第 1、2、3 轮查看图增长

- **WHEN** 三轮运行完成，用户在第三阶段切换 `round=1`、`round=2`、`round=3`
- **THEN** 第 1 轮图不显示第 2 轮评论来源
- **AND** 第 2、3 轮图显示同一评论来源
- **AND** 第 3 轮图比第 2 轮图多出第 3 轮已处理节点/边
- **AND** 每次切换后地址保留 `run`、`selection`、`build`、`preparation`、`layout`、`lang` 与当前 `round`/`platform`

#### Scenario: 平台筛选不混入另一平台

- **WHEN** 用户选择 `platform=platform_b` 查看第 2 轮
- **THEN** 画布只显示平台 B 的第 1–2 轮运行记忆
- **AND** 平台 A 的节点、边或来源不出现在画布或详情
- **AND** 图例与平台标签仍显示 `platform_b/reddit`

#### Scenario: 点击来源回到原行动

- **WHEN** 用户点击一条第 2 轮评论来源
- **THEN** 页面打开该 `actionId` 的行动详情或高亮对应行动行
- **AND** 显示同一运行、同一平台、第 2 轮、模拟时间与原事件序号
- **AND** 不改变当前轮次图的布局与选中平台

#### Scenario: 跨运行或跨平台来源不可跳转

- **WHEN** 记忆来源引用的 `actionId` 属于另一运行或另一平台
- **THEN** 页面显示目标未解析
- **AND** 不打开其他运行的行动详情
- **AND** 不把该来源画成当前运行的已确认行动

### Requirement: 记忆状态、刷新恢复与迟到响应守卫

第三阶段 MUST 显示记忆状态：`unconfigured`、`pending`、`syncing`、`complete`、`partial`、`unconfirmed`、`unavailable`。`unavailable` 时 MUST 显示“记忆同步暂不可用”、待同步数与重试入口；`unconfirmed` 时 MUST 显示“写入待确认，不会换键重写”，MUST NOT 显示为完成；`unconfigured` 时 MUST 说明当前未配置图谱且未发起外部调用。

页面刷新 MUST 从 URL 恢复 `run`、`selection`、`build`、`preparation`、`round`、`platform`、`layout` 与 `lang`，并按服务端返回的同一 `runId`/`round`/`platform` 重绘。切换运行、轮次或平台时 MUST 立即解绑旧视图；旧请求的迟到响应 MUST 按 `runId` + `round` + `platform` 丢弃，MUST NOT 覆盖新视图或把旧运行的节点画到新图。

记忆图节点、边、来源与状态 MUST 全部来自服务端响应；MUST NOT 用前端定时器、事件条数、计划轮数或累计计数推算图增长或完成。SSE 事件只用于唤醒刷新，MUST NOT 作为记忆事实来源。

#### Scenario: 刷新后保持同一轮次图

- **WHEN** 用户在 `round=2&platform=platform_b` 刷新页面
- **THEN** 页面恢复同一运行、同一轮次与同一平台的图
- **AND** 节点、边、来源与状态与刷新前一致
- **AND** 不回到默认最新轮或另一平台

#### Scenario: 旧轮次响应不覆盖新轮次

- **WHEN** 用户从 `round=2` 快速切换到 `round=3`，`round=2` 的响应后到
- **THEN** 页面仍显示 `round=3` 的图
- **AND** 丢弃 `round=2` 的迟到响应
- **AND** 选中平台、来源详情与状态不被旧响应改写

#### Scenario: 切换运行后不显示上一运行记忆

- **WHEN** 用户从运行 A 切换到同一选择的运行 B
- **THEN** 页面立即清空运行 A 的节点、边、来源与来源详情
- **AND** 只显示运行 B 的 `memoryId`、`episodeUuid` 与 `runId`
- **AND** 运行 A 的迟到响应不会出现在运行 B 的图

#### Scenario: 图服务暂不可达的状态可见

- **WHEN** 记忆接口返回 `memory_status=unavailable`
- **THEN** 页面显示暂不可用、待同步数与重试按钮
- **AND** 仍显示已提交的行动流、轮次与本地记忆条目
- **AND** 不显示“记忆已完成”或空图冒充完成

#### Scenario: 未确认状态不被显示为完成

- **WHEN** 某条记忆的 `write_outcome=unconfirmed`
- **THEN** 页面显示“写入待确认”与原因码
- **AND** 该记忆不出现为已发布节点或关系
- **AND** 重试按钮不会以换键重写替代查账

#### Scenario: 中英文与布局保持

- **WHEN** 用户在记忆中英文界面或三种布局之间切换
- **THEN** 轮次、平台、图层图例、来源与状态文案随语言切换
- **AND** 项目名、材料名、角色显示名与图节点名称保持原值
- **AND** `round`、`platform`、`run`、`selection` 等查询参数在切换后保留
