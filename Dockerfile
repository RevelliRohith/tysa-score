# Multi-stage or slim python build for TYSA Score
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Install system dependencies (for psycopg2, Pillow, reportlab, and healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libjpeg-dev \
    zlib1g-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt /app/
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy application code
COPY . /app/

# Expose web port
EXPOSE 8000

# Default entrypoint starts Daphne ASGI server
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
