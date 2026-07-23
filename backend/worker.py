# Cloudflare Python Workers entrypoint. This module MUST live at the
# Worker's base_dir (backend/, sibling to the `app` package) — Pyodide's
# import root is the directory containing wrangler.toml's `main` file, so
# an entrypoint nested inside `app/` cannot resolve `from app.api import
# ...` (no `app` package exists inside `app/` itself). See issue #55.
#
# Cloudflare's runtime only auto-registers event handlers from a
# `WorkerEntrypoint` subclass named `Default` (module-level `on_fetch`
# functions stopped being recognised for compatibility dates after
# 2025-08-14 — see https://developers.cloudflare.com/changelog/post/2025-08-14-new-python-handlers/
# and https://developers.cloudflare.com/workers/languages/python/packages/fastapi/).
# See issue #57.
from app.main import app

try:
    from workers import WorkerEntrypoint
except ImportError:  # `workers` only exists inside the Pyodide Workers runtime

    class WorkerEntrypoint:
        def __init__(self, ctx=None, env=None):
            self.ctx = ctx
            self.env = env


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        import asgi

        return await asgi.fetch(app, request, self.env)
