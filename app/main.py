import logging
import uuid as _uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


class _RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a short request ID to every response for log correlation."""

    async def dispatch(self, request: Request, call_next: object):  # type: ignore[override]
        request_id = str(_uuid.uuid4())[:8]
        request.state.request_id = request_id
        response = await call_next(request)  # type: ignore[operator]
        response.headers["X-Request-ID"] = request_id
        return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="QA Screenshot Tool",
        description="Automated visual regression testing via screenshot capture.",
        version="0.1.0",
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(_RequestIDMiddleware)

    # ---------------------------------------------------------------------------
    # Exception handlers
    # ---------------------------------------------------------------------------

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = []
        for error in exc.errors():
            loc = [str(part) for part in error["loc"] if part != "body"]
            field = " -> ".join(loc) if loc else "request"
            errors.append({"field": field, "message": error["msg"]})
        logger.debug("Validation error: %s", errors)
        return JSONResponse(status_code=422, content={"detail": errors})

    # ---------------------------------------------------------------------------
    # Routers
    # ---------------------------------------------------------------------------

    from app.auth.router import router as auth_router
    from app.projects.router import router as projects_router
    from app.screenshots.router import router as screenshots_router
    from app.settings.router import router as settings_router
    from app.tasks.router import router as tasks_router
    from app.users.router import router as users_router

    app.include_router(auth_router, prefix="/auth", tags=["auth"])
    app.include_router(projects_router, prefix="/projects", tags=["projects"])
    app.include_router(settings_router, prefix="/settings", tags=["settings"])
    app.include_router(tasks_router, prefix="/tasks", tags=["tasks"])
    app.include_router(users_router, prefix="/users", tags=["users"])
    app.include_router(screenshots_router, tags=["screenshots"])

    # ---------------------------------------------------------------------------
    # Health check
    # ---------------------------------------------------------------------------

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        from sqlalchemy import text

        from app.core.database import engine
        from app.core.redis import get_redis_pool

        db_status = "ok"
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning("Health check: DB unavailable — %s", exc)
            db_status = "error"

        redis_status = "ok"
        try:
            redis = await get_redis_pool()
            await redis.ping()
            await redis.close()
        except Exception as exc:
            logger.warning("Health check: Redis unavailable — %s", exc)
            redis_status = "error"

        overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"
        return {"status": overall, "db": db_status, "redis": redis_status}

    return app


app = create_app()
