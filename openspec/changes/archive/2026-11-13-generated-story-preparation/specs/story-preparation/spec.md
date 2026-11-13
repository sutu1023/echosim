# Spec Delta

## Purpose

让创作者通过一句故事创意自动获得概要、世界规则、大纲与三名角色，并通过自然语言修改和整版确认形成可供推演使用的故事依据；持久任务和不可变版本确保失败、重启与后续修改不破坏既有结果。

## ADDED Requirements

### Requirement: P1 Complete generated candidate
The system SHALL accept a nonblank story idea of at most 2000 Unicode code points for an owned work and return 202 with a durable task. A successful candidate SHALL contain synopsis, world rules, brief outline and exactly three characters with identity, goals, background, public information and explicitly scoped private knowledge.

#### Scenario: One sentence creation
- **WHEN** an owner submits a valid idea with an active validated model configuration
- **THEN** the task eventually exposes a complete candidate labeled generated and unconfirmed, without requiring manual field entry

#### Scenario: Invalid or incomplete generation
- **WHEN** input is blank/oversized or generated output remains malformed, incomplete or has the wrong character count after bounded retries
- **THEN** invalid input is rejected with 422 before a call, or the accepted task fails without publishing a partial candidate

#### Scenario: Missing model
- **WHEN** no validated model configuration exists
- **THEN** submission is rejected with an actionable configuration-required error and no task is accepted

### Requirement: P2 Revision and explicit confirmation
The system SHALL preserve immutable candidate versions and allow natural-language whole-version revision. Only explicit confirmation of the current candidate SHALL change the current confirmed version. Creating or revising a candidate SHALL NOT implicitly confirm it or alter an earlier confirmation.

#### Scenario: Failed rewrite
- **WHEN** rewriting a candidate fails
- **THEN** the prior candidate and confirmed version remain available and unchanged

#### Scenario: Confirm exact version
- **WHEN** the user confirms the current candidate by its version identifier
- **THEN** the complete setting and all three characters become confirmed atomically; repeating confirmation of that version has no additional effect

#### Scenario: Stale confirmation
- **WHEN** the candidate has changed since the page loaded
- **THEN** confirming the stale candidate returns 409 and requests a refresh instead of confirming unseen content

#### Scenario: Regenerate after confirmation
- **WHEN** a confirmed work receives a successful new generation and subsequent confirmation
- **THEN** new simulations use the new confirmation while existing simulations retain their original version and history

### Requirement: P3 Durable exclusive tasks
Submission SHALL atomically record task, immutable input and model version. At most one nonterminal story-mutating task SHALL exist per work across preparation and simulation. Same-user/work/operation idempotent replay SHALL return the original task; changed input with the same key SHALL return 409.

#### Scenario: Concurrent submissions
- **WHEN** requests for a work arrive concurrently
- **THEN** identical keyed submissions resolve to one task and competing operations receive 409 without extra effects

#### Scenario: Lost response and page departure
- **WHEN** the response is lost or the page closes
- **THEN** replay finds the same task and execution continues independently of the browser

#### Scenario: Worker restart
- **WHEN** a worker dies before claiming a queued task or while running a task
- **THEN** queued work remains claimable; an abandoned running task becomes failed within the documented recovery bound and cannot later publish a result

### Requirement: P4 Protected observable history
Every preparation version and task read/write SHALL enforce work ownership. Unauthenticated access SHALL return 401; other-user and absent resources SHALL return the same 404. Preparation UI SHALL show loading, empty, queued/running, complete and safe failure states and reload persisted history.

#### Scenario: Two-user attack
- **WHEN** another user guesses work, task or version identifiers
- **THEN** reads, revisions and confirmations return 404 without private content, model calls or writes

#### Scenario: Refresh and safe rendering
- **WHEN** an owner reloads /works/:id/prepare after completion or failure
- **THEN** persisted versions and task status are restored, text is rendered safely and no secret key or raw upstream error is exposed

### Requirement: P5 Bounded trusted publication
Generated content SHALL be treated as untrusted data. Each generation call SHALL have a 60-second deadline, at most two retries with 1/2-second backoff for transient or invalid output, and a 240-second task execution deadline. Authorization failures SHALL NOT retry. Only validated complete output SHALL become a candidate.

#### Scenario: Injection and false provenance
- **WHEN** an idea or generated text asks to override instructions, access another work, or invent material citations
- **THEN** authorization and output constraints remain enforced and generated content is not represented as sourced material or confirmed facts

#### Scenario: Fixed evaluation
- **WHEN** the first creative model capability is delivered or its model/prompt changes
- **THEN** 10–20 fixed cases cover completion, privacy, injection, conflict, provenance and unauthorized requests, with actual outcomes recorded separately from mocked tests
