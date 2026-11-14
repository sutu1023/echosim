# story-preparation Specification

## Purpose

让创作者通过一句故事创意自动获得概要、世界规则、大纲与三名角色，并通过自然语言修改和整版确认形成可供推演使用的故事依据；持久任务和不可变版本确保失败、重启与后续修改不破坏既有结果。

## Requirements

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
The system SHALL preserve immutable candidate versions and allow natural-language whole-version revision before first adoption and while no unresolved simulation or story-mutating task exists. Only explicit confirmation of the current candidate SHALL change the confirmed version. First adoption SHALL freeze preparation generation, revision and reconfirmation; existing versions remain readable.

#### Scenario: Failed rewrite
- **WHEN** an otherwise permitted candidate rewrite fails
- **THEN** the prior candidate and confirmed version remain unchanged

#### Scenario: Confirm exact version
- **WHEN** before first adoption and without a pending result or active task the owner confirms the current candidate
- **THEN** settings and all three characters become confirmed atomically and repeating that confirmation has no additional effect

#### Scenario: Stale confirmation
- **WHEN** the candidate changed since the page loaded
- **THEN** stale confirmation returns 409 and requests a refresh

#### Scenario: Regenerate after confirmation
- **WHEN** before first adoption, with no unresolved result or active task, a confirmed work receives a successful new generation and confirmation
- **THEN** new simulations use the new settings and existing histories retain their original immutable versions

#### Scenario: Frozen preparation
- **WHEN** an owner requests generation, revision or confirmation after the first adoption
- **THEN** the request returns 409 before model invocation, the UI explains that the main story has started and existing settings remain readable

#### Scenario: Pending result prevents revision
- **WHEN** an eligible simulation result still awaits adoption or discard
- **THEN** preparation writes return 409 and the UI directs the owner to resolve that result first

### Requirement: P3 Durable exclusive tasks
Submission SHALL atomically record task, immutable input and model version. At most one nonterminal story-mutating task SHALL exist per work across preparation, simulation and adoption checking. Same-user/work/operation keyed replay SHALL return the original task; changed input with the same key SHALL return 409. Unresolved eligible simulation results SHALL additionally block new preparation and simulation tasks.

#### Scenario: Concurrent submissions
- **WHEN** preparation, simulation or adoption requests arrive concurrently for a work
- **THEN** identical keyed requests return one task and competing operations receive 409 without extra effects

#### Scenario: Lost response and page departure
- **WHEN** a response is lost or the page closes
- **THEN** replay finds the same task and execution continues independently of the browser

#### Scenario: Worker restart
- **WHEN** a worker dies before claiming or while executing a task
- **THEN** queued work remains claimable, abandoned running work fails within the documented recovery bound and stale workers cannot publish

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
