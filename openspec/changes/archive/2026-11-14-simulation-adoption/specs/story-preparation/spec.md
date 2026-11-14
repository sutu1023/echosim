# Spec Delta

## MODIFIED Requirements

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
