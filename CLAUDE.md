# CLAUDE.md — working in this repo

Guidance for AI coding agents. Read this first, then `README.md` for the
human-facing quickstart, full stack list, env-var table, and API reference.

## What this is

**QA Screenshot Tool** — sitemap-driven website screenshot capture for visual QA.
Manage projects, fetch a site's sitemap, then capture every selected page at
multiple viewports/browsers via Playwright and browse the results in a React UI.

**Architecture:** FastAPI (fully async) HTTP API → enqueues jobs to an **ARQ**
worker (Redis-backed) → worker drives **Playwright** in a **separate browser-server
container** (connected to via `BROWSER_WS_ENDPOINT`, on its own host in prod) to
capture screenshots → files on disk under `DATA_DIR`, metadata in **PostgreSQL**
(SQLModel + Alembic) → **React 19 / Vite** SPA served behind Nginx in prod.

## Commands

```bash
# Dev stack (hot reload): api docs :18081/docs, frontend :15173
docker compose -f compose.yml -f compose.dev.yml up -d --build
docker compose -f compose.yml -f compose.dev.yml exec api alembic upgrade head

# Tests (from repo root) — asyncio_mode=auto, testpaths=["tests"]
pytest                      # whole suite
pytest tests/test_task_worker_lifecycle.py -q

# Lint / format (ruff: line-length 88, target py312)
ruff check .
ruff format .

# Frontend (from frontend/)
npm run dev      # vite dev server
npm run build    # tsc -b && vite build
npm run lint     # eslint

# Migrations (Alembic)
alembic revision -m "describe change"
alembic upgrade head

# Operator CLI (in Docker: docker compose … exec api python -m app.cli …)
python -m app.cli create-admin --email you@example.com   # first admin; no public sign-up
python -m app.cli cleanup-files [--apply]                # files left by deleted rows

# Knowledge graph — run after editing code this session (AST-only, no API cost)
graphify update .
```

CI (`.github/workflows/ci.yml`) runs `ruff check`, `pytest`, an Alembic
upgrade → downgrade -1 → upgrade round-trip on Postgres 16, and the frontend
`lint` + `build` (Node 22).

Most tests use in-process **fakes** (see Testing below), so the suite runs without
standing up Postgres/Redis/Playwright.

## Repository layout

```
app/                 FastAPI application — one package per domain
  core/              config, database (async engine/session), exceptions, redis, url_security
  auth/              JWT auth, cookies, password reset, dependencies
  projects/          projects, ProjectConfig, sitemap fetch/parse, URL selection
  tasks/             capture Task model, service (ARQ dispatch), ARQ worker jobs
  screenshots/       Screenshot model, capture logic, file storage + HMAC signed URLs
  settings/          app-level (DB) settings overriding .env defaults
  users/             user admin
  worker/            ARQ WorkerSettings entrypoint
  cli.py             operator commands (create-admin, cleanup-files)
  main.py            create_app() factory
alembic/versions/    chained migrations
frontend/src/        React/Vite SPA (api/ components/ hooks/ lib/ pages/ store/ types/)
tests/               pytest suite (fake-based unit tests)
data/                runtime screenshot + archive storage (gitignored content)
compose.yml          shared base; compose.dev.yml (hot reload) / compose.prod.yml (Nginx)
```

## Backend conventions

**Layering: `router → service → models`.** Routers are thin — they parse/inject
dependencies, call one service function, and return a `*Read` schema. All business
logic and DB access live in `service.py`. See [tasks/router.py](app/tasks/router.py)
delegating to [tasks/service.py](app/tasks/service.py).

**App factory.** [app/main.py](app/main.py) `create_app()` wires CORS, the
`_RequestIDMiddleware` (adds an `X-Request-ID` header), a `RequestValidationError`
handler that returns `{"detail": [{"field", "message"}]}`, and registers each
domain router under its prefix.

**Errors.** Raise the typed `HTTPException` subclasses from
[app/core/exceptions.py](app/core/exceptions.py) — `NotFoundError`, `ForbiddenError`,
`UnauthorizedError`, `BadRequestError`, `ConflictError`, `UnprocessableError` —
instead of bare `HTTPException(status_code=...)`. They carry the right status + a
sensible default message.

