from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    budgets,
    budgets_suggestions,
    dashboard,
    health,
    links,
    me,
    query,
    recommendations,
    sync,
)
from app.core.auth import get_current_user
from app.core.config import get_settings

app = FastAPI(title="Overdulge")

# The SPA (GitHub Pages) and this API (Cloud Run) are different origins, so the
# browser needs CORS headers or every fetch is blocked. Allow the deployed
# frontend origin plus localhost for dev. Origin is scheme+host only (no path).
_frontend = (get_settings().frontend_settings_url or "").rstrip("/")
_allowed = {"https://adityaa-sharma.github.io", "http://localhost:5173", "http://localhost:3000"}
if _frontend:
    # Reduce a full URL (…/Overdulge/) to its origin.
    from urllib.parse import urlparse

    p = urlparse(_frontend)
    if p.scheme and p.netloc:
        _allowed.add(f"{p.scheme}://{p.netloc}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(_allowed),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# GET /health is a public liveness check; every other route under api/
# requires a verified Supabase JWT (BRD AC-8), except the OAuth callback
# in links.router (public by necessity — see ADR-0001), which applies
# get_current_user per-route instead of via this router-wide dependency.
app.include_router(health.router, prefix="/api/v1")
app.include_router(me.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
app.include_router(links.router, prefix="/api/v1")
app.include_router(budgets.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
app.include_router(
    budgets_suggestions.router, prefix="/api/v1", dependencies=[Depends(get_current_user)]
)
app.include_router(dashboard.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
app.include_router(sync.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
app.include_router(
    recommendations.router, prefix="/api/v1", dependencies=[Depends(get_current_user)]
)
app.include_router(query.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
