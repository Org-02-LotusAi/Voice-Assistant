"""Health and static contract routes (no auth)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from service015.api.schemas import Health
from service015.db import connect
from service015.runtime.supervisor_protocol import NullSupervisor
from service015.voice.engine import VoiceEngineClient
from service015.voice.voicebox import VoiceboxClient

log = logging.getLogger("service015.api")
router = APIRouter(tags=["health"])


@router.get("/health")
async def get_health(request: Request) -> JSONResponse:
    database = "ok"
    try:
        conn = connect(request.app.state.settings.db_path)
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
    except Exception:  # health must report the failure, not raise it
        log.exception("health check: database unreachable")
        database = "error"
    voicebox: VoiceboxClient = request.app.state.voicebox
    engine: VoiceEngineClient = request.app.state.voice_engine
    voicebox_state, engine_state = await asyncio.gather(voicebox.health(), engine.health())  # never raise
    runtime = "not_configured" if isinstance(request.app.state.supervisor, NullSupervisor) else "ok"
    body = Health(
        status="ok" if database == "ok" else "degraded",
        version=request.app.state.version,
        dependencies={"database": database, "runtime": runtime, "voicebox": voicebox_state, "voice_engine": engine_state},
    )
    return JSONResponse(body.model_dump(), status_code=200 if database == "ok" else 503)


@router.get("/openapi.yaml")
def get_openapi_yaml(request: Request) -> Response:
    return Response(request.app.state.openapi_yaml, media_type="application/yaml")
