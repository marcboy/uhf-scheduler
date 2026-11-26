# UHF Scheduler

*a 6-kids effort*

**UHF Scheduler** is a lightweight, self-hosted web app for scheduling arbitrary-future IPTV DVR recordings directly into **`uhf-server`** — removing the 24/48-hour guide limit of the Apple TV UHF app while keeping native Apple TV playback, stream recovery, and single-file recordings.

---

## What It Does

- **Manual Scheduling (No Time Limit):** Pick any channel and schedule recordings days, weeks, or months ahead by duration or end time (including across midnight), with pre/post padding, DST-aware weekly repeats, and simultaneous-stream conflict warnings.
- **Full 24-Hour EPG Grid & Multi-Day Program Search:** Browse a full `00:00`–`24:00` local-day guide across days, search upcoming broadcasts across your entire EPG (with mirror channels grouped cleanly per broadcast), and click any program to schedule in one click.
- **Auto-Record Rules (Series Passes):** Pick a channel and program name (or search upcoming programs directly) to automatically schedule every matching airing whenever the EPG syncs daily. Includes accent/case-insensitive matching, automatic padding edge trimming on back-to-back episodes, and a checked-by-default **"Do not schedule on top of existing programming"** guard honoring your simultaneous stream limit.
- **Multi-Source IPTV & XMLTV Support:** Connect one or more **Xtream Codes** or **M3U** playlists plus automatic provider EPG and/or custom 7–14 day `.xml`/`.xml.gz` XMLTV feeds (with automatic multi-source deduplication).
- **Zero-Friction Setup & Updates:** Auto-detects `uhf-server` via LAN mDNS (`_uhf-server._tcp`) and localhost/Docker host probes, auto-detects Apple TV UHF `playlistID`s, and notifies you in-app when a new release is out.

---

## 1-Command Install (Docker — Recommended)

Prebuilt multi-architecture images (`linux/amd64` + `linux/arm64`) are published to `ghcr.io/marcboy/uhf-scheduler:latest` (works on Raspberry Pi, Linux servers/NAS, macOS, and Windows).

**Prerequisites:** [Docker](https://docs.docker.com/get-docker/) (with Docker Compose) and a running [`uhf-server`](https://github.com/swapplications/uhf-server-dist) instance.

### Linux / Raspberry Pi / macOS
```sh
curl -fsSL https://raw.githubusercontent.com/marcboy/uhf-scheduler/main/install.sh | sh
```

### Windows (PowerShell)
```powershell
irm https://raw.githubusercontent.com/marcboy/uhf-scheduler/main/install.ps1 | iex
```

By default, UHF Scheduler runs on port **`8086`** (`http://localhost:8086` or `http://<lan-ip>:8086`) and stores all data in `~/uhf-scheduler/data`.

### Custom Port or Install Directory (Optional)
- **Linux / macOS:**
  ```sh
  curl -fsSL https://raw.githubusercontent.com/marcboy/uhf-scheduler/main/install.sh | UHF_SCHEDULER_PORT=9090 UHF_SCHEDULER_DIR=/opt/uhf-scheduler sh
  ```
- **Windows (PowerShell):**
  ```powershell
  $env:UHF_SCHEDULER_PORT="9090"; $env:UHF_SCHEDULER_DIR="C:\uhf-scheduler"; irm https://raw.githubusercontent.com/marcboy/uhf-scheduler/main/install.ps1 | iex
  ```

---

## First-Time Setup

1. Open **`http://localhost:8086`** (or `http://<your-server-ip>:8086` from any phone/tablet/computer on your LAN).
2. Go to **Settings → UHF Server & Defaults**, click **Auto-Detect** (or enter your `uhf-server` URL and optional server password), click **Test UHF Connection**, and save.
3. Under **Add IPTV Source**, enter your **Xtream Codes** login or **M3U** playlist URL (click **Auto-Detect** next to `playlistID` so recordings group cleanly in Apple TV UHF), save, and click **Sync Now**.
4. *(Optional)* Under **EPG / XMLTV Sources**, sync your provider EPG and/or add any 7–14 day external XMLTV URL (`.xml` or `.xml.gz`).
5. Start scheduling from **Manual Schedule**, **EPG Grid**, or **Auto-Record** — every recording appears natively in your Apple TV UHF app.

---

## Updating (Preserves All Settings & Auto-Record Rules)

All settings, IPTV/EPG sources, favorites, hidden categories, **Auto-Record rules**, and rule history live in `./data/scheduler.db` (mounted as `/data`), while actual recordings live in `uhf-server`. Updating never touches your data.

Run **any** of the following:
- Re-run the 1-command install script above, **or**
- Run the generated helper script inside your install folder (`~/uhf-scheduler/update.sh` on Linux/macOS or `~\uhf-scheduler\update.ps1` on Windows), **or**
- Run directly with Docker Compose:
  ```sh
  cd ~/uhf-scheduler && docker compose pull && docker compose up -d
  ```

Optional hands-off updates are supported via [Watchtower](https://containrrr.dev/watchtower/) (`com.centurylinklabs.watchtower.enable=true` is preconfigured in the generated `docker-compose.yml`).

---

## Manual Docker Compose

```yaml
services:
  uhf-scheduler:
    image: ghcr.io/marcboy/uhf-scheduler:latest
    container_name: uhf-scheduler
    restart: unless-stopped
    # On Linux / Raspberry Pi, host networking enables native mDNS discovery.
    network_mode: host
    # On macOS / Windows Docker Desktop, replace `network_mode: host` with:
    # ports:
    #   - "8086:8086"
    # extra_hosts:
    #   - "host.docker.internal:host-gateway"
    environment:
      - PORT=8086
      - UHF_SCHEDULER_DATA_DIR=/data
    volumes:
      - ./data:/data
```

---

## Local Development & Native Linux Install

### Backend (Python 3.12+ / FastAPI / SQLite)
```bash
cd backend
python -m venv .venv
# Windows: .\.venv\Scripts\activate  |  macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8086
```

### Frontend (React / TypeScript / Vite)
```bash
cd frontend
npm install
npm run dev       # proxies /api to http://127.0.0.1:8086
npm run build     # outputs static SPA to frontend/dist (served automatically by FastAPI)
```

### Tests
```bash
cd backend
pytest -v
```

### Native Systemd User Service (Linux)
A systemd user unit template is included in [deploy/uhf-scheduler.service](deploy/uhf-scheduler.service). Copy it to `~/.config/systemd/user/uhf-scheduler.service` and run `systemctl --user daemon-reload && systemctl --user enable --now uhf-scheduler.service`.