**Async DB.** SQLModel `AsyncSession` injected via `Depends(get_session)` from
[app/core/database.py](app/core/database.py). Service functions take `session` as
their first argument. Query with `session.exec(select(...))`, `session.get(Model, id)`,
then `session.add(...)` / `await session.commit()` / `await session.refresh(...)`.

**Auth & access.** `get_current_user` / `get_current_admin_user` from
[app/auth/dependencies.py](app/auth/dependencies.py). A token may arrive as a
**cookie or Bearer**; the JWT carries a `jti` (checked against a revocation list)
plus a staleness check that invalidates tokens issued before a password change.
There is **no public sign-up**: admins create users (`POST /users`), the first
admin comes from `python -m app.cli create-admin`. Global settings (`/settings`)
are admin-only.

**Projects are shared.** Every signed-in user sees every project, task and
screenshot, and may run captures/re-captures, edit the sitemap URL selection and
delete tasks. Only the project's owner (`projects.owner_id`) or an admin may
rename it, change its URL or `ProjectConfig`, or delete it: services call
`get_project(...)` (existence only) and then `ensure_can_manage_project(project,
user)` ([projects/service.py](app/projects/service.py)). The frontend mirrors
this with `canManageProject` (lib/helpers.ts) — hide, don't just disable, the
owner-only actions.

**Models.** Each `models.py` holds the SQLModel `table=True` entity plus its
`*Create` / `*Read` / `*Response` Pydantic schemas; build `*Read` via a `from_*`
classmethod (e.g. `TaskRead.from_task`). Postgres specifics: `JSONB` for list/dict
columns, `PGUUID`, FKs with `ondelete="CASCADE"`. Status fields are plain strings
whose allowed values are documented inline (e.g. a Task is
`pending | running | completed | failed | cancelled | timed_out`; a Screenshot is
`ok | failed | recapturing`).

