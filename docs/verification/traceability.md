# 运行规约追溯

当前能力：system-runtime、model-connection-test、user-auth、private-works。自动化入口 `make check` 与 `make smoke`。

| Requirement / Scenario | 可执行依据 |
| --- | --- |
| R1 Valid configuration | test_normal_configuration；make smoke 启动真实进程 |
| R1 Missing configuration | test_failure_missing_configuration；test_failure_configuration_process |
| R1 Invalid configuration | test_failure_configuration_process；test_boundary_configuration |
| R2 Healthy dependency | test_normal_health_and_correlation；make smoke |
| R2 Unreachable dependency | test_failure_dependency_and_recovery；test_boundary_probe_timeout；make smoke |
| R2 Dependency recovery | test_failure_dependency_and_recovery；make smoke |
| R3 Resource does not exist | test_api_two_users_and_validation；test_failure_unknown_and_unavailable_business |
| R3 No business access is exposed | test_runtime_private_paths_reject_arbitrary_bearer |
| R3 Unsupported operation | test_failure_method |
| R3 Invalid request identifier | test_boundary_request_identifier；test_boundary_request_identifier_maximum |
| R3 Unexpected failure | test_failure_unexpected_and_log_redaction |
| R4 Correlated request | test_normal_health_and_correlation；test_failure_unexpected_and_log_redaction |
| R4 Graceful worker stop | test_normal_worker_sigterm；test_boundary_worker_stop_during_probe；make smoke |
| R5 Healthy page | App.test.tsx healthy version；test_normal_static_page_and_unknown_assets；make smoke |
| R5 Failed health request | App.test.tsx degraded/retry、connection error、malformed response |

后端测试位于 backend/tests/test_runtime.py；前端测试位于 frontend/src/App.test.tsx；真实集成位于 scripts/smoke.py。正常/失败/边界通过用例名称区分。双用户作品授权由 test_api_two_users_and_validation、真实 PostgreSQL 合同测试及浏览器验收覆盖；材料等尚未实现资源不因此声称完成隔离。

## model-connection-test

| Requirement / Scenario | 可执行依据 |
| --- | --- |
| R1 Explicit local enablement | test_success_and_origin；本地浏览器 /dev/model-test |
| R1 Disabled or production | test_disabled（三组环境/开关） |
| R1 Cross-origin submission | test_success_and_origin |
| R2 Credential lifecycle | test_success_and_origin；ModelTest clears key / clears after；浏览器刷新清空 |
| R2 Invalid input | test_invalid_key；test_valid_usage_and_unknown_fields |
| R3 Successful invocation | test_success_and_origin；真实调用成功记录见 2026-11-13-model-connection-test.md |
| R3 Missing usage | test_success_and_origin；ModelTest reports missing usage |
| R3 Timeout | test_deadline（缩短注入期限，生产固定 60 秒） |
| R3 Unsafe destination | test_public_backend_rejects_private；test_public_backend_pins_validated_address；test_transport_errors 302 |
| R4 Provider rejection | test_transport_errors 401/403/429；前端分类失败测试 |
| R4 Upstream failure | test_transport_errors 500；前端 MODEL_UPSTREAM_ERROR |
| R4 Invalid successful response | test_invalid_response；test_malformed_and_large_transport |
| R5 Pending and retry | ModelTest prevents duplicate pending requests / clears after |
| R5 Accessible status | 本地浏览器键盘焦点；role=status/aria-live 组件；实际读屏语音未验证 |
| R6 Reproducible verification | 2026-11-13-model-connection-test.md；test_safe_diagnostic_metadata；成功/失败安全调用元数据 |

## user-auth / private-works 联合验收

证据：2026-11-13-private-works.md；认证拆分说明见 2026-11-13-user-auth.md。
backend/tests 为离线单元/接口，backend/integration/test_postgres.py::test_postgres_contracts 为真实隔离数据库合同测试，frontend/src/Workspace.test.tsx 为组件行为测试。

