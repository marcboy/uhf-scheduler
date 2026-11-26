#!/bin/sh
set -eu

# UHF Scheduler — 1-command Docker installer & updater (Linux / Raspberry Pi / macOS)
# All settings, channels, EPG data, and Auto-Record rules live in ./data/scheduler.db
# and are preserved across updates.

IMAGE="${UHF_SCHEDULER_IMAGE:-ghcr.io/marcboy/uhf-scheduler:latest}"
INSTALL_DIR="${UHF_SCHEDULER_DIR:-$HOME/uhf-scheduler}"
PORT="${UHF_SCHEDULER_PORT:-8086}"
OS_NAME="$(uname -s)"

echo "=== UHF Scheduler Installer ==="
echo "Install directory: $INSTALL_DIR"
echo "Image:             $IMAGE"
echo "Port:              $PORT"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: Docker is not installed. Install Docker first: https://docs.docker.com/get-docker/" >&2
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  echo "Error: Docker Compose is not available." >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR/data"
cd "$INSTALL_DIR"

if [ "$OS_NAME" = "Linux" ]; then
  UID_NUM="$(id -u)"
  GID_NUM="$(id -g)"
  cat > docker-compose.yml <<EOF
services:
  uhf-scheduler:
    image: ${IMAGE}
    container_name: uhf-scheduler
    restart: unless-stopped
    user: "${UID_NUM}:${GID_NUM}"
    network_mode: host
    environment:
      - PORT=${PORT}
      - UHF_SCHEDULER_DATA_DIR=/data
    volumes:
      - ./data:/data
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
EOF
else
  cat > docker-compose.yml <<EOF
services:
  uhf-scheduler:
    image: ${IMAGE}
    container_name: uhf-scheduler
    restart: unless-stopped
    ports:
      - "${PORT}:8086"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - PORT=8086
      - UHF_SCHEDULER_DATA_DIR=/data
    volumes:
      - ./data:/data
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
EOF
fi

cat > update.sh <<EOF
#!/bin/sh
set -eu
cd "$INSTALL_DIR"
$COMPOSE_CMD pull
$COMPOSE_CMD up -d
echo "UHF Scheduler updated! All settings and Auto-Record rules in ./data were preserved."
EOF
chmod +x update.sh

echo "Pulling latest image and starting UHF Scheduler..."
$COMPOSE_CMD pull
$COMPOSE_CMD up -d

echo "Waiting for UHF Scheduler health check..."
READY=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
      READY=1
      break
    fi
  else
    READY=1
    break
  fi
  sleep 2
done

LAN_IP=""
if [ "$OS_NAME" = "Linux" ] && command -v hostname >/dev/null 2>&1; then
  LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
fi

echo ""
if [ "$READY" = "1" ]; then
  echo "UHF Scheduler is running!"
else
  echo "UHF Scheduler container started (still warming up — check logs with: $COMPOSE_CMD logs -f)."
fi
echo "  Local URL:  http://localhost:${PORT}"
if [ -n "$LAN_IP" ]; then
  echo "  LAN URL:    http://${LAN_IP}:${PORT}"
fi
echo "  Data dir:   ${INSTALL_DIR}/data (settings, EPG & Auto-Record rules)"
echo "  To update:  ${INSTALL_DIR}/update.sh  (or re-run this installer)"
