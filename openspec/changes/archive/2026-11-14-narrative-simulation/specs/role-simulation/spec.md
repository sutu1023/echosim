# Spec Delta

## MODIFIED Requirements

### Requirement: S1 Bounded versioned simulation
The system SHALL return 202 for an owned work with confirmed settings and an active model configuration, binding both immutable versions. New tasks SHALL use three confirmed characters in one scene with a target of 12–18 accepted events and a hard limit of 24, ending at a meaningful stopping point or the event limit rather than a fixed turn count.

#### Scenario: Full scene
- **WHEN** an owner starts a simulation and valid actions advance the scene
- **THEN** ordered events continue toward a staged outcome, normally within 12–18 events, with no requirement that all characters act once per round

#### Scenario: Missing confirmation or forged parameters
- **WHEN** confirmation/configuration is absent, or a caller requests different character count, rounds, event limits or unknown parameters
- **THEN** no task starts and an actionable precondition error or 422 is returned respectively

#### Scenario: Bound history
- **WHEN** configuration or permitted confirmed preparation changes
- **THEN** existing tasks retain their bound inputs and unchanged history

#### Scenario: Early resolution
- **WHEN** the scene reaches an evidenced meaningful outcome before event 12
- **THEN** it can finish successfully with an outcome summary rather than padding the story

#### Scenario: Unresolved hard limit
- **WHEN** accepted event 24 commits while the conflict remains unresolved
- **THEN** no event 25 starts and the task succeeds with an explicit unresolved-limit ending, without inventing a resolution

### Requirement: S2 Independent bounded agents
The plot architect SHALL choose the next actor according to unresolved situations and visible consequences, permitting consecutive actions and bystanders. Each actor SHALL act from its own identity, goals and permitted knowledge without deciding another actor's voluntary response. Coordination SHALL preserve confirmed facts, prevent secret leakage and add no autonomous characters.

#### Scenario: Private knowledge
- **WHEN** A owns a secret unavailable to B
- **THEN** B's context and coordination delivered to B exclude that secret until validated disclosure

#### Scenario: Invalid architect output
- **WHEN** coordination invents an actor, changes world rules, uses hidden knowledge or invents a resolution
- **THEN** it is rejected and bounded retries cannot publish it as accepted coordination

#### Scenario: Natural exchange
- **WHEN** A challenges B while C has no reason to intervene
- **THEN** B can respond, A can react next and C can remain a bystander without a forced turn

#### Scenario: Independent response
- **WHEN** A proposes that B hand over a key
- **THEN** A's event cannot decide B's voluntary compliance or invent B's new dialogue; B's own later action determines the response

### Requirement: S3 Validated progressive events
The system SHALL validate event structure, actor, references, visibility, consistency and narrative progress before atomically persisting and exposing an event. New events SHALL contain 3–6 ordered narration/dialogue segments, targeting 200–400 visible Unicode characters. Only accepted events SHALL influence subsequent actors; model output SHALL NOT execute tools or mutate state.

#### Scenario: Progressive display
- **WHEN** an event passes all validation
- **THEN** the complete event receives a stable sequence and becomes available before task completion, with segment order and speaker identity preserved

#### Scenario: Contradiction or malformed action
- **WHEN** an event conflicts with facts, uses forbidden knowledge, impersonates another actor or remains malformed after bounded retries
- **THEN** no part is displayed or enters subsequent context, and the task fails while preserving earlier events

#### Scenario: Empty repetition
- **WHEN** a candidate repeats an earlier exchange without new information, changed situation or a consequential choice
- **THEN** it is rejected for regeneration rather than counted as story progress

#### Scenario: Consequence of a disclosure
- **WHEN** a character publicly reveals information or sets a boundary that affects another character's choice
- **THEN** the affected character has an independent opportunity to act on it, using only what they witnessed

#### Scenario: Completed action and aftermath
- **WHEN** an object was already transferred and the recipient acted on the choice behind that transfer
- **THEN** a candidate replaying the transfer without a new cause is rejected, and once the central choice has a visible consequence routine bookkeeping alone cannot prolong the scene

#### Scenario: Mixed private and public segments
- **WHEN** an event contains private narration and public dialogue
- **THEN** each segment is validated and filtered by its own visibility; the private narration never reaches other characters through surrounding text or summaries

### Requirement: S7 Real creative acceptance
Delivery SHALL include real local browser/provider evidence and 10–20 fixed cases covering safety and complete multi-event narrative quality. Full-task review SHALL assess causal response, goal-driven action, repetition, readability and natural endings separately from model accept flags. Evidence SHALL distinguish deterministic tests, provider evaluations, human review and unverified claims.

#### Scenario: Joint delivery
- **WHEN** this change is declared complete
- **THEN** make check passes and real evidence covers rich events, natural interaction, validated typewriter playback, refresh recovery, stop and two-user isolation without claiming the later adoption or graph changes are complete

#### Scenario: Full narrative review
- **WHEN** the fixed evaluation is run
- **THEN** at least three full tasks are reviewed against a published rubric with per-case results, rejected attempts and measured latency; single-event acceptance alone cannot pass narrative quality

## ADDED Requirements

### Requirement: S8 Readable validated playback
The UI SHALL distinguish named dialogue from action/environment narration in a conversational flow, hiding round, participant-list and visibility metadata. Only validated text SHALL animate character by character in segment order, with an immediate-display control. Playback SHALL NOT control worker execution or alter durable event state.

#### Scenario: Pending validation
- **WHEN** generation or validation is still running
- **THEN** the UI displays a safe stage indicator and never shows unvalidated text, hidden reasoning or private coordination details

#### Scenario: Ordered playback and reveal
- **WHEN** a validated event arrives and the user chooses immediate display during animation
- **THEN** all remaining text of that event appears once in order and later events remain queued for their own playback

#### Scenario: Reading older content
- **WHEN** the user scrolls away from the latest event while new events arrive
- **THEN** automatic scrolling stops, a new-content indicator appears and an explicit return resumes following

#### Scenario: Reconnect and replay
- **WHEN** a page reloads or SSE and polling return overlapping events
- **THEN** events are deduplicated and ordered; restored history is shown complete while newly arriving events animate once, without replaying the whole history

#### Scenario: Accessible and safe text
- **WHEN** text contains markup or the user uses keyboard/reduced-motion preferences
- **THEN** text remains inert, controls are keyboard reachable, reduced motion shows full segments and assistive announcements occur per completed segment rather than per character

### Requirement: S9 Versioned legacy history
Existing nine-event histories SHALL remain readable without rewriting their content. New event formats and task ending reasons SHALL be identifiable so old records are not interpreted as incomplete new tasks.

#### Scenario: Old history
- **WHEN** an owner opens a task created before this change
- **THEN** its original action/dialogue/result are rendered as narration and named dialogue using its bound settings, preserving event IDs and accepted content
