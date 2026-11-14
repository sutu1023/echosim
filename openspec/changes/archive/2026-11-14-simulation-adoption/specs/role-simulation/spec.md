# Spec Delta

## ADDED Requirements

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
