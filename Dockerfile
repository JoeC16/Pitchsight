# PitchSight app image: API + UI (+ the same image runs the job worker).
#   docker build -t pitchsight .
# If Docker Hub rate-limits you:  --build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:3.12-slim
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

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
