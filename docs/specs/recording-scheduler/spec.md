# recording-scheduler Specification

## Purpose

Enables users to schedule one-off or recurring IPTV recordings at any arbitrary future date and time (or pre-filled from the EPG), applying configurable padding, timezone-aware recurrence, and simultaneous-stream conflict warnings.

## Requirements

### Requirement: Arbitrary-future manual recording scheduling
The system SHALL allow users to schedule a recording for any channel at any future date and start time with no 24/48-hour horizon restriction, specifying duration (or end time) and an optional custom recording title.

#### Scenario: Schedule a manual recording weeks in advance
- **WHEN** a user selects a channel, picks a date 30 days in the future, enters a start time and duration (for example, 90 minutes), and submits
- **THEN** the system resolves the channel's stream URL and Apple TV metadata on the backend, submits a valid `POST /dvr/recordings` request to `uhf-server`, and confirms the scheduled recording

#### Scenario: Specify end time instead of duration
- **WHEN** a user enters a start time and an end time (including across midnight)
- **THEN** the system computes the positive `duration_seconds` accurately and displays the resulting duration before submission

#### Scenario: Default title when omitted
- **WHEN** a user submits a manual schedule without entering a custom title
- **THEN** the system generates a readable default name using the channel name and local date/time

#### Scenario: Reject invalid schedule inputs
- **WHEN** a user submits a recording whose computed end time is in the past or whose duration is less than or equal to zero
- **THEN** the system blocks submission and displays a clear validation error

### Requirement: Configurable pre-start and post-end padding
The system SHALL support default global pre-padding and post-padding minutes in settings and allow per-recording padding adjustments before submission.

#### Scenario: Apply padding to a scheduled recording
- **WHEN** a program runs from 20:00 to 21:00 with 2 minutes pre-padding and 5 minutes post-padding
- **THEN** the system submits `start_time` at 19:58 and `duration_seconds` of 4020 (67 minutes) to `uhf-server`, clamping `start_time` to now if pre-padding would push a starting-soon recording into the past

### Requirement: DST-aware weekly recurring recordings
The system SHALL allow users to configure weekly recurrence (`recurrence_days` for Monday through Sunday) and an optional `recurrence_end_date`, always transmitting the user's IANA `timezone` to `uhf-server`.

#### Scenario: Schedule a weekly recurring recording
- **WHEN** a user selects channel XYZ, start time Sunday 21:00, duration 2 hours, repeat on Sundays, and submits from a browser in `America/Los_Angeles`
- **THEN** the system sends `recurrence_days: [6]` and `timezone: "America/Los_Angeles"` to `uhf-server` along with any optional `recurrence_end_date`

### Requirement: Simultaneous stream overlap detection
The system SHALL allow users to configure a maximum simultaneous stream limit per source (or globally, default 1) and warn users before submitting when a proposed recording overlaps existing `scheduled` or `recording` timers beyond that limit.

#### Scenario: Overlap warning displayed before confirmation
- **WHEN** a user configures a new recording whose padded time range overlaps existing `uhf-server` scheduled/active recordings such that concurrent recordings would exceed the configured max-streams limit
- **THEN** the system displays a clear warning listing the conflicting recording(s) and time overlap while still allowing the user to proceed intentionally if they choose

#### Scenario: No false warning for non-overlapping adjacent timers
- **WHEN** a new recording starts at or after an existing recording's end time
- **THEN** the system does not flag an overlap conflict
