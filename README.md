# QA Screenshot Tool

Sitemap-driven website screenshot capture for visual QA — manages projects, captures pages at multiple viewports/browsers via Playwright, and serves screenshots through a React frontend.

## Stack

Python 3.12, FastAPI, SQLModel, Alembic, ARQ, Playwright, PostgreSQL 16, Redis 7, React 19, TypeScript, Vite, TailwindCSS, Radix UI, Nginx, Docker

## Quickstart

```bash
cp .env.example .env
# edit .env — set SECRET_KEY and adjust ports if needed

# Development (hot reload, Vite dev server)
docker compose -f compose.yml -f compose.dev.yml up -d --build
docker compose -f compose.yml -f compose.dev.yml exec api alembic upgrade head
# → frontend: http://localhost:15173  api: http://localhost:18081/docs

# Production (Nginx on port 80, migrations run automatically)
docker compose -f compose.yml -f compose.prod.yml run --rm migrate
docker compose -f compose.yml -f compose.prod.yml up -d --build
```

## Environment Variables

| Variable | Description |
|---|---|
| `SECRET_KEY` | JWT signing key — **must change in production** |
| `DATABASE_URL` | PostgreSQL connection string |
| `POSTGRES_USER/PASSWORD/DB` | PostgreSQL credentials |
| `REDIS_URL` | Redis connection string |
| `CORS_ALLOWED_ORIGINS` | Comma-separated allowed origins |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT lifetime (default 1440) |
| `ACCESS_TOKEN_COOKIE_SECURE` | Set `true` in production (HTTPS) |
| `SEM_LIMIT` | Pages captured at once per worker, shared by all running tasks and re-captures (default 4) |
| `WORKER_MAX_JOBS` | Tasks/re-captures a worker runs at once — ARQ `max_jobs`, needs a worker restart (default 10) |
| `REUSE_BROWSER_CONTEXT` | Capture all viewports of a URL through one shared browser context (default `true`; editable in Global Settings) |
| `DEV_API_PORT` / `DEV_FRONTEND_PORT` | Dev host port mappings |
| `PROD_HTTP_PORT` | Production web port (default 80) |
| `DATA_DIR` | Screenshot storage path (default `data`) |
| `TASK_JOB_TIMEOUT_SECONDS` | ARQ screenshot task timeout in seconds (default 7200) |
| `BROWSER_WS_ENDPOINT` | Remote browser server **base host without a port** (e.g. `ws://browser`); the worker appends the per-browser port. Empty = launch browsers locally (needs `playwright install`). See **Remote browser server** below. |
| `BROWSER_WS_PATH` | WebSocket path of the browser servers — must match the browser host's value (not a secret, see **Remote browser server**) |
| `BROWSER_CONNECT_TIMEOUT_MS` | Timeout for connecting to the remote browser (default 30000) |

## Project Structure

```
app/             FastAPI application
  auth/          Authentication (JWT, cookies)
  projects/      Projects, configs, sitemap parsing
  tasks/         Capture tasks, ARQ job dispatch
  screenshots/   Screenshot metadata, file serving, HMAC tokens
  settings/      App-level capture settings
  users/         User management
  worker/        ARQ worker entrypoint
alembic/         Database migrations
frontend/        React/Vite SPA + Nginx production image
tests/           Pytest suite
data/            Screenshot and archive storage (runtime)
compose.yml      Shared Compose base
compose.dev.yml  Dev overrides
compose.prod.yml Production overrides
```

The Playwright browser server lives in the sibling repo
**`everneu-screenshots-browsers`** — clone it next to this one (the dev stack
builds its image from `../everneu-screenshots-browsers`).

## Remote browser server

Browsers run in a **separate container** that lives in its own sibling repo
**`everneu-screenshots-browsers`** (the official Playwright image + branded Chrome/Edge),
not inside the api/worker image. Its entrypoint `browser-server.mjs` starts **one Playwright
`launchServer` per browser, each on its own port**, so every engine — and the real branded
channels — is reachable remotely:

| Browser | Port | Renders with |
|---|---|---|
| `chromium` | 3000 | Chromium (Blink) |
| `chrome` | 3001 | **real Google Chrome** (Blink) |
| `msedge` | 3002 | **real Microsoft Edge** (Blink) |
| `firefox` | 3003 | Firefox (Gecko) |
| `webkit` | 3004 | WebKit |

The worker connects per browser via `BROWSER_WS_ENDPOINT` (a **base host without a port**) +
the port above; `page.screenshot()` returns the image bytes back to the worker, which still
writes them to `DATA_DIR` — so storage is unchanged. The channel and chromium launch args are
baked into each `launchServer` (which is why `connect()`, that can't pass them, still gets real
Chrome/Edge).

- **Dev** (`compose.dev.yml`): a `browser` service is built from the sibling repo
  (`../everneu-screenshots-browsers` — clone it next to this one), runs on the same Docker
  network, and the worker is pre-wired to `BROWSER_WS_ENDPOINT=ws://browser`.
- **Production**: deploy the sibling repo on its **own host** with
  `docker compose up -d --build` (run inside that repo), then set
  `BROWSER_WS_ENDPOINT=ws://<browser-host-private-ip>` (no port) and the matching
  `BROWSER_WS_PATH` in the app host's `.env`.

> **Cross-repo contract (kept in sync manually — no automated check):**
> the port map above must equal `_BROWSER_WS_PORTS` in `app/tasks/worker.py` and `SPECS` in
> the sibling repo's `browser-server.mjs`; the sibling Dockerfile's `PLAYWRIGHT_VERSION` must
> match the `playwright` pip version in `pyproject.toml` (currently `1.58.0`) to avoid a
> client/server mismatch; the sibling's `CHROMIUM_ARGS` mirror `_CHROMIUM_LAUNCH_ARGS` minus
> `--disable-dev-shm-usage` (its container runs with `ipc: host`).
>
> **Security:** the browser servers have no authentication, and `BROWSER_WS_PATH` is
> not a secret (each server answers `GET /json` with it). Restrict ports 3000–3004 to the
> app host via private network / firewall / VPC; never expose them publicly.
>
> **Image size:** the branded Chrome + Edge channels add ~1–1.5 GB to that image.
>
> Local non-Docker runs with `BROWSER_WS_ENDPOINT=` empty fall back to a local `launch()` and
> require `python -m playwright install`.

## API

| Endpoint | Description |
|---|---|
| `POST /auth/register` `POST /auth/login` | Auth |
| `GET/POST /projects/` | Projects |
| `GET/PATCH /projects/{id}/config` | Capture settings (viewports, browsers) |
| `POST /projects/{id}/sitemap/fetch` | Parse sitemap from project URL |
| `GET/PATCH /projects/{id}/sitemap/urls` | URL selection |
| `GET/POST/DELETE /tasks/` | Capture tasks |
| `POST /tasks/{id}/archive` | Create ZIP of task screenshots |
| `GET /screenshots/{task_id}` | List screenshots |
| `GET /files/{task_id}/{filename}` | Serve screenshot (HMAC token required) |
| `GET/PATCH /settings/` | App settings |
| `GET /health` | Health check |
