## Purpose

为本地开发者提供可观察的真实模型连接测试，通过前端临时输入凭据、后端固定供应商调用及明确结果区分，验证网络与凭据可用性，同时避免将工程测试误认为持久模型配置、创作能力或公网验证。

## ADDED Requirements

### Requirement: R1 Development-only access
The system SHALL expose the test page and API only when APP_ENV is development and MODEL_CONNECTION_TEST_ENABLED is explicitly true. The default SHALL be disabled. Production SHALL return 404 even if the flag is true. Local launch instructions SHALL bind the exposed development service to loopback.

#### Scenario: Explicit local enablement
- **WHEN** development mode and the enable flag are active
- **THEN** /dev/model-test and POST /api/v1/model-connection-tests are available without creating an account or business resource

#### Scenario: Disabled or production
- **WHEN** the flag is absent or false, or the environment is not development
- **THEN** the page and API return the existing 404/NOT_FOUND contract and no provider call occurs

#### Scenario: Cross-origin submission
- **WHEN** a browser submits from a foreign Origin
- **THEN** the API rejects it without calling the provider and does not grant cross-origin access

### Requirement: R2 Ephemeral credentials
The system SHALL accept a KEY only in a JSON request body, use it only for the current provider call, and never persist or return it. Logs SHALL exclude credentials, bodies and raw provider errors. The browser SHALL not store it in URLs or browser storage and SHALL clear the input on success, failure or page exit. Responses SHALL use Cache-Control: no-store.

#### Scenario: Credential lifecycle
- **WHEN** a test succeeds or fails with a distinctive secret marker
- **THEN** the field is cleared and the marker is absent from responses, logs, storage and database writes

#### Scenario: Invalid input
- **WHEN** the KEY is empty, whitespace-only, longer than 4096 characters, contains control characters, or unknown request fields are supplied
- **THEN** the API returns 422/INVALID_REQUEST without echoing input or calling the provider

### Requirement: R3 Fixed bounded invocation
The system SHALL call only https://opencode.ai/inference/go/openai/v1/chat/completions with model deepseek-v4.1-flash, a versioned fixed short prompt and non-streaming output. It SHALL reject address/model/prompt overrides, refuse redirects and non-public resolved destinations, enforce a 60-second total deadline, and make no automatic retry.

#### Scenario: Successful invocation
- **WHEN** a valid KEY is submitted and the provider returns a successful HTTP response with a nonempty textual assistant answer
- **THEN** the API returns 200 with success, model, duration_ms, request_id and validated token usage when supplied

#### Scenario: Missing usage
- **WHEN** a valid answer has no usable token usage
- **THEN** the connection succeeds and the UI labels usage as 未提供 without estimating it

#### Scenario: Timeout
- **WHEN** the provider does not complete within 60 seconds
- **THEN** the request ends with 504/MODEL_TIMEOUT within one additional second of scheduling tolerance and no retry occurs

#### Scenario: Unsafe destination
- **WHEN** resolution includes a non-public address or the provider responds with a redirect
- **THEN** no credentials are sent to that unsafe destination or redirect target and the test fails safely

### Requirement: R4 Meaningful failure results
The system SHALL distinguish provider authentication, quota/rate limit, timeout, upstream/network failure and invalid response. All errors SHALL preserve error.code, error.message and request_id and SHALL exclude raw upstream content. HTTP success alone SHALL NOT establish model success.

#### Scenario: Provider rejection
- **WHEN** the provider returns 401/403 or 429
- **THEN** the API returns respectively 502/MODEL_AUTH_FAILED or 429/MODEL_RATE_LIMITED with safe actionable text and no automatic retry

#### Scenario: Upstream failure
- **WHEN** a network failure or another non-success provider status occurs
- **THEN** the API returns 502/MODEL_UPSTREAM_ERROR without raw exception text

#### Scenario: Invalid successful response
- **WHEN** a successful provider response is malformed, contains an error, lacks a textual answer, or contains only whitespace or reasoning content
- **THEN** the API returns 502/MODEL_INVALID_RESPONSE rather than success

### Requirement: R5 Observable local interface
The page SHALL display the fixed provider, base URL and model, a masked KEY field and a test button. It SHALL show idle, pending, success and categorized failure states; prevent duplicate submission while pending; and present elapsed time and available token usage on success. It SHALL identify that settings are temporary and that this is not a creative capability.

#### Scenario: Pending and retry
- **WHEN** a test is pending and subsequently fails
- **THEN** duplicate submission is disabled during the request, a readable failure appears, and a new attempt requires entering the KEY again

#### Scenario: Accessible status
- **WHEN** a keyboard user completes a test
- **THEN** the controls are reachable, focus is visible and the result is announced through a status region

### Requirement: R6 Traceable evidence
The system SHALL record safe model/configuration/prompt versions, configured timeout, output limit, temperature setting, elapsed time and available usage for model tests. Real local validation SHALL be recorded separately from mocked tests; absent real validation SHALL be marked pending. Neither SHALL imply public deployment, model quality evaluation or M0 completion.

#### Scenario: Reproducible verification
- **WHEN** verification evidence is written
- **THEN** it identifies the tested revision, environment, commands, results and limitations without KEY or full prompt/response logs
