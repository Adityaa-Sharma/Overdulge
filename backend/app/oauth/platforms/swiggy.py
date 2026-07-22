"""Swiggy's `PlatformConfig` for the generic OAuth engine (ADR-0001, BRD
§2.2). Swiggy's DCR/PKCE auth server fronts both Swiggy Food and Instamart —
one linked account covers both.
"""

from __future__ import annotations

from app.oauth.engine import PlatformConfig

CONFIG = PlatformConfig(
    name="swiggy",
    issuer="https://mcp.swiggy.com/auth",
    scopes=(),
    mcp_base_url="https://mcp.swiggy.com",
)
