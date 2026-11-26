# auto-record-rules Specification

## Purpose

Allows users to create persistent channel and program-title auto-record rules (series passes) that automatically schedule matching upcoming EPG broadcasts into `uhf-server` while avoiding unintended scheduling conflicts.

## Requirements

### Requirement: Auto-record rule management
The system SHALL allow users to create, edit, enable/disable, manually trigger, and delete auto-record rules specifying a target channel, program title pattern, match mode (`starts_with` default, `contains`, or `exact`), optional pre/post padding overrides, and a `skip_if_conflict` toggle ("Do not schedule on top of existing programming", enabled by default).

#### Scenario: Create an auto-record rule from the Auto-Record tab
- **WHEN** a user selects a channel, enters a program title such as `Capital`, keeps `Do not schedule on top of existing programming` checked, and saves the rule
- **THEN** the system persists the rule, defaults `skip_if_conflict` to true and `match_mode` to `starts_with`, and evaluates upcoming EPG airings immediately or on demand

#### Scenario: Create an auto-record rule in one click from EPG selection
- **WHEN** a user opens the scheduling modal from an EPG Grid program or Program Search result and chooses "Auto-record every airing on this channel"
- **THEN** the system creates an auto-record rule pre-populated with that channel, program title, padding, and conflict-avoidance enabled by default, and runs it against upcoming EPG matches

#### Scenario: Search programs directly in the Auto-Record tab
- **WHEN** a user uses the always-visible program search box in the Auto-Record tab
- **THEN** searching with a channel selected filters strictly to upcoming programs on that selected channel (accent-insensitively), and searching before selecting a channel searches across all visible channels so clicking a result selects both the program title and its channel

#### Scenario: Pause or delete a rule
- **WHEN** a user disables or deletes an auto-record rule
- **THEN** subsequent evaluations stop booking new airings for that rule while leaving already-scheduled `uhf-server` recordings intact

### Requirement: Case- and accent-insensitive EPG title matching
The system SHALL match each enabled rule against deduplicated upcoming EPG programs on that rule's channel using Unicode accent-folded, case-insensitive comparison according to the rule's `match_mode`.

#### Scenario: Match French title with season suffix and missing accents
- **WHEN** a rule is configured with title `enquete exclusive` and mode `starts_with` on channel `|FR| M6 UHD`
- **THEN** it matches `Enquête exclusive` and `Enquête exclusive : Spéciale` on that channel while ignoring airings on other channels

#### Scenario: Exact match avoids prefix collisions
- **WHEN** a rule is configured with title `Capital` and mode `exact`
- **THEN** it matches `Capital` and does not match `Capital Santé - S01E08`

### Requirement: Conflict avoidance with back-to-back padding edge trimming
The system SHALL honor `skip_if_conflict` using the configured simultaneous stream limit (`max_simultaneous_streams`, default `1`), automatically trimming only overlapping pre/post padding edges on adjacent non-overlapping broadcasts while skipping any airing whose core broadcast overlap would exceed the stream limit.

#### Scenario: Back-to-back episodes both scheduled by trimming shared padding
- **WHEN** two matching broadcasts air back-to-back (`22:15–23:30` and `23:30–00:45`) with 2-minute pre-padding, 5-minute post-padding, `skip_if_conflict` enabled, and `max_simultaneous_streams` set to `1`
- **THEN** the system trims the shared padding edge so the first recording ends at `23:30` and the second starts at `23:30`, scheduling both without exceeding 1 simultaneous stream

#### Scenario: Trim padding touching an existing timer edge
- **WHEN** a matching broadcast airs `21:00–22:00` with 2-minute pre-padding and an existing `uhf-server` timer ends at `21:00` (with `max_simultaneous_streams = 1`)
- **THEN** the system trims the new recording's pre-padding to start at `21:00` and schedules the full broadcast

#### Scenario: Skip airing when actual broadcast overlaps beyond stream limit
- **WHEN** a matching broadcast's core air time (`20:00–21:30`) overlaps an existing `uhf-server` scheduled or active recording (`20:30–22:00`), `skip_if_conflict` is enabled, and `max_simultaneous_streams` is `1`
- **THEN** the system skips scheduling that airing and records a conflict entry naming the overlapping recording

#### Scenario: Allow concurrent scheduling when stream limit permits or checkbox is unchecked
- **WHEN** a matching broadcast overlaps an existing recording and either peak concurrency remains within `max_simultaneous_streams` or `skip_if_conflict` is unchecked
- **THEN** the system schedules the recording into `uhf-server`

### Requirement: Idempotent rule history and manual override
The system SHALL record each evaluated airing (`scheduled`, `skipped_conflict`, or `error`) in persistent history so repeated EPG syncs never double-book airings or re-schedule airings manually deleted by the user, while tolerating minor EPG start-time shifts (±15 minutes) and offering a manual Force Schedule override for skipped conflicts.

#### Scenario: Re-running evaluation does not duplicate or re-add deleted airings
- **WHEN** an airing was previously scheduled by a rule (even if the user later deletes it in Recordings) and a subsequent EPG sync runs
- **THEN** the rule evaluator recognizes the handled slot (within ±15 minutes on that channel) and does not schedule a duplicate recording

#### Scenario: Force schedule a conflict-skipped airing
- **WHEN** a user clicks "Force Schedule" on a `skipped_conflict` history entry
- **THEN** the system submits that airing directly to `uhf-server` bypassing the conflict skip and updates the history entry to `scheduled`
