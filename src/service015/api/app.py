"""FastAPI app factory for the Service 015 control plane."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from importlib import resources
from importlib.metadata import version as _pkg_version
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from service015 import accounts
from service015.api.routers import accounts as accounts_routes
from service015.api.routers import admin, expert, health, identities, meeting, phone, templates, ui, voices
from service015.auth import RANK, Forbidden, ensure_author, resolve_principal
from service015.config import Settings, check_api_keys_for_deployment, get_settings
from service015.db import connect, migrate
from service015.errors import DomainError
from service015.expert.llm import Llm, OpenAICompatLlm
from service015.runtime.latency import purge_old_text
from service015.runtime.supervisor import LiveKitSupervisor
from service015.runtime.supervisor_protocol import NullSupervisor, RuntimeSupervisor
from service015.telephony.service014 import Service014Client
from service015.templates_repo import seed_starter_templates
from service015.voice.engine import VoiceEngineClient
from service015.voice.voicebox import VoiceboxClient

log = logging.getLogger("service015.api")


def package_version() -> str:
    return _pkg_version("service015")


def error_response(
    status: int, code: str, message: str, details: dict[str, Any] | None = None, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details or {}}}
    return JSONResponse(body, status_code=status, headers=headers)


def create_app(
    settings: Settings,
    supervisor: RuntimeSupervisor | None = None,
    telephony: Service014Client | None = None,
    expert_llm: Llm | None = None,
    voicebox: VoiceboxClient | None = None,
    voice_engine: VoiceEngineClient | None = None,
) -> FastAPI:
    check_api_keys_for_deployment(settings)
    accounts.check_bootstrap_password(settings.admin_password)
    # Packaged copy of the repo-root openapi.yaml (a test keeps them byte-identical); raises if missing.
    openapi_yaml = resources.files("service015").joinpath("openapi.yaml").read_bytes()
    if not settings.api_keys:
        log.warning("SERVICE015_API_KEYS is empty: every protected route will answer 401")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = connect(settings.db_path)
        try:
            migrate(conn)
            accounts.warm_up()
            accounts.bootstrap_admin(conn, settings.admin_password, settings.public_url)  # adopts pre-existing rows
            seed_starter_templates(conn)
            purged = purge_old_text(conn, settings.transcript_retention_days)
            if purged:
                log.info("purged conversation text from %d turn_metrics rows older than %d days", purged, settings.transcript_retention_days)
        finally:
            conn.close()
        start = getattr(app.state.supervisor, "startup", None)
        if start is not None:
            await start()  # adopt a live runtime worker or mark orphaned rows OFFLINE runtime_lost
        try:
            yield
        finally:
            close = getattr(app.state.supervisor, "aclose", None)
            if close is not None:
                await close()  # stops the runtime worker (and its job processes) this API spawned

    app = FastAPI(
        title="SAFI Service 015",
        version=package_version(),
        lifespan=lifespan,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    if supervisor is None:
        # LiveKit is "configured" when its URL and credentials were provided (env / .env), not merely defaulted.
        lk_fields = {"livekit_url", "livekit_api_key", "livekit_api_secret"}
        configured = lk_fields <= settings.model_fields_set and all(getattr(settings, f) for f in lk_fields)
        if configured:
            supervisor = LiveKitSupervisor(settings)
        else:
            missing = sorted(lk_fields - settings.model_fields_set)
            log.warning(
                "LiveKit is not configured (set %s): launch/stop will answer 503 `runtime not configured`",
                ", ".join(v.upper() for v in missing) or "LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET",
            )
            supervisor = NullSupervisor(settings.db_path)
    app.state.supervisor = supervisor
    app.state.telephony = telephony if telephony is not None else Service014Client.from_settings(settings)
    app.state.expert_llm = expert_llm if expert_llm is not None else OpenAICompatLlm.from_settings(settings)
    app.state.voicebox = voicebox if voicebox is not None else VoiceboxClient(settings.voicebox_url, settings.voicebox_timeout_s)
    app.state.voice_engine = (
        voice_engine if voice_engine is not None else VoiceEngineClient(settings.voice_engine_url, settings.voice_engine_timeout_s)
    )
    app.state.version = package_version()
    app.state.openapi_yaml = openapi_yaml

    @app.middleware("http")
    async def _limit_voice_upload(request: Request, call_next):
        # Starlette spools a multipart body before any handler runs, so the cap must be enforced on Content-Length here.
        if request.method == "POST" and request.url.path == "/voices":
            # Authenticate BEFORE any body is read, so an anonymous client cannot force a multipart spool (401/403 answer here).
            conn = connect(settings.db_path)
            try:
                principal = resolve_principal(request, conn)
            except DomainError as exc:
                return error_response(exc.status, exc.code, exc.message, exc.details, getattr(exc, "headers", None))
            finally:
                conn.close()
            try:
                if RANK[principal.effective_role] < RANK["operator"]:
                    raise Forbidden(
                        "forbidden",
                        f"this operation requires role 'operator'; your {principal.describe()} has role {principal.role!r}",
                        details={"required_role": "operator", "role": principal.role},
                    )
                ensure_author(principal)
            except DomainError as exc:
                return error_response(exc.status, exc.code, exc.message, exc.details)
            length = request.headers.get("content-length", "")
            if not length.isdigit() or int(length) > voices.MAX_BODY_BYTES:
                return error_response(
                    413,
                    "sample_too_large",
                    "a Content-Length no larger than the reference-audio limit is required",
                    {"max_bytes": voices.MAX_SAMPLE_BYTES},
                )
        return await call_next(request)

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

    app.state.routers = (
        health.router, accounts_routes.router, admin.router, templates.router, identities.router, meeting.router, phone.router,
        expert.router, voices.router,
    )
    for router in app.state.routers:
        app.include_router(router)
    app.include_router(ui.router)  # static Workbench UI: unauthenticated, not part of openapi.yaml
    return app


def main() -> None:
    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
