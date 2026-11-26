# Stage 1: Build React SPA
FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python FastAPI runtime
FROM python:3.13-slim
WORKDIR /app

LABEL org.opencontainers.image.source="https://github.com/marcboy/uhf-scheduler" \
      org.opencontainers.image.description="UHF Scheduler — a 6-kids effort. Web scheduler, 24h EPG grid, and Auto-Record rules for uhf-server." \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8086 \
    UHF_SCHEDULER_DATA_DIR=/data \
    UHF_SCHEDULER_FRONTEND_DIR=/app/frontend/dist

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/app ./backend/app
COPY --from=frontend-build /frontend/dist ./frontend/dist

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8086

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8086\")}/api/health', timeout=4)" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --app-dir /app/backend --host ${HOST:-0.0.0.0} --port ${PORT:-8086}"]
