# Overdulge frontend

React (Vite), static build, deployed to GitHub Pages. See
`docs/architecture/SYSTEM.md` for module boundaries and conventions.

## Setup

```
cd frontend
npm ci
```

Copy the repo-root `.env.example` to `frontend/.env` (gitignored) and fill in
the `VITE_*` variables. Only frontend-safe values go here — the Supabase
**anon** key is public by design; never put the service-role key in a
`VITE_*` variable, since anything prefixed `VITE_` is bundled into the
client build and shipped to the browser.

- `VITE_SUPABASE_URL` — Supabase project URL.
- `VITE_SUPABASE_ANON_KEY` — Supabase anon (public) key.
- `VITE_API_BASE_URL` — backend API base URL (defaults to `/api/v1`).

## Run locally

```
npm run dev
```

## Test & lint

```
npm test
npm run lint
```

## Build

```
npm run build
```

Produces `dist/index.html`. The repo deploys as a GitHub Pages *project*
page (`https://<user>.github.io/Overdulge/`, no custom domain), so
`vite.config.ts` sets `base: '/Overdulge/'` and the router reads
`import.meta.env.BASE_URL` as its `basename`. `postbuild` copies
`dist/index.html` to `dist/404.html` so client-side routes survive a
hard refresh on Pages' static hosting.
