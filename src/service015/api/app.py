"""FastAPI app factory for the Service 015 control plane."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from service015.api.routers import health, identities, meeting, templates
from service015.config import Settings, get_settings
from service015.db import connect, migrate
from service015.errors import DomainError
from service015.runtime.supervisor_protocol import NullSupervisor, RuntimeSupervisor
from service015.templates_repo import seed_starter_templates

log = logging.getLogger("service015.api")
OPENAPI_PATH = Path(__file__).resolve().parents[3] / "openapi.yaml"


def package_version() -> str:
    return _pkg_version("service015")


def error_response(
    status: int, code: str, message: str, details: dict[str, Any] | None = None, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details or {}}}
    return JSONResponse(body, status_code=status, headers=headers)


def create_app(settings: Settings, supervisor: RuntimeSupervisor | None = None) -> FastAPI:
    if not OPENAPI_PATH.is_file():
        raise FileNotFoundError(f"openapi.yaml not found at {OPENAPI_PATH}")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = connect(settings.db_path)
        try:
            migrate(conn)
            seed_starter_templates(conn)
        finally:
            conn.close()
        yield

    app = FastAPI(
        title="SAFI Service 015",
        version=package_version(),
        lifespan=lifespan,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.supervisor = supervisor if supervisor is not None else NullSupervisor(settings.db_path)
    app.state.version = package_version()
    app.state.openapi_yaml = OPENAPI_PATH.read_bytes()

    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.details, getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": [str(p) for p in e["loc"]], "msg": str(e["msg"]), "type": str(e["type"])} for e in exc.errors()]
        return error_response(422, "invalid_request", "request validation failed", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, f"http_{exc.status_code}")
        return error_response(exc.status_code, code, str(exc.detail), headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return error_response(500, "internal_error", "internal server error")

    app.state.routers = (health.router, templates.router, identities.router, meeting.router)
    for router in app.state.routers:
        app.include_router(router)
    return app


def main() -> None:
    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