| Requirement / Scenario | 可执行依据 |
| --- | --- |
| user-auth A1 Create and authenticate | test_account_reset_sessions_and_expiry；test_provision_invalid_input；test_password_hash_is_not_plaintext；test_postgres_contracts；浏览器密码登录与命令创建 |
| user-auth A1 Reset password | test_account_reset_sessions_and_expiry；test_expired_session_write_and_reset_endpoint；test_postgres_contracts（含并发签发）；浏览器重置后重新登录 |
| user-auth A2 Refresh and restart | test_postgres_contracts；浏览器真实 db/api 重启后详情恢复 |
| user-auth A2 Expiry boundary | test_account_reset_sessions_and_expiry；test_postgres_contracts（注入时钟） |
| user-auth A2 Logout one browser | test_account_reset_sessions_and_expiry；test_csrf_cookie_and_logs；test_expired_session_write_and_reset_endpoint；浏览器退出 |
| user-auth A3 Indistinguishable credentials | test_failures_rate_limit_recovery；test_csrf_cookie_and_logs；test_api_two_users_and_validation |
| user-auth A3 Rate limit and recovery | test_failures_rate_limit_recovery；test_source_limit_applies_across_usernames；test_postgres_contracts（并发与重新连接） |
| user-auth A4 Cross-site writes | test_csrf_cookie_and_logs |
| user-auth A4 Cookie and logs | test_production_cookie_attributes；test_csrf_cookie_and_logs；test_failure_unexpected_and_log_redaction |
| user-auth A5 Switch between accounts | test_quick_switch_and_missing_account；test_quick_api_switch；Workspace quick login；浏览器 A/B 切换与切回 |
| user-auth A5 Failed switch | test_quick_switch_and_missing_account；test_postgres_contracts（数据库触发器失败注入） |
| user-auth A5 Disabled environment | test_quick_disabled_server（三组）；Workspace disabled quick accounts；make smoke test 环境 |
| user-auth A6 Login form feedback | Workspace login reports errors / title and login fields validate / quick login blocks duplicate submission |
| user-auth A6 Protected navigation | Workspace unauthenticated deep link；浏览器会话失效跳转 |
| private-works W1 Valid and duplicate titles | test_work_validation_isolation_and_idempotency；test_postgres_contracts |
| private-works W1 Title boundaries | test_work_validation_isolation_and_idempotency；test_input_boundaries_and_invalid_json |
| private-works W1 Forged ownership | test_api_two_users_and_validation |
| private-works W2 Two-user isolation | test_api_two_users_and_validation；test_postgres_contracts；浏览器 A/B 列表与跨账号详情 |
| private-works W2 Unauthenticated access | test_runtime_private_paths_reject_arbitrary_bearer；test_expired_session_write_and_reset_endpoint |
| private-works W2 Pagination and invalid filters | test_api_two_users_and_validation；test_input_boundaries_and_invalid_json；test_postgres_contracts（同时间戳翻页） |
| private-works W3 Lost response and concurrent replay | test_postgres_contracts（八路并发、重新建立存储连接并重放）；Workspace uncertain submission |
| private-works W3 Changed payload | test_work_validation_isolation_and_idempotency；test_api_two_users_and_validation；test_postgres_contracts |
| private-works W3 Independent accounts | test_work_validation_isolation_and_idempotency；test_postgres_contracts |
| private-works W3 Transaction failure | test_postgres_contracts（写入后异常回滚再重试） |
| private-works W3 Invalid request identifier | test_work_validation_isolation_and_idempotency；test_input_boundaries_and_invalid_json |
| private-works W4 Empty list and create | Workspace empty list；浏览器 A 空列表→创建→详情 |
| private-works W4 Uncertain submission | Workspace uncertain submission reuses key until explicitly changed；pending work submission preserves the retry identity |
| private-works W4 Reload and restart | test_private_routes_refresh；浏览器详情直接导航、刷新及 db/api 重启 |
| private-works W4 Safe text rendering | Workspace private detail is text |
| system-runtime Invalid public origin | test_api_requires_explicit_public_origin；test_invalid_public_origin；test_production_requires_https_origin |
| system-runtime Real business navigation | test_private_routes_refresh；浏览器 /login→/works→详情 |

