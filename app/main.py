"""FastAPI application entrypoint."""

import utils.transport
import utils.session
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.v1.router import api_router
from app.core.config import clear_settings_cache, get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger, setup_logging
from app.core.supabase import check_supabase_connection, get_supabase_client, is_supabase_configured
from app.schemas.common import error_response, success_response

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Re-read .env on every process start / --reload (settings are otherwise cached).
    clear_settings_cache()
    settings = get_settings()
    setup_logging(debug=settings.debug)
    logger.info(
        "Starting %s v%s (env=%s)",
        settings.app_name,
        __version__,
        settings.app_env,
    )
    logger.info("CORS origins: %s", settings.get_cors_origins())
    logger.info(
        "OpenRouter configured=%s model=%s",
        settings.openrouter_configured,
        settings.get_openrouter_model() if settings.openrouter_configured else None,
    )

    if is_supabase_configured(settings):
        probe = check_supabase_connection(settings)
        if probe.get("connected"):
            # Eagerly create the service client so misconfig fails early.
            get_supabase_client("service")
            logger.info(
                "Supabase connected (%s)",
                settings.supabase_url,
            )
        else:
            logger.warning(
                "Supabase is configured but unreachable (status=%s)",
                probe.get("status"),
            )
    else:
        logger.info("Supabase not configured — running without remote DB")

    yield
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "ResearchFlow Multi-Agent Research & Report Generator API "
            "(Phases 1–10 complete).\n\n"
            "## Workflow\n"
            "1. Create run → 2. Generate/edit/approve plan → 3. Start research "
            "(parallel agents) → 4. Generate & verify report → 5. Usage/cost → "
            "6. Export (md/pdf/docx) → 7. History + timeline replay.\n\n"
            "## Reliability (Phase 10)\n"
            "- Tool timeout 30s, max 2 retries, exponential backoff\n"
            "- Global run timeout (`RUN_TIMEOUT_S`) and cost ceiling (`RUN_COST_LIMIT_USD`)\n"
            "- Cooperative cancellation via `POST /runs/{id}/cancel`\n"
            "- Failed researcher isolation; partial results preserved\n"
            "- Legal status transitions enforced\n\n"
            "All responses use `{ success, message, data }` (errors: `{ success, message, detail }`)."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        logger.warning("AppError: %s detail=%s", exc.message, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(exc.message, exc.detail),
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        detail: dict[str, Any] = {}
        if not isinstance(exc.detail, str):
            detail = {"error": exc.detail}
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(message, detail),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_response(
                "Validation failed",
                {"errors": jsonable_encoder(exc.errors())},
            ),
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_response(
                "Internal server error",
                {"type": type(exc).__name__},
            ),
        )

    application.include_router(api_router, prefix=settings.api_v1_prefix)

    @application.get("/")
    def root() -> dict:
        return success_response(
            data={
                "service": settings.app_name,
                "version": __version__,
                "docs": "/docs",
                "health": f"{settings.api_v1_prefix}/health",
            },
            message="Multi-Agent Research API",
        )

    return application


app = create_app()
