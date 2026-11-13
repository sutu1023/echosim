# System runtime

## Purpose
为 EchoSim 提供独立于业务功能的可运行基线，使开发者与部署平台能够确定服务是否启动、依赖是否可达，并通过稳定错误格式和请求标识定位故障，在新增创作能力时保持可重复验证。

## ADDED Requirements

### Requirement: R1 Explicit runtime configuration
The system SHALL reject missing, empty or invalid required configuration before serving requests. Required values are APP_ENV, APP_VERSION, DATABASE_URL, PORT, HEALTH_TIMEOUT_SECONDS and WORKER_POLL_SECONDS. Only development, test and production environments are valid; port is 1–65535; timeout is 1–10 seconds; worker interval is 1–60 seconds.

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
The system SHALL return JSON errors with error.code, error.message and request_id. Unknown URLs including unimplemented business resources return 404/NOT_FOUND. Unsupported methods return 405/METHOD_NOT_ALLOWED. Unexpected failures return 500/INTERNAL_ERROR with a safe message and request identifier; details remain private. Invalid request identifiers SHALL be replaced with a generated identifier.

#### Scenario: Resource does not exist
- **GIVEN** the runtime is serving
- **WHEN** GET /api/v1/works/unknown is requested
- **THEN** it returns 404/NOT_FOUND without business data

#### Scenario: No business access is exposed
- **GIVEN** a caller supplies an arbitrary bearer token
- **WHEN** GET /api/v1/works is requested
- **THEN** it returns the same 404 contract as an unknown resource without granting access

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
The system SHALL serve a Chinese EchoSim status page at / using the same origin as /health. It SHALL show loading, healthy, degraded and connection-error states, offer retry, and identify that creative capabilities are not yet available. It SHALL NOT present fabricated works or functional business controls.

#### Scenario: Healthy page
- **GIVEN** /health returns 200
- **WHEN** the page loads
- **THEN** it shows a healthy service state and version

#### Scenario: Failed health request
- **GIVEN** /health returns 503 or cannot be reached
- **WHEN** the page loads or the user retries
- **THEN** it shows the matching degraded or connection-error state and an enabled retry control
