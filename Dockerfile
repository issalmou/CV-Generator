# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# CV Assistant - CV Generation service
# Single-stage image: FastAPI app served by uvicorn.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# - PYTHONDONTWRITEBYTECODE: no .pyc in the image
# - PYTHONUNBUFFERED: logs stream straight to `docker logs`
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Optional: headless Chromium for the browser-based job providers.
# Off at runtime by default (BROWSER_ENABLED=false); installed so it's ready.
# Build without it:  docker build --build-arg INSTALL_BROWSER=false ...
ARG INSTALL_BROWSER=true
RUN if [ "$INSTALL_BROWSER" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends \
        chromium chromium-driver fonts-liberation ca-certificates \
      && rm -rf /var/lib/apt/lists/*; \
    fi
ENV BROWSER_BINARY=/usr/bin/chromium \
    BROWSER_DRIVER_PATH=/usr/bin/chromedriver

# Dependencies first, so the layer is cached until requirements.txt changes.
# PyMuPDF / reportlab / python-docx ship self-contained wheels — no apt packages
# are needed on top of python:3.11-slim.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Liveness: the app's own health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else sys.exit(1)"

# Shell form so ${PORT} is expanded; single worker (LLM calls are I/O-bound
# and already offloaded to a thread pool in main.py).
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
