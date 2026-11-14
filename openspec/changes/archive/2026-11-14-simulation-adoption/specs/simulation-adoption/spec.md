# Spec Delta

## Purpose

让创作者在唯一故事主线上选择整次采纳或放弃推演，以明确、不可逆且一致的确认，将事件、角色记忆和故事事实纳入后续创作依据，避免候选结果、失败或重复操作污染正式剧情。

## ADDED Requirements

### Requirement: A1 Single pending result
A work SHALL permit only one active simulation or unresolved eligible result. A succeeded or explicitly stopped simulation with accepted events SHALL await adoption or discard before another simulation or preparation change. Failed and empty terminal tasks SHALL release this decision gate without becoming adoptable.

#### Scenario: Pending decision
- **WHEN** an owner submits another simulation or changes preparation while a nonempty successful/stopped result awaits a decision
- **THEN** the server returns 409 without a new task and the UI directs the owner to adopt or discard

#### Scenario: Empty or failed task
- **WHEN** a task fails or stops with zero accepted events
- **THEN** its existing events remain readable, adoption is unavailable and a new simulation can start from the unchanged canon

### Requirement: A2 Whole immutable choice
The owner SHALL explicitly choose adoption or permanent discard for the entire eligible result. Adoption SHALL require an irreversible-action acknowledgement and SHALL NOT permit selecting or editing individual events. Discarded and adopted results SHALL remain readable and SHALL NOT be reversed or adopted again as new content.

#### Scenario: Successful or stopped adoption
- **WHEN** the owner adopts a nonempty succeeded or explicitly stopped result
- **THEN** the whole accepted sequence is checked for adoption; a stopped result is labeled incomplete and unresolved-limit success remains eligible

#### Scenario: Discard then restart
- **WHEN** the owner discards the pending result
- **THEN** canon, memories and facts remain unchanged, the result becomes permanently non-adoptable and a new task starts from the same canon

#### Scenario: Forbidden choice
- **WHEN** a caller attempts partial adoption, event editing, rollback or adoption of a failed, empty, discarded or legacy-ineligible task
- **THEN** the request is rejected without changing formal story state

### Requirement: A3 Checked bounded adoption
Adoption SHALL return 202 for durable checking bound to the result, confirmed settings and expected canon revision. Before commit it SHALL validate supported fact changes and character memories against immutable sources and existing canon. Conflicts, ambiguous factual claims or exhausted processing SHALL leave the result pending and canon unchanged.

#### Scenario: Contradictory outcome
- **WHEN** adoption checking finds that a previously destroyed unique key is reused without an evidenced explanation
- **THEN** adoption fails with a safe actionable conflict message and no event, memory or fact enters canon

#### Scenario: Interrupted checking
- **WHEN** checking times out, the worker dies or the provider returns invalid output
- **THEN** the checking task fails within its recovery bound, retains a safe reason and allows retry or discard of the still-pending result

#### Scenario: Exclusive check
- **WHEN** adoption checking is active and another request tries to discard, start simulation or change preparation
- **THEN** the competing operation receives 409 and cannot alter the checked inputs

### Requirement: A4 Atomic versioned commit
A successful adoption SHALL atomically advance one canon revision, append the source events, persist character-scoped memories and publish the same revision's structured story facts. Authorization, result eligibility and expected revisions SHALL be checked again at commit. No partial success or model/network calls inside the commit transaction SHALL be permitted.

#### Scenario: Write failure
- **WHEN** a fault occurs between writing formal events, memories, facts or adoption status
- **THEN** every write rolls back and readers still observe the previous complete revision

#### Scenario: Concurrent or stale commits
- **WHEN** repeated or competing requests try to adopt one result or commit against a changed canon/settings revision
- **THEN** at most one adoption commits; identical replay returns its existing outcome and stale/conflicting input returns 409 without extra effects

#### Scenario: Snapshot consistency
- **WHEN** readers query during adoption commit
- **THEN** they receive either the complete old revision or complete new revision, never new events with old story facts

### Requirement: A5 Evidenced facts and private memories
Formal facts and memories SHALL reference confirmed-setting fields or accepted source segments and retain character visibility. Facts SHALL identify people, places, key objects and relationships with stable identities; unsupported inferences SHALL NOT become facts. Only information visible to a character SHALL enter that character's memory.

#### Scenario: Transfer and identity
- **WHEN** a validated event transfers an existing key from A to B
- **THEN** adoption preserves the key identity, replaces its current holder relationship and records the source segment without duplicating the object

#### Scenario: Secret memory
- **WHEN** the creator adopts an event containing A's private narration
- **THEN** the creator's story facts can retain it but B's memory and later context exclude it unless a later validated disclosure authorizes access

#### Scenario: Uncertain relation
- **WHEN** a character merely suspects another character owns an object
- **THEN** the suspicion cannot replace an established ownership fact or become a certain relation without evidence

### Requirement: A6 Protected durable decisions
All adoption, discard, progress, history and fact operations SHALL enforce work and resource ownership with 401 for unauthenticated callers and indistinguishable 404 for absent or other-user resources. Keyed replay SHALL be scoped by user, work and operation, survive restart and reject changed payloads.

#### Scenario: Cross-user decisions
- **WHEN** B submits, reads, retries or discards A's result or adoption task
- **THEN** the same 404 as a missing resource is returned without model calls, data disclosure or writes

#### Scenario: Lost response
- **WHEN** an adoption response is lost and the owner repeats the same keyed request after restart
- **THEN** the same task or committed outcome is returned without duplicate canon events or facts

### Requirement: A7 Sequential workflow and legacy boundary
The UI SHALL distinguish pending, checking, adopted, discarded and ineligible histories and offer only valid next actions. Existing tasks without a captured canon revision SHALL remain read-only, SHALL NOT block new sequential work and SHALL NOT be implicitly adopted.

#### Scenario: Decision UI restoration
- **WHEN** the owner reloads during checking or after an adoption/discard decision
- **THEN** durable decision state is restored, irreversible confirmation is explicit and start/adopt/discard controls match server eligibility

#### Scenario: Pre-upgrade histories
- **WHEN** a migrated work contains several old completed simulations
- **THEN** all remain readable without becoming branches, pending choices or formal story facts
