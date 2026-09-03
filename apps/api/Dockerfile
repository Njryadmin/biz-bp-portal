# apps/api/Dockerfile
# FastAPI service for the Fin BP Portal.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps (gcc needed for pandas/asyncpg wheels on slim)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cache layer)
COPY apps/api/pyproject.toml apps/api/README.md* ./
RUN pip install --upgrade pip && \
    pip install -e . && \
    pip install "uvicorn[standard]>=0.30.1"

# Copy source
COPY apps/api/app ./app

# Healthcheck
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD curl -fsS http://localhost:8000/api/registry/lines || exit 1

EXPOSE 8000

# Entrypoint — wait briefly for any external deps then start uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
