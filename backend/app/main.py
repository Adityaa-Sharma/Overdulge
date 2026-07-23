from fastapi import Depends, FastAPI

from app.api import dashboard, health, links, me
from app.core.auth import get_current_user

app = FastAPI(title="Overdulge")

# GET /health is a public liveness check; every other route under api/
# requires a verified Supabase JWT (BRD AC-8), except the OAuth callback
# in links.router (public by necessity — see ADR-0001), which applies
# get_current_user per-route instead of via this router-wide dependency.
app.include_router(health.router, prefix="/api/v1")
app.include_router(me.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
app.include_router(links.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
