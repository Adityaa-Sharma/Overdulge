from fastapi import Depends, FastAPI

from app.api import health, me
from app.core.auth import get_current_user

app = FastAPI(title="Overdulge")

# GET /health is a public liveness check; every other route under api/
# requires a verified Supabase JWT (BRD AC-8). The OAuth callback endpoint
# (added in a later task) is the one other exception, per issue #15 scope.
app.include_router(health.router, prefix="/api/v1")
app.include_router(me.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])


async def on_fetch(request, env):
    import asgi

    return await asgi.fetch(app, request, env)
