# role-simulation Specification

## Purpose

将创作者确认的故事信息与角色推进为可观察的可能剧情，通过架构智能体协调和角色独立行动产生有限事件，并支持持久查看、停止与重连；推演结果不会自动成为正式剧情或章节。

## Requirements

### Requirement: S1 Bounded versioned simulation
The system SHALL accept an owned work with a confirmed preparation version and active model configuration, returning 202 for a persistent simulation bound to both versions. A completed simulation SHALL contain one scene and three rounds, each with one validated action per each of the three confirmed characters.

#### Scenario: Full scene
- **WHEN** the owner starts a simulation and all actions succeed
- **THEN** nine ordered character events expose action, dialogue where applicable and result, with the task marked succeeded after round three

#### Scenario: Missing confirmation or forged parameters
- **WHEN** confirmation/configuration is absent, or the caller requests different character count, rounds or unknown parameters
- **THEN** no task starts and an actionable precondition error or 422 is returned respectively

#### Scenario: Bound history
- **WHEN** configuration or confirmed preparation later changes
- **THEN** the existing simulation still displays its original input versions and unchanged event history

### Requirement: S2 Independent bounded agents
The plot architect SHALL coordinate scene and turn order without rewriting confirmed facts or adding autonomous characters. Each character SHALL receive only public information, its own private knowledge and visible simulation history, and act according to its own identity and goals. The architect SHALL NOT leak private information through coordination.

#### Scenario: Private knowledge
- **WHEN** character A owns a marked secret unavailable to B
- **THEN** B's model input and architect instructions delivered to B exclude that secret unless a validated disclosure makes it visible

#### Scenario: Invalid architect output
- **WHEN** coordination changes confirmed world rules, invents a new acting character or omits/duplicates a character turn
- **THEN** it is rejected and bounded retries cannot publish it as accepted coordination

### Requirement: S3 Validated progressive events
The system SHALL validate structured model output, actor identity, references, visibility and consistency against confirmed facts and accepted events before persistence and display. Only accepted events SHALL enter subsequent contexts. Model output SHALL NOT execute tools or mutate state directly.

#### Scenario: Progressive display
- **WHEN** a character event passes validation
- **THEN** it is atomically persisted with a stable sequence number and displayed before the entire simulation finishes

#### Scenario: Contradiction or malformed action
- **WHEN** an action contradicts confirmed facts, uses unauthorized knowledge or fails structural validation
- **THEN** it is not displayed as an accepted event and cannot influence subsequent actions; exhausted retries fail the task while preserving earlier events

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
Completion SHALL require real local browser/provider evidence for the entire configuration-to-simulation flow, and 10–20 fixed evaluations covering role goals, privacy, injection, fact conflicts and rejection. Evidence SHALL distinguish mock checks, actual model results and unverified external behavior.

#### Scenario: Joint delivery
- **WHEN** the three linked changes are declared jointly complete
- **THEN** evidence demonstrates persistent configuration after restart, one-idea generation, confirmation, real progressive simulation, refresh recovery, stop and two-user isolation, with make check passing and no claim of chapter adoption or public deployment
