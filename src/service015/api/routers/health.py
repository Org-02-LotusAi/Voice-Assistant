"""Health and static contract routes (no auth)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from service015.api.schemas import Health
from service015.db import connect
from service015.runtime.supervisor_protocol import NullSupervisor

log = logging.getLogger("service015.api")
router = APIRouter(tags=["health"])


@router.get("/health")
def get_health(request: Request) -> JSONResponse:
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
    runtime = "not_configured" if isinstance(request.app.state.supervisor, NullSupervisor) else "ok"
    body = Health(
        status="ok" if database == "ok" else "degraded",
        version=request.app.state.version,
        dependencies={"database": database, "runtime": runtime},
    )
    return JSONResponse(body.model_dump(), status_code=200 if database == "ok" else 503)


@router.get("/openapi.yaml")
def get_openapi_yaml(request: Request) -> Response:
    return Response(request.app.state.openapi_yaml, media_type="application/yaml")
