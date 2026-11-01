# PitchSight app image: API + UI (+ the same image runs the job worker).
#   docker build -t pitchsight .                  production image (last stage, "app")
#   docker build --target dev -t pitchsight:dev .  + test tools and the S3 emulator, for compose/CI
# If Docker Hub rate-limits you:  --build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:3.12-slim
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS base

# ffmpeg/ffprobe for probing, clips, reels and proxies. Debian's build is GPL (includes x264):
# see docs/LICENCES.md before distributing this image to anyone.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PITCHSIGHT_DATA_DIR=/data
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY sport_packs sport_packs
COPY frontend frontend
COPY backend backend

RUN useradd --create-home --uid 10001 pitchsight && mkdir -p /data && chown pitchsight /data
USER pitchsight
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# Development extras (pytest, moto's S3 emulator). Not in the production image.
FROM base AS dev
USER root
COPY backend/requirements-dev.txt backend/requirements-dev.txt
RUN pip install -r backend/requirements-dev.txt
USER pitchsight

# Production image: the default (last) stage.
FROM base AS app
