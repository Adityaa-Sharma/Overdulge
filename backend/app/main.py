from fastapi import FastAPI

from app.api import health

app = FastAPI(title="Overdulge")
app.include_router(health.router, prefix="/api/v1")


async def on_fetch(request, env):
    import asgi

    return await asgi.fetch(app, request, env)
