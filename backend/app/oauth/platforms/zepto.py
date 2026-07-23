"""Zepto's `PlatformConfig` for the generic OAuth engine (ADR-0001, BRD
§2.2).
"""

from __future__ import annotations

from app.oauth.engine import PlatformConfig

CONFIG = PlatformConfig(
    name="zepto",
    issuer="https://auth.zepto.co.in",
    scopes=(),
    mcp_base_url="https://mcp.zepto.co.in",
)
