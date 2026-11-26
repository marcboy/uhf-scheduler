# uhf-scheduler — STATE.md

## GOAL
Build and distribute a standalone web application (`uhf-scheduler`, *"a 6-kids effort"*) that schedules arbitrary-future manual, EPG-grid, and Auto-Record (series pass) IPTV DVR recordings directly into `uhf-server`, packaged as a multi-arch (`linux/amd64`, `linux/arm64`) Docker image with 1-command install/update scripts and persistent `/data` storage.

## STATUS
- **Version:** v0.1.16
- **Repo:** Public GitHub repo `https://github.com/marcboy/uhf-scheduler` (`main`)
- **Docker image:** `ghcr.io/marcboy/uhf-scheduler:latest` (`linux/amd64`, `linux/arm64`)
- **Stack:** React + TypeScript (Vite) frontend, Python 3 + FastAPI + SQLite (WAL) backend (`24/24` pytest tests passing)
- **Specs:** Synced under `docs/specs/`

## KEY ARCHITECTURAL NOTES
- `uhf-server` advertises itself via mDNS (`_uhf-server._tcp.local.`) and returns `id_token` from `/auth/password-login`.
- mDNS IPv4 selection ranks `192.168.*` / `10.*` LAN addresses ahead of `172.*` container bridge IPs; `host.docker.internal` probes support macOS/Windows Docker Desktop hosts.
- `uhf-server` has no in-place edit endpoint for start time/duration: edits create the replacement recording first and only remove the old one after creation succeeds.
- Stopping a recurring scheduled series requires `PATCH /dvr/recordings/{id}/cancel-recurrence`; `/cancel` on a recurring scheduled timer spawns the next occurrence instead.
- Always pass the client's IANA `timezone` alongside `recurrence_days` so DST and local weekdays match (`tzdata` is included in `requirements.txt` for Windows compatibility).
- Outbound IPTV/EPG HTTP requests send a standard browser `User-Agent` to avoid provider TCP resets.
- EPG duplicate collapsing checks true time overlap (`next.start_ts < prev.end_ts`) so short 5-minute broadcasts never hide the immediately following program.
- All user settings, IPTV/EPG sources, favorites, Auto-Record rules, and rule history persist in `/data/scheduler.db` across container upgrades.
