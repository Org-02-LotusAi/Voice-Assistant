"""Template routes (+ instantiate)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from service015 import identities_repo, templates_repo
from service015.api.deps import get_conn, get_voicebox
from service015.api.schemas import InstantiateRequest
from service015.auth import Principal, require_principal
from service015.models import TemplateBody, TemplateCreate, TemplateSummary
from service015.resolver import ResolvedIdentity
from service015.voice.authz import ensure_voice_usable
from service015.voice.voicebox import VoiceboxClient

router = APIRouter(tags=["templates"])


@router.get("/templates", response_model=list[TemplateSummary])
def list_templates(principal: Principal = Depends(require_principal("viewer")), conn: sqlite3.Connection = Depends(get_conn)):
    return templates_repo.list_templates(conn, principal.visibility)


@router.post("/templates", status_code=201, response_model=TemplateBody)
def create_template(
    body: TemplateCreate, principal: Principal = Depends(require_principal("admin")), conn: sqlite3.Connection = Depends(get_conn)
):
    return templates_repo.create_template(conn, body, owner_user_id=principal.owner_id_for_new, shared=True)


@router.get("/templates/{template_id}", response_model=TemplateBody)
def get_template(
    template_id: str,
    version: int | None = Query(default=None, ge=1),
    principal: Principal = Depends(require_principal("viewer")),
    conn: sqlite3.Connection = Depends(get_conn),
):
    templates_repo.ensure_visible(conn, template_id, principal.visibility)
    return templates_repo.get_template(conn, template_id, version)


@router.get("/templates/{template_id}/versions", response_model=list[int])
def list_versions(
    template_id: str, principal: Principal = Depends(require_principal("viewer")), conn: sqlite3.Connection = Depends(get_conn)
):
    templates_repo.ensure_visible(conn, template_id, principal.visibility)
    return templates_repo.list_versions(conn, template_id)


@router.post("/templates/{template_id}/instantiate", status_code=201, response_model=ResolvedIdentity)
async def instantiate(
    template_id: str,
    body: InstantiateRequest,
    principal: Principal = Depends(require_principal("operator")),
    conn: sqlite3.Connection = Depends(get_conn),
    voicebox: VoiceboxClient = Depends(get_voicebox),
):
    templates_repo.ensure_visible(conn, template_id, principal.visibility)  # 404 for templates the caller may not see
    template = templates_repo.get_template(conn, template_id, body.template_version)  # 404 before touching Voicebox
    templates_repo.ensure_enabled(conn, template_id)  # 409 template_disabled
    await ensure_voice_usable(
        conn, voicebox, body.overrides.get("voice.profile_id", template.voice.default_voice_profile_id), principal.visibility
    )
    return identities_repo.instantiate(
        conn, template_id, body.overrides, body.template_version, owner_user_id=principal.owner_id_for_new
    )