**Time is UTC, always aware.** Every timestamp column is `timestamptz`, declared
as `Field(..., sa_type=UtcDateTime)`; "now" is `utcnow()` — both from
[app/core/time.py](app/core/time.py). Never use `datetime.utcnow()` or naive
datetimes (asyncpg would read a naive value in the process's local zone). The API
returns ISO strings with `Z`; the frontend shows them in the user's profile time
zone (`users.timezone`, NULL = the browser's).

**Settings.** [app/core/config.py](app/core/config.py) (pydantic-settings) validates
`SECRET_KEY` (≥32 chars, not the placeholder). Note the **dual-config** model: many
values in `.env` are only *initial defaults* that are overridable at runtime through
Global Settings stored in the DB — resolve them with `resolve_setting(...)` /
`get_application_settings(...)` from [app/settings/service.py](app/settings/service.py).
A few stay `.env`-only (CORS origins, cookie flags, SMTP credentials). See
[.env.example](.env.example) for the full annotated list.

## Screenshot capture pipeline (core flow)

1. **Dispatch** — `create_task` in [tasks/service.py](app/tasks/service.py): verify
   the project exists → guard against a second `pending`/`running` task for the same
   project → resolve effective browsers/viewports and target URLs → compute
   `progress_total` (URLs × viewport-browser combos) → persist → enqueue the ARQ job
   `run_screenshot_task` with a deterministic `_job_id = "screenshot-task:{task_id}"`
   (`worker_job_id_for`). If enqueue fails, the task is flipped to `failed` so it
   never hangs in `pending`.

2. **Run** — `run_screenshot_task` in [tasks/worker.py](app/tasks/worker.py): claim
   the task under a row lock (`pending` → `running`; any other status → return
   without doing anything, so a stale or re-delivered job never runs a task twice)
   → load the run plan (`_load_run_plan`, inside the main `try`, so a setup error
   fails the task) → build the flat `(url, viewport, browser)` combo list → obtain one
   Playwright browser per unique browser name via `_launch_browser` (see **Remote
   browser** below) → a **producer/consumer** pool over an `asyncio.Queue` with
   `SEM_LIMIT` consumers works through `(url, browser, [viewports])` groups (one group
   per combo unless `reuse_browser_context` shares a BrowserContext across a URL's
   viewports) → every `capture_screenshot` holds a slot of the worker-wide
   `CaptureLimiter` ([tasks/limiter.py](app/tasks/limiter.py)), so `SEM_LIMIT` bounds
   pages across *all* running tasks and re-captures → progress is flushed to the DB
   periodically while a background task beats `tasks.heartbeat_at` every 30 s →
   final status is set at the end. **Every terminal status goes through
   `finish_task`**, which only moves a task out of `pending`/`running` (a status set
   first elsewhere, e.g. a user's `cancelled`, is kept) and always sets
   `completed_at`.

3. **EffectiveConfig precedence** (which browsers run): per-viewport `browsers` >
   task `override_browsers` > legacy `override_browser` > `ProjectConfig.default_browsers`.
   Image format/quality come from app-level settings; capture timeout is project-scoped.

4. **Cancellation / timeout.** User cancel = a Redis marker
   (`screenshot-task:user-cancel:{id}`) plus ARQ `job.abort`. On `CancelledError` the
   worker distinguishes user-cancel (→ `cancelled`), the ARQ job timeout (→ `timed_out`,
   backfilling placeholder failed rows for uncaptured combos), and any other interrupt
   (→ `failed`). `reset_orphaned_recapturing` runs on worker startup to recover rows
   stuck in `recapturing` after a crash/redeploy.

   **Orphaned tasks** (worker killed/OOM, job lost): `reap_orphaned_tasks` fails
   `running` tasks without a heartbeat for 150 s and `pending` tasks older than
   2 min whose ARQ job is gone; it runs at worker startup and every 5 minutes as an
   ARQ cron job. `run_screenshot_task` is registered with `max_tries=1`, so ARQ never
   re-runs a capture after a kill (that used to duplicate screenshot rows).
   `cancel_task`/`delete_task` skip the ARQ abort when the task's worker is gone
   (`task_is_alive`, [tasks/lifecycle.py](app/tasks/lifecycle.py)) — a dead job's
   in-progress key lives for `job_timeout` + 10 s and an abort would only time out.

5. **Storage & signed URLs** — [screenshots/storage.py](app/screenshots/storage.py).
   Files live at `DATA_DIR/{project}/{task}/{sanitised_url}/{w}x{h}_{browser}.{ext}`
   and are served only through **HMAC-SHA256 signed URLs** (`/files/...?token=`,
   `/archives/...?token=`) verified with `hmac.compare_digest`. Task archives are
   zipped in a thread pool (`run_in_executor`) and cached on disk
   (`archive_path(task_id)`); a finished re-capture drops the task's cached ZIP.

6. **Deleting** a task or project removes its files after the DB commit
   (`delete_task_files` / `delete_project_files`, in a thread, never outside
   `DATA_DIR`). Both refuse (409) while a capture or re-capture is running for it.
   `python -m app.cli cleanup-files` finds what older deletions left behind.

## Worker runtime

[app/worker/main.py](app/worker/main.py) defines `WorkerSettings` (`functions =
[func(run_screenshot_task, max_tries=1), recapture_screenshot]`, `cron_jobs` = the
5-minute orphan reaper, `max_jobs = WORKER_MAX_JOBS`, `job_timeout =
TASK_JOB_TIMEOUT_SECONDS`, `allow_abort_jobs`, startup/shutdown hooks). The compose
`worker` service has `stop_grace_period: 30s` so a SIGTERM'd capture can record its
status. `startup` puts the
shared `CaptureLimiter` into `ctx["capture_limiter"]`; jobs resize it to the effective
`sem_limit` (jobs run without it — unit tests — fall back to a job-local limiter). Run it
with `python -m arq app.worker.main.WorkerSettings` (the `worker` compose service does this).

**Capture package** ([screenshots/capture/](app/screenshots/capture/)): stages take a
`CaptureRun` and return a dataclass result — `navigation.navigate` → `prepare.prepare_page`
→ `prepare.reapply_transforms` + `readiness.finalize` → `prepare.final_touches` →
`snapshot.take_snapshot`, run by `pipeline.run_attempt` / `pipeline.capture_with_retries`;
[screenshots/service.py](app/screenshots/service.py) keeps `capture_screenshot` (file +
row). Modules call each other through the module object and read tunables as `cfg.NAME`
from `capture/config.py`, so tests patch the defining module
(`app.screenshots.capture.readiness.wait_for_dom_stability`,
`app.screenshots.capture.clock.now`). Every page script carries a `/*qa:<name>*/` marker
as the first token of its function body and runs through `jsutil`; the test fakes in
`tests/capture/fakes.py` answer scripts by marker.

**Capture timing**: scroll and settle pauses are adaptive — `network.wait_for_quiet` ends a
pause once no tracked request (`network.InflightTracker`: image/font/stylesheet/script/
document; xhr/fetch only right after `goto`; never media) has been in flight for the quiet
period, capped at the old fixed pause, so no wait is ever longer than before. A
stabilization timeout first gets an in-place second chance on the same page
(`readiness.finalize` with the longer retry window) before the capture is marked unstable;
only asset/other errors re-navigate in a fresh context. Page scripts that wait
(`readiness.JS_WAIT_FOR_FONTS`, `readiness.JS_DECODE_VISIBLE_IMAGES`) bound themselves and
run via `jsutil.evaluate_self_bounded`.

## Remote browser

Browsers run in a **separate container** that lives in its own **sibling repo**
`everneu-screenshots-browsers` (clone it next to this one) — official Playwright image +
branded Chrome/Edge, not bundled into the api/worker image. Its entrypoint
`browser-server.mjs` starts **one `launchServer` per browser, each on its own port** —
chrome 3001, msedge 3002, firefox 3003, webkit 3004 — so all four render with their real
engines/channels (chrome/msedge are the real branded binaries; chrome is the default). The
`chromium` server (port 3000) is retired: `chromium` survives only as an alias of `chrome`
(`normalize_browsers` in [projects/models.py](app/projects/models.py), applied by the input
validators, the worker's combo list and `_launch_browser`). `launchServer`
(not `run-server`) is required because the channel + launch args must be baked in
server-side: `connect()` can't pass them. `_launch_browser` in
[tasks/worker.py](app/tasks/worker.py) is the single chokepoint: when `BROWSER_WS_ENDPOINT`
(a **base host without port**) is set it resolves the port from `_BROWSER_WS_PORTS` and
`connect()`s to `f"{base}:{port}/"` (engine type via `_resolve_browser_type`, so chromium for
chrome/msedge); when empty it falls back to local `launch()` (fake-based tests + non-Docker
dev). The worker still writes files to `DATA_DIR`, so **storage is unchanged**. Dev builds the
sibling repo's image via the build context in [compose.dev.yml](compose.dev.yml); prod deploys
the sibling repo standalone on a separate host (`docker compose up -d --build` there).
Gotchas — the cross-repo contract is **manual** (no automated check; it's documented in both
READMEs): the port map is duplicated in the sibling's `browser-server.mjs` and
`_BROWSER_WS_PORTS` — **keep them in sync**; the sibling Dockerfile's `PLAYWRIGHT_VERSION`
**must match** the `playwright` pip version (`pyproject.toml`); its `CHROMIUM_ARGS` mirror
`_CHROMIUM_LAUNCH_ARGS` minus `--disable-dev-shm-usage`; the servers have no auth and
`BROWSER_WS_PATH` is not a secret (`GET /json` returns it) — firewall ports 3001–3004;
branded channels add ~1–1.5 GB to the image.

## Frontend conventions

Under [frontend/src/](frontend/src/):

- **Layout:** `api/` (one module per domain), `components/ui` (shadcn primitives +
  shared app blocks) + feature components, `hooks/`, `lib/`, `pages/`, `store/`
  (Zustand: `auth`, `theme`, `layout`, `breadcrumbs`), `types/index.ts` (mirrors the
  backend schemas).
- **UI kit: shadcn/ui, style `radix-nova`** (Radix primitives via the `radix-ui`
  package, neutral oklch tokens in `src/index.css`). Add components with
  `npx shadcn@latest add <name>` from `frontend/` on the host — never inside the
  container. Install files as-is; adapt them through app wrappers (`FormField` on
  `Field`, `ConfirmDialog` on `AlertDialog`, `StateMessage` on `Empty`, `DataTable`
  on `Table`, `StatusBadge` on `Badge`) instead of editing the primitives.
  `lib/utils.ts` only re-exports `cn` (the CLI owns it); app helpers live in
  `lib/helpers.ts`. Status colors are tokens (`bg-status-*-bg`, `text-success`,
  `text-warning`) — no raw palette classes.
- **Shell:** `components/layout/AppLayout.tsx` = shadcn `SidebarProvider` + `AppSidebar`
  + sticky `SiteHeader` (breadcrumb, theme menu). Pages name themselves in the
  breadcrumb with `useBreadcrumbs([...])`; the page scrolls with the window.
- **`@/` alias** resolves to `src/`.
- **One axios instance** — [api/client.ts](frontend/src/api/client.ts): `withCredentials`,
  `baseURL = VITE_API_URL ?? "/api"`, and a global response interceptor that on `401`
  fires an `auth:logout` event and redirects to `/login` (skipping public auth pages),
  and toasts on network errors.
- **Data fetching** via **TanStack Query**; route guards `ProtectedRoute` /
  `PublicRoute` / `AdminRoute` and the `AuthBootstrap` wrapper live in
  [App.tsx](frontend/src/App.tsx). Forms use react-hook-form + zod; toasts use sonner.
- **Dates:** render API timestamps with `<DateTime value relative?>`
  (components/ui/DateTime.tsx); sort/compare with `apiDateMs`
  ([lib/datetime.ts](frontend/src/lib/datetime.ts)). Never `new Date(apiString)` or
  `toLocaleString()` directly — the module reads offset-less values as UTC and
  formats in the profile time zone ("Oct 20, 2026, 15:42").

## Testing

pytest + pytest-asyncio (`asyncio_mode=auto`, `testpaths=["tests"]`).
[tests/conftest.py](tests/conftest.py) sets the required env vars **before** any app
module is imported (so `Settings` builds without a real `.env`). The suite favors
hand-rolled **fakes** — `_FakeRedis`, `_FakeBrowser`, `_FakePlaywrightContext`,
`_FakeUserSession`, `_FakeScalarResult` — so unit tests run without real Postgres,
Redis, or Playwright. Follow that fake-based style for new tests; name files `test_*.py`.

## Migrations

Alembic, in [alembic/versions/](alembic/versions/) — sequential, chained revisions
with descriptive slugs. Add a migration for **any** model change. Dev applies them
via `alembic upgrade head` inside the `api` container; prod runs the `migrate`
compose service.

## Gotchas worth respecting

- **Capture scalars before the session closes.** In async/worker code, read the
  values you need into locals before the `AsyncSession` context exits, to avoid
  `DetachedInstanceError` on expired ORM attributes (the pattern throughout
  [tasks/worker.py](app/tasks/worker.py)).
- **Never block the event loop** — offload CPU/sync work (e.g. zipping) via
  `run_in_executor`.
- **Don't clobber an externally set `cancelled` status** when finalizing a task —
  write terminal statuses through `finish_task`.
- **Storage paths are posix** (Linux/Docker target) — keep `/` separators.
- **`override_browser` (singular) is legacy**; prefer `override_browsers` (list).

## Key files

| Area | File |
|---|---|
| App factory, middleware, routers | [app/main.py](app/main.py) |
| Config / dual-config settings | [app/core/config.py](app/core/config.py), [app/settings/service.py](app/settings/service.py) |
| Typed HTTP errors | [app/core/exceptions.py](app/core/exceptions.py) |
| Async DB session | [app/core/database.py](app/core/database.py) |
| Auth deps | [app/auth/dependencies.py](app/auth/dependencies.py) |
| Task dispatch / lifecycle | [app/tasks/service.py](app/tasks/service.py), [app/tasks/lifecycle.py](app/tasks/lifecycle.py) |
| UTC time helpers | [app/core/time.py](app/core/time.py) |
| Operator CLI | [app/cli.py](app/cli.py) |
| Capture pipeline (ARQ jobs) | [app/tasks/worker.py](app/tasks/worker.py) |
| Capture + signed-URL storage | [app/screenshots/capture/](app/screenshots/capture/), [app/screenshots/service.py](app/screenshots/service.py), [app/screenshots/storage.py](app/screenshots/storage.py) |
| Worker entrypoint | [app/worker/main.py](app/worker/main.py) |
| Frontend routing / api client / types | [frontend/src/App.tsx](frontend/src/App.tsx), [frontend/src/api/client.ts](frontend/src/api/client.ts), [frontend/src/types/index.ts](frontend/src/types/index.ts) |
| Test fakes & fixtures | [tests/conftest.py](tests/conftest.py) |

The core domain abstractions (graphify "god nodes"): `Screenshot`, `User`, `Task`,
`EffectiveConfig`, `ProjectConfig`, `Project`.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
