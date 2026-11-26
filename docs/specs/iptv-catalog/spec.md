# iptv-catalog Specification

## Purpose

Manages configurable IPTV provider sources, synchronizes and indexes large live-channel catalogs locally, and provides fast channel discovery, category filtering, and favorites management.

## Requirements

### Requirement: Multi-source IPTV configuration
The system SHALL allow users to add, edit, enable/disable, and delete multiple IPTV sources supporting both Xtream Codes credentials (server URL, username, password) and M3U playlist URLs.

#### Scenario: Add an Xtream Codes source
- **WHEN** a user saves an Xtream Codes source with server URL, username, and password
- **THEN** the system persists the source, masks the password in subsequent API responses, and triggers or allows an immediate channel sync

#### Scenario: Add an M3U playlist source
- **WHEN** a user saves an M3U source with a valid HTTP(S) playlist URL
- **THEN** the system persists the source and parses `#EXTINF` entries (name, `tvg-id`, `tvg-logo`, `group-title`, and stream URL) during sync

#### Scenario: Remove an IPTV source
- **WHEN** a user deletes an IPTV source
- **THEN** the system removes that source's cached categories and channels without affecting existing recordings already stored in `uhf-server`

### Requirement: Local channel synchronization and indexing
The system SHALL fetch live categories and channels from enabled IPTV sources using browser-compatible HTTP `User-Agent` headers, store them in a local indexed database without holding unbounded raw responses in memory, and record sync timestamps and descriptive error messages (including exception class names when underlying network errors carry empty messages) per source.

#### Scenario: Sync a large Xtream catalog
- **WHEN** an Xtream source containing 10,000 or more live channels is synced
- **THEN** the system fetches categories and live streams via `player_api.php` with a standard browser `User-Agent`, stores normalized channel records with stream URLs and `epg_channel_id`, and reports the synced count and timestamp

#### Scenario: Sync failure preserves prior catalog
- **WHEN** a provider is temporarily unreachable or resets the connection during a refresh
- **THEN** the system records a non-empty descriptive sync error for that source and retains the previously cached channels so scheduling remains usable

#### Scenario: Periodic background sync
- **WHEN** an automatic sync interval is configured
- **THEN** the system refreshes enabled IPTV sources on schedule without overlapping concurrent sync runs for the same source

### Requirement: Fast channel search, favorites, and category visibility
The system SHALL allow users to search channels by name, filter by source or category, persist the last selected channel group and favorites filter in browser storage, mark channels as favorites, and hide unwanted categories across both the Manual Schedule and EPG Grid views.

#### Scenario: Search channels by partial name
- **WHEN** a user types a channel query (such as `Canal+`) into the channel picker
- **THEN** the system returns matching channels from the local index with category, source, and logo metadata

#### Scenario: Favorite channels surfaced first
- **WHEN** a user marks one or more channels as favorites
- **THEN** those channels are accessible through a dedicated Favorites filter and appear prioritized in channel selection

#### Scenario: Hide unwanted categories
- **WHEN** a user hides one or more channel categories in settings or category management
- **THEN** channels belonging to hidden categories are excluded from default category lists and EPG Grid views unless the user explicitly chooses to show hidden categories

### Requirement: Channel credentials never leaked unnecessarily to UI
The system SHALL keep provider passwords masked in settings responses and construct or resolve stream URLs on the backend when scheduling recordings into `uhf-server`.

#### Scenario: Retrieve settings for display
- **WHEN** the web UI fetches configured IPTV sources
- **THEN** provider passwords are returned as masked placeholders or boolean "configured" indicators rather than plaintext secrets
