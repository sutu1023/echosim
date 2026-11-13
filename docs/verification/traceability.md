# 运行规约追溯

当前能力：system-runtime。自动化入口 `make check` 与 `make smoke`。

| Requirement / Scenario | 可执行依据 |
| --- | --- |
| R1 Valid configuration | test_normal_configuration；make smoke 启动真实进程 |
| R1 Missing configuration | test_failure_missing_configuration；test_failure_configuration_process |
| R1 Invalid configuration | test_failure_configuration_process；test_boundary_configuration |
| R2 Healthy dependency | test_normal_health_and_correlation；make smoke |
| R2 Unreachable dependency | test_failure_dependency_and_recovery；test_boundary_probe_timeout；make smoke |
| R2 Dependency recovery | test_failure_dependency_and_recovery；make smoke |
| R3 Resource does not exist | test_failure_unknown_and_unavailable_business |
| R3 No business access is exposed | test_failure_unknown_and_unavailable_business（非业务授权实现） |
| R3 Unsupported operation | test_failure_method |
| R3 Invalid request identifier | test_boundary_request_identifier；test_boundary_request_identifier_maximum |
| R3 Unexpected failure | test_failure_unexpected_and_log_redaction |
| R4 Correlated request | test_normal_health_and_correlation；test_failure_unexpected_and_log_redaction |
| R4 Graceful worker stop | test_normal_worker_sigterm；test_boundary_worker_stop_during_probe；make smoke |
| R5 Healthy page | App.test.tsx healthy version；test_normal_static_page_and_unknown_assets；make smoke |
| R5 Failed health request | App.test.tsx degraded/retry、connection error、malformed response |

后端测试位于 backend/tests/test_runtime.py；前端测试位于 frontend/src/App.test.tsx；真实集成位于 scripts/smoke.py。正常/失败/边界通过用例名称区分。当前无登录或业务资源，因此双用户授权用例在相关业务变更中实现，不把封闭路由测试当成已实现权限隔离。