已有 R1/R3/R5 场景沿用上表并按本轮增量更正业务入口契约；实际读屏语音、公网和备份恢复未验证。

## 上一轮已实现创作链路

以下为新增 Scenario 的验证映射；真实证据未完成项不得视为已通过。

| Capability / Scenario | 自动化或实际依据 |
| --- | --- |
| persistent-model-config / Save and restart | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| persistent-model-config / Failed replacement | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| persistent-model-config / Invalid fields | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| persistent-model-config / Cross-user access | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| persistent-model-config / Secret lifecycle | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| persistent-model-config / Missing master key | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| persistent-model-config / Unsafe destination | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| persistent-model-config / Bounded failure | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| persistent-model-config / Rotation during execution | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| persistent-model-config / Retired credential cleanup | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| persistent-model-config / Concurrent activation | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| persistent-model-config / Pending form | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| persistent-model-config / Real evidence | test_model_config.py；test_z_model_config.py；ModelSettings.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / One sentence creation | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Invalid or incomplete generation | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Missing model | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Failed rewrite | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Confirm exact version | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Stale confirmation | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Regenerate after confirmation | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Concurrent submissions | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Lost response and page departure | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Worker restart | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Two-user attack | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Refresh and safe rendering | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Injection and false provenance | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| story-preparation / Fixed evaluation | test_story_generation.py；test_zz_story.py；StoryPreparation.test.tsx；对应命名的 docs/verification/ 验证记录列出真实结果与限制 |
| role-simulation / Full scene | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Missing confirmation or forged parameters | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Bound history | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Private knowledge | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Invalid architect output | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Progressive display | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Contradiction or malformed action | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Stop during action | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Stop queued or terminal task | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Reconnect gap | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Unauthorized stream | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Duplicate or competing task | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Crash or deadline | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |
| role-simulation / Joint delivery | test_simulation.py；test_zzz_simulation.py；Simulation.test.tsx；2026-11-13-minimal-role-simulation.md 记录真实结果与限制 |

## 2026-11-14 三项实现追溯（自动检查通过，联合质量验收进行中）

下表保留每个Scenario及任务链接。自动测试通过不替代真实模型与浏览器验收；未覆盖的子项仍待验证。规划检查见 [本轮规划验证](2026-11-14-simulation-planning.md)。

