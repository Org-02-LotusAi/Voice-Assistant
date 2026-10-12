"""Template routes (+ instantiate)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

from service015 import identities_repo, templates_repo
from service015.api.deps import get_conn, get_voicebox
from service015.api.schemas import InstantiateRequest, TemplatePatch
from service015.auth import Forbidden, Principal, ensure_author, require_principal
from service015 import accounts
from service015.errors import Conflict, ValidationFailed
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
    body: TemplateCreate, principal: Principal = Depends(require_principal("operator")), conn: sqlite3.Connection = Depends(get_conn)
):
    """Admin: a shared template (new family, or the next version of any shared family). User: a private template owned by the
    caller (new family) or the next version of a private family they own. Same validation either way."""
    ensure_author(principal)
    is_admin = principal.effective_role == "admin"

    def authorize(meta: sqlite3.Row | None) -> None:  # runs inside the write transaction
        if meta is None:
            return
        if not principal.visibility.allows(meta["owner_user_id"], bool(meta["shared"])):
            raise Conflict(  # the same answer whether or not the id exists privately for someone else
                "template_id_taken", f"template_id {body.template_id!r} is already in use; choose another",
                details={"template_id": body.template_id},
            )
        if meta["shared"]:
            if not is_admin:
                raise Forbidden(
                    "forbidden", "shared templates can only be versioned by an administrator",
                    details={"required_role": "admin", "role": principal.role},
                )
        elif principal.user_id is None or meta["owner_user_id"] != principal.user_id:
            raise Forbidden(
                "template_not_owned", "a private template can only be versioned by its owner",
                details={"template_id": body.template_id},
            )

    return templates_repo.create_template(
        conn, body, owner_user_id=principal.owner_id_for_new, shared=is_admin, authorize=authorize
    )


@router.patch("/templates/{template_id}", response_model=TemplateSummary)
def patch_template(
    template_id: str,
    body: TemplatePatch,
    principal: Principal = Depends(require_principal("admin")),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Admin: disable/enable (blocks new instantiation only), change sharing, or hand the template to a user."""
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if not changes:
        raise ValidationFailed("empty_patch", "give at least one of disabled, shared, owner_user_id")
    for key in ("disabled", "shared"):
        if key in changes and changes[key] is None:
            raise ValidationFailed("invalid_request", f"{key} must be true or false", details={"field": key})
    if changes.get("owner_user_id") is not None:
        accounts.get_user(conn, changes["owner_user_id"])  # 404 user_not_found
    return templates_repo.patch_template(conn, template_id, changes)


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
