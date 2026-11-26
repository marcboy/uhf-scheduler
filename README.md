# UHF Scheduler

*a 6-kids effort*

A standalone web application for scheduling arbitrary-future IPTV DVR recordings (manual date/time/duration, full 24-hour EPG grid, multi-day program search, and EPG-driven Auto-Record Rules) directly into **`uhf-server`** — removing the 24/48-hour guide horizon limit of the Apple TV UHF client while keeping native Apple TV playback and recording resilience.

## 1-Command Install (Docker — Recommended)

Prebuilt multi-architecture images (`linux/amd64` + `linux/arm64`) are published to `ghcr.io/marcboy/uhf-scheduler:latest`. All settings, channels, EPG data, and Auto-Record rules live in `./data/scheduler.db` and are preserved across updates.

### Linux / Raspberry Pi / macOS
```sh
curl -fsSL https://raw.githubusercontent.com/marcboy/uhf-scheduler/main/install.sh | sh
```

### Windows (PowerShell)
```powershell
irm https://raw.githubusercontent.com/marcboy/uhf-scheduler/main/install.ps1 | iex
```

### Updating Later
Re-run the same install command above, or run the generated update script inside `~/uhf-scheduler`:
```sh
cd ~/uhf-scheduler && docker compose pull && docker compose up -d
```
All settings, IPTV sources, favorites, EPG feeds, and Auto-Record rules/history stored in `./data` are kept intact. Optional automatic updates are supported via Watchtower (`com.centurylinklabs.watchtower.enable=true`).

## Features
- **Arbitrary-future Manual Scheduling**: Pick any channel (with auto-saved last category & favorites filter), any future date/time, duration or end time (including across midnight), optional pre/post padding, DST-aware weekly recurrence, and simultaneous-stream overlap warnings.
- **Full 24-Hour Interactive EPG Grid (Optional)**: Full `00:00`–`24:00` local-day timeline with day buttons, hour jump shortcuts, virtualized category/favorites rows, auto-saved last selected group, per-channel EPG ID overrides, live `uhf-server` timer overlays, and click-to-schedule modal prefill.
- **Multi-Source EPG Deduplication & Multi-Day Program Search**: Supports automatic Xtream provider XMLTV plus custom 7–14 day `.xml` / `.xml.gz` XMLTV feeds parsed incrementally into SQLite; automatically prefers custom XMLTV feeds over short provider guides with fallback for uncovered channels, and provides fast upcoming program search with mirror-stream grouping and optional description matching.
- **Auto-Record Rules (Series Passes)**: Pick a channel and/or search upcoming EPG programs directly in the **Auto-Record** tab (or 1-click inside the EPG Schedule modal) to automatically schedule every matching upcoming airing whenever the EPG refreshes in the background. Supports accent- and case-insensitive matching (`starts_with`, `contains`, `exact`), automatic padding edge trimming on back-to-back episodes, a checked-by-default **"Do not schedule on top of existing programming"** option respecting `Max Simultaneous Streams`, idempotent history (manually cancelled airings are not re-added), and 1-click **Force Schedule** overrides.
- **UHF Server Auto-Detect**: Click **Auto-Detect** in Settings to discover `uhf-server` instances on your LAN via Zeroconf/mDNS (`_uhf-server._tcp.local.`), localhost ports (`8085` / `8000`), and Docker Desktop `host.docker.internal` probes.
- **In-App Update Notifications**: Checks GitHub Releases periodically and notifies you in the header and Settings when a newer release is available.
- **Recordings Management**: View scheduled, active, completed, and failed `uhf-server` recordings with progress, `error_code` diagnostics, safe edit-via-replace, recurrence-safe cancellation, and direct Watch/Download links to `uhf-server`.

## Local Development

### Backend
```bash
cd backend
python -m venv .venv
# Windows: .\.venv\Scripts\activate  |  macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8086
```

### Frontend
```bash
cd frontend
npm install
npm run dev       # proxies /api to http://127.0.0.1:8086
npm run build     # builds static SPA to frontend/dist (served automatically by FastAPI)
```

### Tests
```bash
cd backend
pytest -v
```

## Manual Docker Compose
```yaml
services:
  uhf-scheduler:
    image: ghcr.io/marcboy/uhf-scheduler:latest
    container_name: uhf-scheduler
    restart: unless-stopped
    network_mode: host # Linux/Pi (enables mDNS); use ports: ["8086:8086"] on macOS/Windows
    volumes:
      - ./data:/data
```

## Systemd User Service (Optional Native Linux Install)
A template unit is included in `deploy/uhf-scheduler.service`. Copy it to `~/.config/systemd/user/uhf-scheduler.service` and enable via `systemctl --user enable --now uhf-scheduler.service`.
