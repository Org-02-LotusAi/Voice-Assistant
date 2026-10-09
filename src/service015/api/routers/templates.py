"""Template routes (+ instantiate)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from service015 import identities_repo, templates_repo
from service015.api.deps import get_conn, get_voicebox
from service015.api.schemas import InstantiateRequest
from service015.auth import require_role
from service015.models import TemplateBody, TemplateCreate, TemplateSummary
from service015.resolver import ResolvedIdentity
from service015.voice.authz import ensure_voice_usable
from service015.voice.voicebox import VoiceboxClient

router = APIRouter(tags=["templates"])


@router.get("/templates", response_model=list[TemplateSummary], dependencies=[Depends(require_role("viewer"))])
def list_templates(conn: sqlite3.Connection = Depends(get_conn)):
    return templates_repo.list_templates(conn)


@router.post("/templates", status_code=201, response_model=TemplateBody, dependencies=[Depends(require_role("admin"))])
def create_template(body: TemplateCreate, conn: sqlite3.Connection = Depends(get_conn)):
    return templates_repo.create_template(conn, body)


@router.get("/templates/{template_id}", response_model=TemplateBody, dependencies=[Depends(require_role("viewer"))])
def get_template(
    template_id: str, version: int | None = Query(default=None, ge=1), conn: sqlite3.Connection = Depends(get_conn)
):
    return templates_repo.get_template(conn, template_id, version)


@router.get("/templates/{template_id}/versions", response_model=list[int], dependencies=[Depends(require_role("viewer"))])
def list_versions(template_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    return templates_repo.list_versions(conn, template_id)


@router.post(
    "/templates/{template_id}/instantiate",
    status_code=201,
    response_model=ResolvedIdentity,
    dependencies=[Depends(require_role("operator"))],
)
async def instantiate(
    template_id: str,
    body: InstantiateRequest,
    conn: sqlite3.Connection = Depends(get_conn),
    voicebox: VoiceboxClient = Depends(get_voicebox),
):
    template = templates_repo.get_template(conn, template_id, body.template_version)  # 404 before touching Voicebox
    await ensure_voice_usable(
        conn, voicebox, body.overrides.get("voice.profile_id", template.voice.default_voice_profile_id)
    )
    return identities_repo.instantiate(conn, template_id, body.overrides, body.template_version)
