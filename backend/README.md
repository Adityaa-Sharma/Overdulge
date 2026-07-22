# Overdulge backend

FastAPI on Cloudflare Python Workers (`pywrangler`). See
`docs/architecture/SYSTEM.md` for module boundaries and conventions.

## Setup

```
cd backend
uv sync
```

## Run locally

```
uv run uvicorn app.main:app --reload
curl http://localhost:8000/api/v1/health
# {"status":"ok"}
```

Secrets are read from the environment / a local `.env` (gitignored, copy
from the repo-root `.env.example`) via `app/core/config.py` — never
hard-coded.

## Test & lint

```
uv run pytest -q
uv run ruff check .
```
