# Chapter drafts

## Purpose

让创作者把一次推演整理成可阅读的章节，保存自己的标题和正文，并把保存版本带出作品。章节支持创作者在确认本轮剧情前改写情节，也支持在先采纳后成章时忠实呈现已确认事件。

## ADDED Requirements

### Requirement: Eligible chapter generation
The system SHALL support owner-initiated durable, streaming chapter generation for a pending or adopted nonlegacy simulation that succeeded or explicitly stopped with at least one validated event. Generated prose SHALL preserve key actions, causal relationships and the ending of its source, including an unresolved ending.

#### Scenario: Generate and reconnect
- **WHEN** the owner requests a chapter and refreshes during generation
- **THEN** submission returns 202, prose appears progressively, the task continues and the owner can retrieve its completed title and text after reconnecting.

#### Scenario: Ineligible history
- **WHEN** generation is requested for a running, failed, empty, discarded or legacy read-only result
- **THEN** the action is unavailable and the server rejects it without invoking a model.

### Requirement: One saved draft per simulation
The system SHALL keep one current saved title and body per simulation and export that saved version as Markdown. Regeneration SHALL preview a candidate and require explicit replacement.

#### Scenario: Edited draft survives regeneration
- **WHEN** the owner reopens an edited chapter, closes a regeneration preview, or generation fails
- **THEN** the saved title and body remain available and exportable; accepting a complete replacement updates the saved chapter only if its version is still current.

#### Scenario: Export an edit
- **WHEN** the owner edits the title and a line of dialogue, saves and downloads the chapter
- **THEN** the downloaded Markdown contains the saved title and edited dialogue.

### Requirement: Editing follows the simulation decision
The owner SHALL be able to change pending-round plot in the draft without advancing canon. After adoption the owner MAY generate a chapter or polish wording consistent with the adopted events. Discard SHALL freeze the last saved draft.

#### Scenario: Polish adopted prose
- **WHEN** the adopted event gives the key to C and the owner changes “递给 C” to “放进 C 掌心”
- **THEN** saving succeeds; changing the recipient to B instead reports a factual conflict and preserves the saved version.

#### Scenario: Discard a draft
- **WHEN** the owner chooses discard with a saved draft
- **THEN** the UI explains the read-only consequence before confirmation; after discard the owner can read and export that draft but cannot edit, regenerate or replace it.

### Requirement: Chapter ownership
All chapter, preview, progress and export operations SHALL enforce user and work ownership.

#### Scenario: Foreign chapter
- **WHEN** an unauthenticated caller requests chapter data or a signed-in user accesses another user's work, simulation, draft or task
- **THEN** the former receives 401 and the latter the same 404 as an absent resource, without writes, model calls or content disclosure.
