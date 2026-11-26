# epg-guide Specification

## Purpose

Ingests optional XMLTV electronic program guide feeds from IPTV providers or custom external URLs, maps programs to channels, and powers a full interactive EPG grid for discovering and scheduling broadcasts.

## Requirements

### Requirement: Optional multi-source XMLTV EPG configuration
The system SHALL function fully with zero EPG sources configured, while allowing users to enable automatic Xtream provider XMLTV feeds and/or add one or more custom XMLTV URLs supporting both uncompressed `.xml` and gzip-compressed `.xml.gz` payloads.

#### Scenario: Operate with no EPG sources
- **WHEN** no EPG sources are configured
- **THEN** manual recording scheduling and recordings management remain fully operational and the EPG Grid displays a prompt linking to EPG settings

#### Scenario: Add a custom multi-day XMLTV URL
- **WHEN** a user adds an external XMLTV URL (`.xml` or `.xml.gz`) covering 7–14 days
- **THEN** the system downloads and ingests the feed and exposes programs across the full date range covered by that feed

#### Scenario: Automatic provider XMLTV for Xtream source
- **WHEN** a user enables provider EPG on an Xtream Codes source
- **THEN** the system fetches `xmltv.php` using that source's credentials without requiring the user to paste credentials into a separate URL field

### Requirement: Streaming XMLTV ingestion and retention
The system SHALL parse XMLTV feeds incrementally using streaming XML parsing into an indexed local database, prune expired past programs, and isolate failures per EPG source.

#### Scenario: Stream-parse a large XMLTV feed safely
- **WHEN** an XMLTV feed of 50 MB or larger (or its `.xml.gz` equivalent) is synced
- **THEN** the system parses `<channel>` and `<programme>` elements incrementally with bounded memory usage and commits programs in batches indexed by channel EPG ID and UTC start/end timestamps

#### Scenario: Prune outdated EPG entries
- **WHEN** an EPG sync completes
- **THEN** programs that ended more than a configurable retention window in the past (default 24 hours) are removed to keep the local database compact

### Requirement: Channel-to-EPG mapping
The system SHALL automatically match IPTV channels to EPG guide channels by `epg_channel_id` (case-insensitive) and allow optional per-channel manual EPG ID overrides when an external XMLTV feed uses different identifiers.

#### Scenario: Automatic EPG ID match
- **WHEN** a channel's `epg_channel_id` matches an XMLTV `<channel id="...">` entry
- **THEN** programs for that XMLTV channel appear on that channel's row in the EPG Grid

#### Scenario: Manual EPG ID override
- **WHEN** a user maps a channel to a specific XMLTV channel ID from an external guide source
- **THEN** the system uses the override to display programs for that channel in the EPG Grid and preserves the override across subsequent catalog syncs

### Requirement: Full interactive category-scoped EPG grid
The system SHALL provide a 24-hour local-day horizontal timeline EPG grid scoped to Favorites or a selected category, deduplicating multi-source EPG feeds, overlaying `uhf-server` recordings, persisting the last selected group, and opening a pre-filled scheduling form on program click.

#### Scenario: Browse a full 24-hour day across multiple days
- **WHEN** a user opens the EPG Grid, selects a category or Favorites, and navigates across days using day buttons, Prev/Next Day, or the date picker
- **THEN** the grid fetches the full 24-hour window (`00:00` – `24:00` local time) for the visible channels, renders program blocks aligned to the user's local timezone, and remembers the selected category across reloads

#### Scenario: Deduplicate overlapping multi-source EPG feeds
- **WHEN** both a custom XMLTV source (such as an 8-day external guide) and an automatic provider EPG source are enabled and cover the same channel
- **THEN** the system prefers the custom XMLTV source for that channel, falls back to the provider EPG for channels not covered by custom feeds, and collapses near-duplicate entries starting within 5 minutes

#### Scenario: Scheduled recordings highlighted on the grid
- **WHEN** a channel has an upcoming or active recording in `uhf-server` that overlaps a displayed time window
- **THEN** the EPG Grid visually indicates the scheduled/recording slot on that channel's row

#### Scenario: Click a program to schedule
- **WHEN** a user clicks an upcoming or currently airing program in the EPG Grid
- **THEN** the system opens the scheduling modal pre-populated with that channel, program title, start time, duration, and default padding, allowing the user to adjust any field before submitting

### Requirement: Multi-day EPG program search with mirror-stream grouping
The system SHALL allow users to search upcoming EPG programs across all indexed future days scoped to Favorites, a selected category, or all visible categories, defaulting to title-only matching with an optional toggle to include program descriptions, grouping mirror streams of the same broadcast into one row with a stream selector, and offering one-click scheduling.

#### Scenario: Search upcoming programs by title with grouped mirror streams
- **WHEN** a user enters a program search query of at least 2 characters while multiple mirror channels share the same broadcast and EPG ID
- **THEN** the system returns matching upcoming broadcasts across all future days with mirror channels grouped into a single row and selectable via a channel dropdown before clicking Schedule

#### Scenario: Opt-in description search
- **WHEN** a user enables the "Include descriptions" option on program search
- **THEN** the system matches the query against both program titles and descriptions while ranking title matches first

### Requirement: Automatic periodic EPG synchronization
The system SHALL check elapsed time since the last EPG sync on startup and periodically in the background, refreshing enabled EPG sources whenever `sync_interval_hours` has elapsed, and automatically evaluate enabled auto-record rules after every scheduled or manual EPG sync.

#### Scenario: Catch up on missed EPG sync after restart
- **WHEN** the service starts and an enabled EPG source has never synced or last synced longer ago than `sync_interval_hours`
- **THEN** the background scheduler triggers an EPG refresh promptly rather than waiting a full interval, and then evaluates enabled auto-record rules

#### Scenario: Manual EPG sync triggers auto-record evaluation
- **WHEN** a user triggers a manual EPG source sync from Settings or clicks "Sync & Run Rules Now"
- **THEN** the system evaluates all enabled auto-record rules once the EPG sync completes and reports newly scheduled and skipped counts

### Requirement: Overlap-only EPG duplicate collapsing and per-channel search
The system SHALL collapse near-duplicate EPG entries on the same channel only when their time intervals genuinely overlap (preserving sequential short broadcasts such as 5-minute shows immediately before a following program) and SHALL support accent-insensitive upcoming program search scoped strictly to a single selected channel.

#### Scenario: Short 5-minute broadcast preserves following program
- **WHEN** a channel's EPG has a 5-minute program (`20:05–20:10`) immediately followed by a feature broadcast (`20:10–22:10`)
- **THEN** both programs appear intact in the EPG Grid, program search, and auto-record evaluation

#### Scenario: Per-channel accent-insensitive program search
- **WHEN** a client queries `/api/epg/search` with a specific `channel_id` and query `enquete`
- **THEN** the system returns only matching upcoming broadcasts on that channel (such as `Enquête exclusive`) without including other channels
