$ErrorActionPreference = "Stop"

# UHF Scheduler — 1-command Docker installer & updater for Windows PowerShell
# All settings, channels, EPG data, and Auto-Record rules live in .\data\scheduler.db
# and are preserved across updates.

$Image = if ($env:UHF_SCHEDULER_IMAGE) { $env:UHF_SCHEDULER_IMAGE } else { "ghcr.io/marcboy/uhf-scheduler:latest" }
$InstallDir = if ($env:UHF_SCHEDULER_DIR) { $env:UHF_SCHEDULER_DIR } else { Join-Path $env:USERPROFILE "uhf-scheduler" }
$Port = if ($env:UHF_SCHEDULER_PORT) { $env:UHF_SCHEDULER_PORT } else { "8086" }

Write-Host "=== UHF Scheduler Installer (Windows) ===" -ForegroundColor Cyan
Write-Host "Install directory: $InstallDir"
Write-Host "Image:             $Image"
Write-Host "Port:              $Port"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker is not installed. Install Docker Desktop first: https://docs.docker.com/desktop/install/windows-install/"
    exit 1
}

New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "data") | Out-Null
Set-Location $InstallDir

$composeContent = @"
services:
  uhf-scheduler:
    image: $Image
    container_name: uhf-scheduler
    restart: unless-stopped
    ports:
      - "${Port}:8086"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - PORT=8086
      - UHF_SCHEDULER_DATA_DIR=/data
    volumes:
      - ./data:/data
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
"@
Set-Content -Path (Join-Path $InstallDir "docker-compose.yml") -Value $composeContent -Encoding UTF8

$updateContent = @"
`$ErrorActionPreference = "Stop"
Set-Location "$InstallDir"
docker compose pull
docker compose up -d
Write-Host "UHF Scheduler updated! All settings and Auto-Record rules in .\data were preserved." -ForegroundColor Green
"@
Set-Content -Path (Join-Path $InstallDir "update.ps1") -Value $updateContent -Encoding UTF8

Write-Host "Pulling latest image and starting UHF Scheduler..."
docker compose pull
docker compose up -d

Write-Host "Waiting for UHF Scheduler health check..."
$ready = $false
for ($i = 0; $i -lt 15; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:${Port}/api/health" -TimeoutSec 3
        if ($resp.status -eq "ok") {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

Write-Host ""
if ($ready) {
    Write-Host "UHF Scheduler is running!" -ForegroundColor Green
} else {
    Write-Host "Container started (still warming up — check logs with: docker compose logs -f)." -ForegroundColor Yellow
}
Write-Host "  Open:       http://localhost:${Port}"
Write-Host "  Data dir:   $(Join-Path $InstallDir 'data') (settings, EPG & Auto-Record rules)"
Write-Host "  To update:  $(Join-Path $InstallDir 'update.ps1')  (or re-run this installer)"