| Change / Capability / Requirement / Scenario | 规划验证任务 | 状态 |
| --- | --- | --- |
| 2026-11-14-narrative-simulation / role-simulation / S1 / Full scene | [任务 2.1](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 动态调度及24条上限通过，三篇固定场景验收见 2026-11-14-disclosure-review.md 与 v6 审阅 |
| 2026-11-14-narrative-simulation / role-simulation / S1 / Missing confirmation or forged parameters | [任务 2.1](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 自动验证：test_dynamic_exchange_and_bounded_endings、test_twenty_four_events_stop_replay_and_isolation；完整场景见三篇剧情验收 |
| 2026-11-14-narrative-simulation / role-simulation / S1 / Bound history | [任务 2.1](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 自动验证：test_dynamic_exchange_and_bounded_endings、test_twenty_four_events_stop_replay_and_isolation；完整场景见三篇剧情验收 |
| 2026-11-14-narrative-simulation / role-simulation / S1 / Early resolution | [任务 2.1](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | test_narrative.py：提前收束不凑数；三篇真实任务均自然结束 |
| 2026-11-14-narrative-simulation / role-simulation / S1 / Unresolved hard limit | [任务 2.1](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | test_zzz_simulation.py：24事件持久化、第25条数据库拒绝 |
| 2026-11-14-narrative-simulation / role-simulation / S2 / Private knowledge | [任务 2.1–2.2](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | test_simulation.py/test_narrative.py：角色私密字段、拒绝文本、逐段可见性隔离；真实违规样例保留 |
| 2026-11-14-narrative-simulation / role-simulation / S2 / Invalid architect output | [任务 2.1–2.2](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 非法结构/协调不发布，语义冲突在同一事件时限内重新规划；test_narrative.py、真实审阅记录。 |
| 2026-11-14-narrative-simulation / role-simulation / S2 / Natural exchange | [任务 2.1–2.2](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 自动验证：test_dynamic_exchange_and_bounded_endings、test_twenty_four_events_stop_replay_and_isolation；完整场景见三篇剧情验收 |
| 2026-11-14-narrative-simulation / role-simulation / S2 / Independent response | [任务 2.1–2.2](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 自动验证：test_dynamic_exchange_and_bounded_endings、test_twenty_four_events_stop_replay_and_isolation；完整场景见三篇剧情验收 |
| 2026-11-14-narrative-simulation / role-simulation / S3 / Progressive display | [任务 1.1、2.2](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 自动验证：test_narrative.py、test_simulation.py；真实模型评测见 narrative-v12-fixed-short.json 和披露篇复评 |
| 2026-11-14-narrative-simulation / role-simulation / S3 / Contradiction or malformed action | [任务 1.1、2.2](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 自动验证：test_narrative.py、test_simulation.py；真实模型评测见 narrative-v12-fixed-short.json 和披露篇复评 |
| 2026-11-14-narrative-simulation / role-simulation / S3 / Empty repetition | [任务 1.1、2.2](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 自动验证：test_narrative.py、test_simulation.py；真实模型评测见 narrative-v12-fixed-short.json 和披露篇复评 |
| 2026-11-14-narrative-simulation / role-simulation / S3 / Consequence of a disclosure | [任务 4.1](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 同一固定场景，从公开披露追踪到另一角色自己的选择。知识边界另见 test_narrative.py。 |
| 2026-11-14-narrative-simulation / role-simulation / S3 / Completed action and aftermath | [任务 4.1](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | v6 披露篇事件 2–3 是重复交付的失败样本，事件 12–18 是例行收尾的反例。复验检查取件原因、实际后果和末次事件。 |
| 2026-11-14-narrative-simulation / role-simulation / S3 / Mixed private and public segments | [任务 1.1、2.2](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 自动验证：test_narrative.py、test_simulation.py；真实模型评测见 narrative-v12-fixed-short.json 和披露篇复评 |
| 2026-11-14-narrative-simulation / role-simulation / S7 / Joint delivery | [任务 4.1–4.2](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 已验证24事件真实浏览器采纳/继续、停止放弃与跨账号404，三篇剧情已独立评分并达标 |
| 2026-11-14-narrative-simulation / role-simulation / S7 / Full narrative review | [任务 4.1–4.2](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 三篇独立评分通过，旧失败与复评证据见推演专项记录 |
| 2026-11-14-narrative-simulation / role-simulation / S8 / Pending validation | [任务 3.1–3.3](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-narrative-simulation / role-simulation / S8 / Ordered playback and reveal | [任务 3.1–3.3](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | NarrativePlayer.test.tsx：逐字顺序、立即显示、减少动画；Simulation.test.tsx：终态保留队列 |
| 2026-11-14-narrative-simulation / role-simulation / S8 / Reading older content | [任务 3.1–3.3](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | NarrativePlayer.test.tsx与真实Chrome验证回看、逐字显示及可访问的完成提示。 |
| 2026-11-14-narrative-simulation / role-simulation / S8 / Reconnect and replay | [任务 3.1–3.3](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | Simulation.test.tsx：SSE重复、轮询补偿、跨作品晚到；旧历史完整显示 |
| 2026-11-14-narrative-simulation / role-simulation / S8 / Accessible and safe text | [任务 3.1–3.3](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-narrative-simulation / role-simulation / S9 / Old history | [任务 1.1–1.2](../../openspec/changes/archive/2026-11-14-narrative-simulation/tasks.md) | test_zzz_simulation.py：九条旧事件读适配、ID/内容不变、迁移重放 |
| 2026-11-14-simulation-adoption / role-simulation / S10 / Adopt and continue | [任务 3.3](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | test_zzzz_adoption.py：主线/记忆绑定；真实revision1后继续已观察，完整质量未通过 |
| 2026-11-14-simulation-adoption / role-simulation / S10 / Discard and retry | [任务 3.3](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / role-simulation / S10 / Revision snapshot | [任务 3.3](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / role-simulation / S10 / Memory privacy | [任务 3.3](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | test_story_facts.py/test_simulation.py：主线事实与记忆按角色过滤，隐藏端点不泄漏 |
| 2026-11-14-simulation-adoption / simulation-adoption / A1 / Pending decision | [任务 1.2](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / simulation-adoption / A1 / Empty or failed task | [任务 1.2](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / simulation-adoption / A2 / Successful or stopped adoption | [任务 1.3、4.1](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / simulation-adoption / A2 / Discard then restart | [任务 1.3、4.1](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / simulation-adoption / A2 / Forbidden choice | [任务 1.3、4.1](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / simulation-adoption / A3 / Contradictory outcome | [任务 2.2–2.4](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / simulation-adoption / A3 / Interrupted checking | [任务 2.2–2.4](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 隔离数据库覆盖600秒超时恢复pending、丢租约及旧fence拒写。 |
| 2026-11-14-simulation-adoption / simulation-adoption / A3 / Exclusive check | [任务 2.2–2.4](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / simulation-adoption / A4 / Write failure | [任务 3.1–3.2](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | test_zzzz_adoption.py：快照、决定状态、head更新三个写点失败全部回滚 |
| 2026-11-14-simulation-adoption / simulation-adoption / A4 / Concurrent or stale commits | [任务 3.1–3.2](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | test_zzzz_adoption.py：5并发同key、采纳/放弃竞争、旧revision拒绝、重建服务后重放 |
| 2026-11-14-simulation-adoption / simulation-adoption / A4 / Snapshot consistency | [任务 3.1–3.2](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | test_zzzz_adoption.py：提交并发读取仅见完整旧/新主线 |
| 2026-11-14-simulation-adoption / simulation-adoption / A5 / Transfer and identity | [任务 2.1、3.3](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / simulation-adoption / A5 / Secret memory | [任务 2.1、3.3](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / simulation-adoption / A5 / Uncertain relation | [任务 2.1、3.3](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / simulation-adoption / A6 / Cross-user decisions | [任务 2.2、3.2](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / simulation-adoption / A6 / Lost response | [任务 2.2、3.2](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | AdoptionPanel.test.tsx 及真实Chrome注入丢响应：重试使用相同key。 |
| 2026-11-14-simulation-adoption / simulation-adoption / A7 / Decision UI restoration | [任务 1.1、4.1](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / simulation-adoption / A7 / Pre-upgrade histories | [任务 1.1、4.1](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / story-preparation / P2 / Failed rewrite | [任务 3.4](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / story-preparation / P2 / Confirm exact version | [任务 3.4](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / story-preparation / P2 / Stale confirmation | [任务 3.4](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / story-preparation / P2 / Regenerate after confirmation | [任务 3.4](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / story-preparation / P2 / Frozen preparation | [任务 3.4](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | test_zzzz_adoption.py+StoryPreparation.test.tsx：采纳后直接服务请求及UI拒绝修改，旧版本可读 |
| 2026-11-14-simulation-adoption / story-preparation / P2 / Pending result prevents revision | [任务 3.4](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / story-preparation / P3 / Concurrent submissions | [任务 1.2、2.3](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / story-preparation / P3 / Lost response and page departure | [任务 1.2、2.3](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-simulation-adoption / story-preparation / P3 / Worker restart | [任务 1.2、2.3](../../openspec/changes/archive/2026-11-14-simulation-adoption/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_story_facts.py、test_graph_api.py、AdoptionPanel.test.tsx；真实采纳与专项结果见 2026-11-14-simulation-adoption.md |
| 2026-11-14-story-graph / story-graph / G1 / Initial graph | [任务 1.1–1.3](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | test_zzzz_adoption.py：重复确认只创建一任务；真实基线12实体9关系，见joint-simulation-graph.json |
| 2026-11-14-story-graph / story-graph / G1 / Missing or failed baseline | [任务 1.1–1.3](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | test_zzzz_adoption.py：失败阻止推演、重试、租约丢失旧fence不发布；StoryGraph.test.tsx：构建入口 |
| 2026-11-14-story-graph / story-graph / G1 / Reconfirmation before canon | [任务 1.1–1.3](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | 隔离数据库确认入队故障回滚、重复确认单任务、旧版本晚到被拒。 |
| 2026-11-14-story-graph / story-graph / G2 / Key changes holder | [任务 1.2、2.2](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_graph_api.py、StoryGraph.test.tsx；真实图谱更新与专项结果见 2026-11-14-story-graph.md |
| 2026-11-14-story-graph / story-graph / G2 / Unsupported inference | [任务 1.2、2.2](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | 固定真实16例覆盖疑问、推测、谎言、无依据关系、无效来源与注入。 |
| 2026-11-14-story-graph / story-graph / G2 / Ambiguous identity | [任务 1.2、2.2](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | 固定真实16例覆盖人物/物品同名与明确别名，见story-facts-pooled-v2.json。 |
| 2026-11-14-story-graph / story-graph / G3 / Whole task then adoption | [任务 2.2、3.2](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | 隔离库验证pending不变；真实停止后待采纳revision0，采纳后revision1，完整24事件的采纳及版本变化见 full-adoption-browser.json |
| 2026-11-14-story-graph / story-graph / G3 / Stop failure and discard | [任务 2.2、3.2](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | 真实浏览器停止与放弃后图谱版本1不变并可再次启动；见browser-playback-adoption-checks.txt。 |
| 2026-11-14-story-graph / story-graph / G3 / Atomic publication failure | [任务 2.2、3.2](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | test_zzzz_adoption.py：三个写点失败旧图不变，并发读取没有混合版本 |
| 2026-11-14-story-graph / story-graph / G4 / Inspect evidence | [任务 3.1–3.3](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | StoryGraph.test.tsx及真实浏览器：原文文本、安全渲染、面板焦点和关闭后返回；见截图 |
| 2026-11-14-story-graph / story-graph / G4 / New revision fetch | [任务 3.1–3.3](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | StoryGraph.test.tsx：同版本不替换、旧revision拒绝、读取失败保留旧图与重试 |
| 2026-11-14-story-graph / story-graph / G4 / Keyboard and larger graph | [任务 3.1–3.3](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | StoryGraph.test.tsx：100实体200边无截断、键盘平移/缩放/来源焦点；完整读屏人工验收待做 |
| 2026-11-14-story-graph / story-graph / G5 / Other-user graph | [任务 2.1、2.3](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | test_graph_api.py与真实隔离数据库：401/CSRF、跨用户与不存在同404 |
| 2026-11-14-story-graph / story-graph / G5 / Creator sees a secret | [任务 2.1、2.3](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | 实现已接入；test_zzzz_adoption.py、test_graph_api.py、StoryGraph.test.tsx；真实图谱更新与专项结果见 2026-11-14-story-graph.md |
| 2026-11-14-story-graph / story-graph / G6 / Full creative loop | [任务 4.1–4.3](../../openspec/changes/archive/2026-11-14-story-graph/tasks.md) | 真实浏览器确认→完整24事件→采纳→revision1→继续成功。固定16项提取通过，见full-adoption-browser.json及story-facts-pooled-v2.json |

推演现场三栏布局：长剧情独立滚动、窄屏叠放与缺失角色名称见 [前端布局验证](2026-11-14-simulation-layout.md)。

全系统编辑式布局 — Desktop navigation、Compact viewport 的桌面及窄屏检查见 [验证记录](2026-11-14-system-editorial-layout.md)。

### 2026-11-14-chapter-drafts

| 场景 | 检查 |
| --- | --- |
| 成章资格、重连、导出、替换与并发保存、放弃只读、跨账号拒绝 | test_zzzzz_chapters.py、test_chapter_drafts.py 及[真实浏览器记录](2026-11-14-chapter-drafts.md) |
| 修改稿收件人、冲突前史、角色可见性、过期预览 | test_zzzzz_chapters.py、AdoptionPanel.test.tsx 及[真实续演记录](2026-11-14-chapter-drafts.md) |
| 回滚、重复确认与直接采纳 | test_zzzz_adoption.py、test_zzzzz_chapters.py |
