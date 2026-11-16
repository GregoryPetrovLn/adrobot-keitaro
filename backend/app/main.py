"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import dispose_engine
from app.errors import AppError
from app.routers import campaigns, dictionaries, offers

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("Keitaro API: %s", settings.keitaro_api_url)
    if not settings.keitaro_api_key:
        logger.warning("KEITARO_API_KEY is empty — Keitaro calls will fail until it is set.")

    app.state.http_client = httpx.AsyncClient(
        timeout=settings.keitaro_timeout,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AdRobot — Keitaro campaign creator & stream editor",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "keitaro_api_url": settings.keitaro_api_url,
            "keitaro_configured": bool(settings.keitaro_api_key),
        }

    app.include_router(dictionaries.router, prefix="/api")
    app.include_router(offers.router, prefix="/api")
    app.include_router(campaigns.router, prefix="/api")

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built React bundle when it exists (production single-process mode).

    In development the frontend runs on Vite's dev server and proxies /api here instead.
    """
    if not STATIC_DIR.is_dir():
        logger.info("no built frontend at %s — run `npm run build` to serve the UI", STATIC_DIR)
        return

    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = STATIC_DIR / "index.html"

    # response_model=None: the return type is a union of Response subclasses,
    # which FastAPI would otherwise try to turn into a Pydantic field.
    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    async def spa(full_path: str) -> FileResponse | JSONResponse:
        # Real API routes are registered before this catch-all and win. Anything else
        # under /api is a typo, and answering it with the SPA shell at HTTP 200 would
        # hide the mistake from any client that checks the status code.
        if full_path.startswith("api/") or full_path in {"api", "docs", "openapi.json"}:
            return JSONResponse(status_code=404, content={"message": "Not found."})

        candidate = (STATIC_DIR / full_path).resolve()
        if (
            full_path
            and STATIC_DIR in candidate.parents
            and candidate.is_file()
        ):
            return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(status_code=404, content={"message": "Frontend is not built."})


app = create_app()
