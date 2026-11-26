# uhf-server-integration Specification

## Purpose

Connects the scheduler to a user-configured `uhf-server` instance, managing authentication, recording lifecycle operations, status visibility, and metadata compatibility with the Apple TV UHF client.

## Requirements

### Requirement: Configurable UHF server connection and authentication
The system SHALL allow users to configure the base URL and optional server password for a `uhf-server` instance, test connectivity, and authenticate automatically using `uhf-server`'s supported password-session mechanism.

#### Scenario: Connect to a passwordless UHF server
- **WHEN** a user saves a `uhf-server` URL whose `/auth/methods` response reports `password_required: false`
- **THEN** the system opens a session via `/auth/password-login` without requiring a password and reports the connection as healthy

#### Scenario: Connect to a password-protected UHF server
- **WHEN** a user saves a `uhf-server` URL whose `/auth/methods` response reports `password_required: true` along with a valid server password
- **THEN** the system authenticates via `/auth/password-login`, extracts the session bearer token (`id_token`, `access_token`, or `token`), stores it on the server side, and never exposes the raw password back to the browser

#### Scenario: Automatic session renewal after expiry or password change
- **WHEN** a proxied request to `uhf-server` returns HTTP 401 Unauthorized
- **THEN** the system re-authenticates once using the stored server credentials and retries the original request before surfacing an authentication error to the user

#### Scenario: Unreachable UHF server
- **WHEN** `uhf-server` cannot be reached or times out
- **THEN** the system returns a clear connectivity error without crashing or losing local catalog or EPG data

### Requirement: Apple TV UHF metadata and playlist compatibility
The system SHALL populate recording `description`, `parent_id`, and `metadata` fields (`name`, `categoryName`, `originalCategoryName`, `thumbnailURL`, and optional `playlistID`) when creating recordings in `uhf-server` so recordings display and group properly in the Apple TV UHF app.

#### Scenario: Auto-detect playlist ID from existing recordings
- **WHEN** an IPTV source does not have an explicit `playlistID` configured and `uhf-server` already contains recordings from the same stream host
- **THEN** the system offers or applies the `playlistID` detected from those existing recordings

#### Scenario: Recording created without detected playlist ID
- **WHEN** no matching `playlistID` exists in `uhf-server` or source settings
- **THEN** the system still creates the recording with channel name, category, and logo metadata omitted only for missing fields

### Requirement: Recordings list and lifecycle management
The system SHALL display recordings retrieved from `uhf-server` grouped or filterable by status (`scheduled`, `recording`, `completed`, `failed`, `cancelled`), expose failure causes (`error_code`, `error`) and progress (`recorded_seconds`), and provide safe lifecycle actions plus direct links to `uhf-server` for playback or download.

#### Scenario: Stop an in-progress recording
- **WHEN** a user stops a recording with status `recording`
- **THEN** the system calls `PATCH /dvr/recordings/{id}/cancel` and refreshes the recording list

#### Scenario: Stop a recurring scheduled series
- **WHEN** a user cancels a scheduled recording that has `recurrence_days` configured
- **THEN** the system calls `PATCH /dvr/recordings/{id}/cancel-recurrence` so the series stops rather than spawning a replacement occurrence

#### Scenario: Delete a one-off scheduled or terminal recording
- **WHEN** a user deletes a non-recurring `scheduled` recording or a `completed`, `failed`, or `cancelled` recording
- **THEN** the system calls `DELETE /dvr/recordings/{id}` and removes it from the view

#### Scenario: Edit a scheduled recording via safe replacement
- **WHEN** a user edits the title, start time, duration, or recurrence of a recording whose status is `scheduled`
- **THEN** the system creates the updated recording first via `POST /dvr/recordings` and only removes the original timer (via `cancel-recurrence` if recurring, or `DELETE` if one-off) after creation succeeds

#### Scenario: Edit blocked for non-scheduled recording
- **WHEN** a recording's status is no longer `scheduled` (for example, it already started recording)
- **THEN** the system disallows time/duration replacement and informs the user that only scheduled timers can be edited

#### Scenario: Open or download completed recording via UHF server
- **WHEN** a user selects Watch or Download on a completed recording
- **THEN** the system provides a direct link to `uhf-server`'s recording stream or download interface without implementing an in-app video player

### Requirement: UHF server automatic network discovery
The system SHALL provide on-demand discovery of `uhf-server` instances on the local network via Zeroconf/mDNS (`_uhf-server._tcp.local.`), localhost port probes (`8085`, `8000`), and Docker host-gateway probes (`host.docker.internal:8085`, `host.docker.internal:8000`), verifying each candidate via `/auth/methods` and prioritizing LAN IPv4 addresses over container bridge interfaces.

#### Scenario: Discover UHF server via mDNS and localhost probe
- **WHEN** a user clicks Auto-Detect next to UHF Server URL in Settings
- **THEN** the system scans `_uhf-server._tcp.local.` and probes localhost ports, verifies responding `uhf-server` instances, prefers LAN IPv4 addresses over `172.x` bridge interfaces, and presents clickable server choices with their password-required status

#### Scenario: Discover host-running UHF server from Docker Desktop on macOS or Windows
- **WHEN** `uhf-scheduler` runs inside a Docker container on macOS or Windows where `uhf-server` runs natively on the host
- **THEN** Auto-Detect probes `http://host.docker.internal:8085` and `http://host.docker.internal:8000` via `/auth/methods` and surfaces the responding host `uhf-server` as a clickable choice
