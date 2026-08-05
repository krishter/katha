FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Production default: no --reload (a dev-server file-watcher has no place
# here), and --workers 1 is required, not just a default — APScheduler runs
# in-process (see the comment next to scheduler.start() in main.py), and a
# second worker would double-send every scheduled session. Local dev's
# --reload lives in docker-compose.yml's command override instead.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
