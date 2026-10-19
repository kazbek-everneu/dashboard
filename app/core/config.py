from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_SECRET_KEY = "change-me-to-a-random-secret-key"
_DEFAULT_CORS_ALLOWED_ORIGINS = ",".join(
    [
        "http://localhost:15173",
        "http://127.0.0.1:15173",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    REDIS_URL: str
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 48  # 48 hours
    ACCESS_TOKEN_COOKIE_NAME: str = "access_token"
    ACCESS_TOKEN_COOKIE_SECURE: bool = True
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30
    PASSWORD_RESET_RATE_LIMIT_MAX: int = 5
    PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS: int = 3600
    FRONTEND_BASE_URL: str = "http://localhost:15173"
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str = "no-reply@example.com"
    SMTP_USE_TLS: bool = True

    @field_validator("SECRET_KEY")
    @classmethod
    def _validate_secret_key(cls, v: str) -> str:
        if v == _DEFAULT_SECRET_KEY or len(v) < 32:
            raise ValueError(
                "SECRET_KEY must be changed from the default and be at least "
                "32 characters. Generate one with: python -c "
                '"import secrets; print(secrets.token_hex(32))"'
            )
        return v

    ACCESS_TOKEN_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    CORS_ALLOWED_ORIGINS: str = _DEFAULT_CORS_ALLOWED_ORIGINS
    DATA_DIR: str = "data"
    # Max pages captured at once by one worker process — shared by all running
    # tasks and re-captures (app.tasks.limiter.CaptureLimiter).
    SEM_LIMIT: int = 4
    # ARQ max_jobs: tasks/re-captures a worker runs at once. Read at worker boot
    # (restart to apply). Page concurrency is bounded by SEM_LIMIT regardless.
    WORKER_MAX_JOBS: int = 10
    # Capture all viewports of one URL in one browser through a shared browser
    # context (shared HTTP cache, cookies, localStorage). Retries always use a
    # fresh isolated context.
    REUSE_BROWSER_CONTEXT: bool = True
    # Base host of the remote browser server, WITHOUT a port (e.g. "ws://browser"
    # in dev, "ws://10.0.0.5" in prod). The worker appends the per-browser port
    # (3000-3004, see worker._BROWSER_WS_PORTS) and connects there instead of
    # launching locally. Empty = launch locally (requires `playwright install`);
    # keeps tests and non-Docker dev working.
    BROWSER_WS_ENDPOINT: str = ""
    # WebSocket path of the remote browser server. Must match the
    # BROWSER_WS_PATH env of the browser container (browser-server.mjs in the
    # sibling repo everneu-screenshots-browsers). The worker appends it to the
    # endpoint URL; empty = root path "/".
    BROWSER_WS_PATH: str = ""
    # Timeout (ms) for connecting to the remote browser server, so a dead
    # browser host fails fast instead of hanging the worker.
    BROWSER_CONNECT_TIMEOUT_MS: int = 30000

    @field_validator("BROWSER_WS_ENDPOINT")
    @classmethod
    def _validate_browser_ws_endpoint(cls, v: str) -> str:
        if not v:
            return v
        parts = urlsplit(v)
        if parts.scheme not in ("ws", "wss"):
            raise ValueError(
                f"BROWSER_WS_ENDPOINT must start with ws:// or wss:// (got {v!r})"
            )
        if parts.port is not None:
            raise ValueError(
                "BROWSER_WS_ENDPOINT must be a base host WITHOUT a port — the "
                "worker appends the per-browser port (3000-3004) itself "
                f"(got {v!r}; use e.g. {parts.scheme}://{parts.hostname})"
            )
        if parts.path.strip("/") or parts.query or parts.fragment:
            raise ValueError(
                "BROWSER_WS_ENDPOINT must not contain a path or query "
                f"(got {v!r}); set the ws path via BROWSER_WS_PATH"
            )
        return v

    TASK_JOB_TIMEOUT_SECONDS: int = 7200
    # Wall-clock bound for a single screenshot re-capture, independent of the
    # batch job timeout above. Keeps a slow/hung recapture from leaving the row
    # stuck in "recapturing" for hours.
    SCREENSHOT_RECAPTURE_TIMEOUT_SECONDS: int = 300

    @property
    def cors_allowed_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.CORS_ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]


settings = Settings()
