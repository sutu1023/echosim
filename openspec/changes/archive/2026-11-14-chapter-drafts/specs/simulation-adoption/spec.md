# Simulation adoption

## MODIFIED Requirements

### Requirement: A2 Whole immutable choice
The owner SHALL explicitly adopt or permanently discard the entire eligible result. Adoption requires irreversible-action acknowledgement. For a modified draft, the owner SHALL review key changes and choose “采纳修改稿” for that revision. Without a draft the owner MAY adopt the simulation directly. Individual event editing and partial adoption are forbidden. Adopted and discarded results remain readable and cannot be reversed or adopted again.

#### Scenario: Successful or stopped adoption
- **WHEN** the owner adopts a nonempty succeeded or explicitly stopped result
- **THEN** the whole chosen sequence is checked for adoption; a stopped result is labeled incomplete and unresolved-limit success remains eligible

#### Scenario: Discard then restart
- **WHEN** the owner discards the pending result
- **THEN** canon, memories and facts remain unchanged, the result becomes permanently non-adoptable and a new task starts from the same canon

#### Scenario: Forbidden choice
- **WHEN** a caller attempts partial adoption, event editing, rollback or adoption of a failed, empty, discarded or legacy-ineligible task
- **THEN** the request is rejected without changing formal story state

#### Scenario: Changed draft after preview
- **WHEN** a newer edit exists when the creator confirms the preview
- **THEN** the system rejects the stale confirmation and requires review of the current version.

### Requirement: A3 Checked bounded adoption
Adoption SHALL return 202 for durable checking bound to the result, confirmed settings and expected canon revision. Before commit it SHALL validate supported fact changes and character memories against the chosen whole-round source, confirmed settings and canon at the start of this round and existing canon. Conflicts, ambiguous factual claims or exhausted processing SHALL leave the result pending and canon unchanged.

#### Scenario: Contradictory outcome
- **WHEN** adoption checking finds that a previously destroyed unique key is reused without an evidenced explanation
- **THEN** adoption fails with a safe actionable conflict message and no event, memory or fact enters canon

#### Scenario: Interrupted checking
- **WHEN** checking times out, the worker dies or the provider returns invalid output
- **THEN** the checking task fails within its recovery bound, retains a safe reason and allows retry or discard of the still-pending result

#### Scenario: Exclusive check
- **WHEN** adoption checking is active and another request tries to discard, start simulation or change preparation
- **THEN** the competing operation receives 409 and cannot alter the checked inputs

#### Scenario: Rewrite an earlier fact
- **WHEN** the draft asserts that C always held the key despite established prior ownership by A
- **THEN** the conflict identifies the earlier fact, preserves the editable draft and blocks adoption.

### Requirement: A4 Atomic versioned commit
A successful adoption SHALL atomically advance one canon revision, append formal events derived from the reviewed source (the accepted simulation or the exact reviewed draft revision), persist character-scoped memories and publish the same revision's structured story facts. Authorization, result eligibility and expected revisions SHALL be checked again at commit. No partial success or model/network calls inside the commit transaction SHALL be permitted.

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
Formal facts and memories SHALL reference confirmed-setting fields or accepted source segments, including version-bound passages supporting formal events derived from a reviewed draft and retain character visibility. Facts SHALL identify people, places, key objects and relationships with stable identities; unsupported inferences SHALL NOT become facts. Only information visible to a character SHALL enter that character's memory.

#### Scenario: Transfer and identity
- **WHEN** a validated event transfers an existing key from A to B
- **THEN** adoption preserves the key identity, replaces its current holder relationship and records the source segment without duplicating the object

#### Scenario: Secret memory
- **WHEN** the creator adopts an event containing A's private narration
- **THEN** the creator's story facts can retain it but B's memory and later context exclude it unless a later validated disclosure authorizes access

#### Scenario: Uncertain relation
- **WHEN** a character merely suspects another character owns an object
- **THEN** the suspicion cannot replace an established ownership fact or become a certain relation without evidence

#### Scenario: Changed recipient
- **WHEN** A held the key before the round, the simulation gives it to B, and the owner adopts a draft giving it to C instead
- **THEN** the formal transfer is A to C, the same key now has C as holder, B gains no memory of receiving it, and the next simulation uses the revised event and character-visible memories.
