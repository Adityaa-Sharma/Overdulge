# Cloudflare Python Workers entrypoint. This module MUST live at the
# Worker's base_dir (backend/, sibling to the `app` package) — Pyodide's
# import root is the directory containing wrangler.toml's `main` file, so
# an entrypoint nested inside `app/` cannot resolve `from app.api import
# ...` (no `app` package exists inside `app/` itself). See issue #55.
from app.main import app


async def on_fetch(request, env):
    import asgi

    return await asgi.fetch(app, request, env)
