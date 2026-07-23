# Overdulge backend — FastAPI on Google Cloud Run.
# Cloud Run injects $PORT (default 8080); the server MUST bind 0.0.0.0:$PORT.
FROM python:3.12-slim

WORKDIR /app

# uv for fast, locked installs.
RUN pip install --no-cache-dir uv

# Install runtime deps first (cached unless the lock changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# App code.
COPY app ./app

ENV PORT=8080
EXPOSE 8080

# Shell form so ${PORT} expands at runtime (Cloud Run sets it).
CMD uv run --no-dev uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
