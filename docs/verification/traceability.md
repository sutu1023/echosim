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

## 本轮创作链路

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
