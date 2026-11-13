# Spec Delta

## Purpose

为创作者提供按用户隔离、经过连接验证且能跨重启使用的生成模型配置，使后台创作任务能够稳定绑定其提交时的模型版本，同时避免密钥泄露和不安全的外部连接。

## ADDED Requirements

### Requirement: C1 Validated persistent configuration
The system SHALL offer authenticated model settings at /settings/models, defaulting to OpenCode Go base URL https://opencode.ai/inference/go/openai/v1 and model deepseek-v4.1-flash. A new version SHALL become active only after a successful backend connection test. Embedding configuration is excluded.

#### Scenario: Save and restart
- **WHEN** the user supplies a valid key and successfully tests and saves a configuration
- **THEN** refreshing and restarting API/database preserves the active URL, model and masked credential availability

#### Scenario: Failed replacement
- **WHEN** a replacement test fails authentication, rate limit, timeout, network or response validation
- **THEN** a categorized safe error is shown and the previous active version remains unchanged

#### Scenario: Invalid fields
- **WHEN** required fields are empty, contain control characters, exceed documented limits, or unknown fields are supplied
- **THEN** the request returns 422 without calling the provider or echoing the key

### Requirement: C2 Confidential user ownership
The system SHALL encrypt persisted credentials using an environment-provided master key, never return plaintext keys, and exclude keys and private content from logs. Every configuration operation SHALL enforce authenticated ownership and write CSRF protection.

#### Scenario: Cross-user access
- **WHEN** user B reads, tests or updates user A's configuration by identifier
- **THEN** the response is the same 404 as a nonexistent resource and no model call or mutation occurs

#### Scenario: Secret lifecycle
- **WHEN** a distinctive key is saved or a call fails
- **THEN** database credential fields contain ciphertext, responses expose only a fixed mask, logs contain no marker and the browser clears the key field without browser-storage persistence

#### Scenario: Missing master key
- **WHEN** persistent configuration is enabled without a valid master key
- **THEN** startup fails safely without printing configuration values

### Requirement: C3 Restricted bounded connections
The system SHALL accept only the supported HTTPS OpenCode Go base URL, reject redirects and non-public DNS destinations on every connection, and use a 60-second total connection-test deadline without automatic retry. HTTP success without nonempty assistant text SHALL fail validation.

#### Scenario: Unsafe destination
- **WHEN** a user supplies another origin/path, or DNS includes private, loopback or metadata addresses, or the server redirects
- **THEN** the operation fails without sending credentials to an unsafe destination or redirect target

#### Scenario: Bounded failure
- **WHEN** the provider hangs or returns malformed success
- **THEN** the test returns a safe timeout within 61 seconds or an invalid-response error respectively

### Requirement: C4 Immutable task binding
New creative tasks SHALL bind the current validated configuration version atomically at acceptance. Later configuration changes SHALL affect only new tasks. Retired versions SHALL retain credentials only while queued or active tasks reference them; historical metadata SHALL remain readable without retaining the credential.

#### Scenario: Rotation during execution
- **WHEN** a task bound to v1 is active and v2 becomes current
- **THEN** that task continues using v1 and newly accepted tasks use v2

#### Scenario: Retired credential cleanup
- **WHEN** the last nonterminal task referencing a retired version terminates
- **THEN** its stored credential is removed within 60 seconds while version metadata and task history remain available

#### Scenario: Concurrent activation
- **WHEN** two updates target the same current version
- **THEN** at most one activates and the stale update receives 409 without overwriting the winner

### Requirement: C5 Usable verified settings
The settings page SHALL expose loading, empty, pending, success and categorized failure states with keyboard-accessible controls. Verification SHALL distinguish automated tests from actual provider calls.

#### Scenario: Pending form
- **WHEN** a test/save is pending
- **THEN** duplicate submission is disabled and accessible status feedback is displayed

#### Scenario: Real evidence
- **WHEN** this change is marked implemented
- **THEN** evidence records a real successful configuration save and restart, safe failure checks, tested revision and limitations without key disclosure
