# OpenAI-compat /v1/audio/speech shim for Fish Speech (see ./server.py).
# Runs as a sidecar next to fish-speech in docker-compose.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn>=0.30" \
    "httpx>=0.27" \
    "pydantic>=2.8"

COPY server.py /app/server.py

ENV PYTHONUNBUFFERED=1

EXPOSE 8093

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8093/health')" || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8093"]
