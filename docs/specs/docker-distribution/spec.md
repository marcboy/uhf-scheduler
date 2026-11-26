# docker-distribution Specification

## Purpose

Packages `uhf-scheduler` as a multi-architecture Docker image with automated GHCR publishing, idempotent one-command installers that preserve persistent user data across updates, and in-app update notifications.

## Requirements

### Requirement: Multi-architecture Docker packaging and automated publishing
The system SHALL provide a production multi-stage container image supporting `linux/amd64` and `linux/arm64` with a `/data` volume, configurable `PORT`, container health check against `/api/health`, and automated CI/CD publishing to `ghcr.io/marcboy/uhf-scheduler` after tests pass, keeping in-progress `main` builds isolated from production `:latest` releases.

#### Scenario: In-progress push to main publishes preview `:edge` without affecting production
- **WHEN** changes are pushed to `main` (without a release tag)
- **THEN** the CI workflow runs the backend test suite and frontend build, builds `linux/amd64` and `linux/arm64` images, and pushes `:edge` and `:sha-<short>` tags to GHCR without updating `:latest` or creating a GitHub Release

#### Scenario: Stable release tag publishes production `:latest` and GitHub Release
- **WHEN** a stable version tag `vX.Y.Z` (containing no prerelease `-` suffix) is pushed
- **THEN** the CI workflow runs the backend test suite and frontend build, builds `linux/amd64` and `linux/arm64` images, pushes `:latest`, `:X.Y.Z`, `:X.Y`, and `:sha-<short>` tags to GHCR, and creates a GitHub Release (which triggers in-app update notifications for existing installations)

#### Scenario: Prerelease tag does not overwrite production `:latest`
- **WHEN** a prerelease version tag such as `vX.Y.Z-rc.1` is pushed
- **THEN** the CI workflow pushes the versioned prerelease image tag and creates a GitHub prerelease without updating `:latest` or triggering stable update notifications

#### Scenario: Container health check reports readiness
- **WHEN** the container is running
- **THEN** Docker's built-in health check probes `/api/health` and marks the container healthy once the web server responds HTTP 200

### Requirement: Idempotent one-command install and data-preserving updates
The system SHALL provide one-command install scripts (`install.sh` for Linux/Raspberry Pi/macOS and `install.ps1` for Windows PowerShell) that configure Docker Compose with a persistent host `./data:/data` mount, start the service, and support re-running or calling `update.sh`/`update.ps1` to upgrade the container image without deleting or overwriting `scheduler.db`.

#### Scenario: Fresh one-command installation on Linux or Raspberry Pi
- **WHEN** a user runs the `install.sh` one-liner on a Linux host with Docker installed
- **THEN** the script creates `docker-compose.yml` with `network_mode: host` (enabling LAN mDNS discovery) and `./data:/data`, pulls `ghcr.io/marcboy/uhf-scheduler:latest`, starts the container, waits for `/api/health`, and prints the reachable URL

#### Scenario: Update preserves all settings, channels, EPG, and Auto-Record rules
- **WHEN** a user re-runs the installer or executes `docker compose pull && docker compose up -d` against an existing installation
- **THEN** the container restarts on the new image while retaining the mounted `./data/scheduler.db` intact so all settings, IPTV sources, favorites, EPG data, Auto-Record rules, and rule history remain preserved

#### Scenario: Fresh one-command installation on macOS or Windows
- **WHEN** a user runs `install.sh` on macOS or `install.ps1` on Windows PowerShell
- **THEN** the script generates `docker-compose.yml` with port mapping (`8086:8086`), `host.docker.internal` host-gateway mapping, and persistent `./data:/data` volume, pulls the image, and starts the container

### Requirement: In-app update availability notifications
The system SHALL periodically check the latest published GitHub Release (cached server-side to avoid rate limits and failing silently offline) via `GET /api/system/update-check` and display a subtle notification in the UI with update instructions whenever a newer version is available.

#### Scenario: Newer version available on GitHub Releases
- **WHEN** the latest GitHub Release tag is newer than the running `APP_VERSION`
- **THEN** `GET /api/system/update-check` reports `update_available: true` with `latest_version` and `release_url`, and the UI displays an update badge linking to the release and copyable update command

#### Scenario: Already up to date or offline
- **WHEN** the running version matches or exceeds the latest release, or GitHub cannot be reached
- **THEN** the app continues operating normally without errors or intrusive banners
