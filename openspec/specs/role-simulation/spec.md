# role-simulation Specification

## Purpose

将创作者确认的故事信息与角色推进为可观察的可能剧情，通过架构智能体协调和角色独立行动产生有限事件，并支持持久查看、停止与重连；推演结果不会自动成为正式剧情或章节。

## Requirements

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

### Requirement: S4 Stop at event boundary
An owner SHALL be able to stop a queued or running simulation idempotently. A running task SHALL enter stopping, complete at most the current in-flight event, start no further event and terminate stopped. Terminal tasks SHALL NOT resume.

#### Scenario: Stop during action
- **WHEN** stop is requested during a character call
- **THEN** at most that event is validated and committed before stopped; if it cannot complete within its bounded deadline the task stops without it

#### Scenario: Stop queued or terminal task
- **WHEN** stop is requested before execution or repeated after termination
- **THEN** queued work stops without model calls and terminal state/events remain unchanged

### Requirement: S5 Durable reconnect and isolation
Event retrieval and streaming SHALL enforce work ownership, ordered replay and bounded pages. Browser disconnection SHALL NOT stop execution. Refresh/reconnection with the last received sequence SHALL recover missing events without duplicate UI entries.

#### Scenario: Reconnect gap
- **WHEN** several events commit while the owner is disconnected
- **THEN** reconnect restores those events in sequence and shows the current durable task state

#### Scenario: Unauthorized stream
- **WHEN** another user requests status, history, events, stop or a stream
- **THEN** the result is the same 404 as an absent resource without any data or side effect; an expired session cannot continue receiving private events

### Requirement: S6 Bounded failure and exclusivity
Simulation SHALL use the preparation task mechanism for work exclusivity, idempotency and crash handling. Calls SHALL have 60-second deadlines and at most two retries with 1/2-second backoff for transient or invalid responses; total execution SHALL be bounded to 45 minutes. Failure SHALL preserve accepted events and require a new task for a new attempt.

#### Scenario: Duplicate or competing task
- **WHEN** a submission is replayed or a preparation task competes with simulation
- **THEN** keyed replay returns the original task and a different active operation receives 409

#### Scenario: Crash or deadline
- **WHEN** the worker is lost or the execution deadline expires
- **THEN** the task becomes failed within the recovery bound, the work is unlocked, accepted events remain readable, and stale workers cannot append

### Requirement: S7 Real creative acceptance
Delivery SHALL include real local browser/provider evidence and 10–20 fixed cases covering safety and complete multi-event narrative quality. Full-task review SHALL assess causal response, goal-driven action, repetition, readability and natural endings separately from model accept flags. Evidence SHALL distinguish deterministic tests, provider evaluations, human review and unverified claims.

#### Scenario: Joint delivery
- **WHEN** this change is declared complete
- **THEN** make check passes and real evidence covers rich events, natural interaction, validated typewriter playback, refresh recovery, stop and two-user isolation without claiming the later adoption or graph changes are complete

#### Scenario: Full narrative review
- **WHEN** the fixed evaluation is run
- **THEN** at least three full tasks are reviewed against a published rubric with per-case results, rejected attempts and measured latency; single-event acceptance alone cannot pass narrative quality

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

### Requirement: S10 Canon bound continuation
New simulations SHALL bind the current canon revision together with settings and model versions and SHALL start only when no pending result or active story mutation exists. Character contexts SHALL include only authorized adopted memories and visible accepted events from the current task. Unadopted histories and creator-only graph data SHALL NOT become role knowledge.

#### Scenario: Adopt and continue
- **WHEN** an owner adopts a key-transfer event and starts the next simulation
- **THEN** the new task binds the advanced canon revision and preserves the transfer for characters entitled to know it

#### Scenario: Discard and retry
- **WHEN** the previous result was discarded or failed
- **THEN** the next task binds unchanged canon and cannot recall that result's events or secrets

#### Scenario: Revision snapshot
- **WHEN** model configuration changes after submission
- **THEN** the task retains its captured model, settings and canon versions and displays unchanged historical events

#### Scenario: Memory privacy
- **WHEN** the creator has adopted A's secret action but B has not learned it
- **THEN** B's new task context excludes that action and any summaries, relation labels or evidence that reveal it

### Requirement: Three-column simulation workspace
The desktop simulation view SHALL show the work and current graph on the left, narrative playback in the middle, and the selected simulation's characters and decisions on the right.

#### Scenario: Read a long simulation
- **WHEN** the creator scrolls through overflowing narrative on desktop
- **THEN** the narrative scrolls independently while the graph, character panel and playback heading retain their positions

#### Scenario: Narrow viewport or unavailable character names
- **WHEN** the columns cannot fit or the selected simulation has no character names
- **THEN** all panels remain reachable in a stacked layout on narrow screens and missing names are represented by an empty state
