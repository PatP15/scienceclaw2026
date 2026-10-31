"""FastAPI application factory.

Run:  uv run uvicorn fermentai.api.app:app --host 0.0.0.0 --port 8000
      (or scripts/start_demo.sh)
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .db import Database
from .devices import CommandError, ControlService, UnknownChamber, set_default_service
from .feedback import jsonable
from .routes import router

log = logging.getLogger("fermentai.api")

PLACEHOLDER_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FermentAI backend</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;max-width:46rem;margin:2rem auto;
padding:0 1rem;line-height:1.5;color:#222;background:#fafaf7}code{background:#eee;padding:0 .3em}
a{color:#7a4b00}</style></head><body>
<h1>FermentAI backend is running</h1>
<p>The dashboard (<code>src/fermentai/dashboard/static/</code>) is not installed yet, so this
placeholder is served instead.</p>
<ul>
<li><a href="/api/v1/health">/api/v1/health</a></li>
<li><a href="/api/v1/chambers">/api/v1/chambers</a> (state of A and B)</li>
<li><a href="/api/v1/commands">/api/v1/commands</a> (command log)</li>
<li><a href="/api/v1/events">/api/v1/events</a> (event log; SSE at /api/v1/events/stream)</li>
<li><a href="/api/v1/predictions">/api/v1/predictions</a>,
<a href="/api/v1/molecular/summary">/api/v1/molecular/summary</a>,
<a href="/api/v1/stl">/api/v1/stl</a>, <a href="/api/v1/findings">/api/v1/findings</a></li>
<li><a href="/openapi.json">/openapi.json</a> (the /docs UI needs internet for its assets)</li>
</ul></body></html>"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.db_path)
    service = ControlService(db, settings.hardware)
    set_default_service(service)

    app = FastAPI(title="FermentAI local API", version="1.0.0",
                  description="Local LAN backend for FermentAI chambers, dashboard and agents.")
    app.state.settings = settings
    app.state.service = service

    if settings.token_is_default:
        log.warning("FERMENTAI_API_TOKEN is unset or the .env.example default; "
                    "set a unique token in .env before the demo")

    origins = os.environ.get("FERMENTAI_CORS_ORIGINS")
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins.split(","), allow_methods=["*"],
                           allow_headers=["*"])

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        # jsonable() maps NaN/inf inputs to null so the 422 body itself is valid JSON.
        return JSONResponse({"detail": jsonable(exc.errors())}, status_code=422)

    @app.exception_handler(UnknownChamber)
    async def _unknown_chamber(_: Request, exc: UnknownChamber):
        return JSONResponse({"detail": exc.args[0]}, status_code=404)

    @app.exception_handler(CommandError)
    async def _command_error(_: Request, exc: CommandError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    app.include_router(router)

    static_dir = settings.dashboard_static_dir
    app.state.dashboard_static = static_dir.is_dir() and any(static_dir.iterdir())
    if app.state.dashboard_static:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="dashboard")
    else:
        @app.get("/", include_in_schema=False)
        def _placeholder():
            return HTMLResponse(PLACEHOLDER_HTML)

    return app


def __getattr__(name: str):
    # Lazily build the module-level `app` so importing this module (e.g. in tests) has no
    # side effects on the default DB.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
