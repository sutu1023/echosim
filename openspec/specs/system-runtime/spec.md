# system-runtime Specification

## Purpose
为 EchoSim 提供独立于业务功能的可运行基线，使开发者与部署平台能够确定服务是否启动、依赖是否可达，并通过稳定错误格式和请求标识定位故障，在新增创作能力时保持可重复验证。

## Requirements

### Requirement: R1 Explicit runtime configuration
The system SHALL reject missing, empty or invalid required configuration before serving requests. Required values are APP_ENV, APP_VERSION, DATABASE_URL, PORT, HEALTH_TIMEOUT_SECONDS and WORKER_POLL_SECONDS. The API additionally requires PUBLIC_ORIGIN, an exact HTTP(S) origin with HTTPS in production. Only development, test and production environments are valid; port is 1–65535; timeout is 1–10 seconds; worker interval is 1–60 seconds.

#### Scenario: Valid configuration
- **GIVEN** all required values are valid and the database is reachable
- **WHEN** the API and worker start
- **THEN** the API accepts requests and the worker emits a started event

#### Scenario: Missing configuration
- **GIVEN** DATABASE_URL is missing or empty
- **WHEN** either process starts
- **THEN** it exits nonzero without opening a listening socket or printing secret values

#### Scenario: Invalid configuration
- **GIVEN** PORT is 0 or DATABASE_URL is not a PostgreSQL connection URL
- **WHEN** either process starts
- **THEN** it exits nonzero with a safe configuration error

#### Scenario: Invalid public origin
- **WHEN** the API starts without PUBLIC_ORIGIN or production uses an HTTP origin
- **THEN** startup fails safely without serving business requests or printing configuration values

### Requirement: R2 Observable readiness
The system SHALL expose GET /health without authentication and return status, version, UTC time, request_id and dependencies.database. A successful database probe returns 200/ok/up; failure returns 503/degraded/down. A probe SHALL terminate within the configured timeout plus one second scheduling tolerance. Health responses SHALL NOT contain connection details or exception text.

#### Scenario: Healthy dependency
- **GIVEN** the database accepts queries
- **WHEN** an unauthenticated caller requests /health
- **THEN** status is 200 and the dependency is up with the configured version

#### Scenario: Unreachable dependency
- **GIVEN** database queries fail or exceed the configured timeout
- **WHEN** a caller requests /health
- **THEN** status is 503, dependency is down, and no credentials or stack trace are returned

#### Scenario: Dependency recovery
- **GIVEN** the previous health probe failed and the database now accepts queries
- **WHEN** the next caller requests /health
- **THEN** status returns to 200 without an API restart

### Requirement: R3 Request boundaries and error contract
系统 SHALL 返回含 error.code、error.message、request_id 的 JSON 错误。未知 URL 和未实现业务资源返回 404/NOT_FOUND；已实现私有 API 无有效会话返回 401，认证后不可访问的作品返回 404。不支持的方法返回 405/METHOD_NOT_ALLOWED；意外故障返回 500/INTERNAL_ERROR、安全提示与请求标识，不泄露内部细节。非法请求标识 SHALL 替换为生成的标识。

#### Scenario: Resource does not exist
- **GIVEN** the runtime is serving and the caller has a valid session
- **WHEN** GET /api/v1/works/unknown is requested
- **THEN** it returns 404/NOT_FOUND without business data

#### Scenario: No business access is exposed
- **GIVEN** a caller supplies an arbitrary bearer token without a valid session
- **WHEN** GET /api/v1/works is requested
- **THEN** it returns 401/UNAUTHENTICATED without granting access

#### Scenario: Unsupported operation
- **GIVEN** the runtime is serving
- **WHEN** POST /health is requested
- **THEN** it returns 405/METHOD_NOT_ALLOWED

#### Scenario: Invalid request identifier
- **GIVEN** an X-Request-ID longer than 64 characters or containing characters outside letters, digits, hyphens and underscores
- **WHEN** any request is made
- **THEN** response header and JSON request_id use the same generated identifier

#### Scenario: Unexpected failure
- **GIVEN** an unexpected server error occurs
- **WHEN** the request is handled
- **THEN** the response is 500/INTERNAL_ERROR without a stack trace or secret, and a correlated error log is emitted

### Requirement: R4 Structured lifecycle and request logs
The system SHALL emit one JSON object per log line containing UTC timestamp, level, service, event and request_id. Request logs include method, status and duration_ms; they SHALL NOT include authorization, cookies, query strings, bodies, database URLs or raw exception text. Worker lifecycle logs use a null request_id.

#### Scenario: Correlated request
- **GIVEN** X-Request-ID is valid
- **WHEN** a health request completes
- **THEN** the response and request log have the supplied identifier and the request log has its status and nonnegative duration_ms

#### Scenario: Graceful worker stop
- **GIVEN** the idle worker is running with a 60 second poll interval
- **WHEN** SIGTERM is delivered
- **THEN** it emits worker_stopped and exits successfully within 5 seconds without waiting the full interval

### Requirement: R5 Runtime status page
The system SHALL serve a Chinese EchoSim status page at / using the same origin as /health. It SHALL show loading, healthy, degraded and connection-error states, offer retry, and identify that creative capabilities are not yet available. It SHALL offer navigation to login or private works without fabricated data or controls for unimplemented creative capabilities.

#### Scenario: Healthy page
- **GIVEN** /health returns 200
- **WHEN** the page loads
- **THEN** it shows a healthy service state and version

#### Scenario: Failed health request
- **GIVEN** /health returns 503 or cannot be reached
- **WHEN** the page loads or the user retries
- **THEN** it shows the matching degraded or connection-error state and an enabled retry control

#### Scenario: Real business navigation
- **WHEN** a visitor follows the work entry from the status page
- **THEN** the app displays login or the authenticated work list, while story preparation, simulation and chapters remain marked unavailable

### Requirement: Shared editorial layout
The frontend SHALL use a shared paper surface, typographic hierarchy and accent for all existing screens, including the local diagnostic page.

#### Scenario: Desktop navigation
- **WHEN** a creator opens the home, login, works, preparation or settings screen
- **THEN** related content and actions occupy aligned panels, with feedback adjacent to its task and work-specific titles readable.

#### Scenario: Compact viewport
- **WHEN** the viewport cannot accommodate the desktop columns
- **THEN** panels stack and inputs, navigation, and long content remain reachable without horizontal page scrolling.
