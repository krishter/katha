FROM python:3.12-slim

WORKDIR /app

# ffmpeg for the audio paths. The rest is for Pillow's complex text
# layout: libraqm pulls in HarfBuzz and FriBidi, which is what shapes
# Indic scripts — forming conjuncts and moving pre-base vowel signs in
# front of the consonant they follow in memory. Without it, memory cards
# in Malayalam, Tamil, Bengali and the rest render incorrectly, and
# right-to-left Urdu not at all.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libraqm-dev \
    libfreetype6-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libjpeg-dev \
    zlib1g-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Pillow is built from source rather than installed as a wheel. The
# prebuilt manylinux wheels are not linked against libraqm — verified:
# `python:3.12-slim` + `pip install Pillow` reports features.check("raqm")
# as False — and raqm support is a compile-time decision, so installing
# the library at runtime does nothing. --no-binary is what makes the
# apt packages above matter.
RUN pip install --no-cache-dir --no-binary Pillow -r requirements.txt

# Fail the build rather than ship an image that renders tofu. This is
# cheap to check and impossible to notice later: cards still generate,
# still upload and still send, they are just unreadable to the family
# they were made for.
RUN python -c "\
from PIL import features; \
assert features.check('raqm'), 'Pillow built without libraqm — Indic text would not be shaped'; \
print('Pillow raqm support: OK')"

COPY . .

# Production default: no --reload (a dev-server file-watcher has no place
# here), and --workers 1 is required, not just a default — APScheduler runs
# in-process (see the comment next to scheduler.start() in main.py), and a
# second worker would double-send every scheduled session. Local dev's
# --reload lives in docker-compose.yml's command override instead.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
