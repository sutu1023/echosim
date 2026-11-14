# Spec Delta

## Purpose

为创作者提供一张可理解、可追溯的当前故事图谱，描述确认设定和已采纳剧情中的人物、地点、关键物品及关系；在采纳时一致更新，帮助持续创作而不将候选剧情或角色猜测误当成正式事实。

## ADDED Requirements

### Requirement: G1 Confirmed baseline
The system SHALL build an initial graph from the current confirmed settings with exactly the confirmed characters plus evidenced places, key objects and relationships. Unconfirmed candidates SHALL NOT seed the graph. Initial construction SHALL be durable, bounded and observable; new simulations SHALL wait until their confirmed baseline is ready.

#### Scenario: Initial graph
- **WHEN** an owner confirms settings containing three characters, a lighthouse and a key
- **THEN** construction is queued without model calls inside confirmation, and the resulting graph contains those supported entities and sourced relationships

#### Scenario: Missing or failed baseline
- **WHEN** no settings are confirmed or baseline construction fails
- **THEN** the UI shows an explicit empty or retryable error state, no fabricated graph appears and simulation start returns an actionable precondition error

#### Scenario: Reconfirmation before canon
- **WHEN** a permitted new setting version is confirmed before first adoption
- **THEN** its new baseline is built, the earlier graph is marked out of date until replacement and late results for old settings cannot replace it

### Requirement: G2 Source backed stable graph
Every graph entity and relationship SHALL have a stable work-scoped identity and evidence in a confirmed field or adopted event segment. Uncertain claims SHALL NOT become definite relationships. Updates SHALL preserve identity, current state and historical evidence without simultaneously asserting incompatible current facts.

#### Scenario: Key changes holder
- **WHEN** adoption transfers the existing key from A to B
- **THEN** the key node remains the same, its current holder becomes B, the obsolete holder relation is no longer current and both changes remain traceable

#### Scenario: Unsupported inference
- **WHEN** generated extraction invents a relationship or refers to nonexistent source text
- **THEN** validation rejects that output without publishing the relation; exhausted retries leave the previous valid graph intact

#### Scenario: Ambiguous identity
- **WHEN** two same-named objects cannot be reliably matched from the source
- **THEN** they are not silently merged and the candidate update is rejected for safe retry or reported as an actionable extraction conflict

### Requirement: G3 Adoption only publication
After initial baseline construction, the visible graph SHALL represent only the latest committed canon revision. Simulation events, playback position and terminal status SHALL NOT publish graph changes. Successful adoption SHALL expose one complete graph revision consistent with adopted events and memories; failed adoption SHALL expose none of its candidate changes.

#### Scenario: Whole task then adoption
- **WHEN** a simulation finishes and awaits a decision
- **THEN** the graph remains unchanged; only successful explicit adoption makes the complete new revision visible

#### Scenario: Stop failure and discard
- **WHEN** a simulation stops, fails or is discarded without adoption
- **THEN** its accepted events do not change graph nodes, edges or the displayed revision

#### Scenario: Atomic publication failure
- **WHEN** adoption checking, factual validation or transactional commit fails
- **THEN** the previous graph and canon stay intact with an error state and no partially applied relationships

### Requirement: G4 Single readable view
The UI SHALL show one current graph alongside the narrative with distinct people/place/object labels, readable relationships and inspectable sources. It SHALL NOT offer branch graphs, per-event graph playback or graph editing. Selection of historical simulations SHALL NOT change the current graph.

#### Scenario: Inspect evidence
- **WHEN** the owner selects an entity or relationship
- **THEN** its current attributes and relevant confirmed-field or adopted-event evidence are readable without requiring internal identifiers or visibility metadata

#### Scenario: New revision fetch
- **WHEN** adoption succeeds and the new graph request is slow, fails or responses arrive out of order
- **THEN** the old graph is labeled updating or stale, retry is available and only a complete newer matching-work revision replaces it

#### Scenario: Keyboard and larger graph
- **WHEN** the owner uses a keyboard or the graph cannot fit the viewport
- **THEN** labeled focusable entities, readable relation details and fit/zoom/pan controls permit inspection without losing nodes or silently truncating facts

### Requirement: G5 Creator ownership and role separation
Graph reads, construction retries and evidence lookup SHALL enforce owner access, returning 401 for unauthenticated access and the same 404 for other-user and missing resources. A creator's full graph SHALL NOT be passed unfiltered into role or architect contexts. Graph text SHALL remain inert and private source text SHALL NOT enter logs.

#### Scenario: Other-user graph
- **WHEN** B requests A's graph, source or construction task
- **THEN** the response is indistinguishable from missing data and no construction or other write occurs

#### Scenario: Creator sees a secret
- **WHEN** the graph contains A's secret relationship known only to A
- **THEN** the creator can inspect it while B's next simulation context and public architect input exclude it

### Requirement: G6 Joint evidence
Delivery SHALL include real provider and browser evidence for initial graph construction, unchanged graph during playback, adoption-driven updates and a subsequent consistent simulation. Fixed extraction evaluations SHALL cover provenance, identity, changes, uncertainty, injection and secrets, with actual failures retained.

#### Scenario: Full creative loop
- **WHEN** the three changes are declared jointly complete
- **THEN** evidence shows initial graph, natural interaction, explicit adoption, one consistent updated graph, continuation from canon, discard, stop/failure behavior and two-user isolation with make check and relevant integration checks passing
