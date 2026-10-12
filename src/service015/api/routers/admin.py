"""Administration: accounts, service tokens, all-owner persona and voice listings, owner hand-over."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from service015 import accounts, identities_repo
from service015.api.deps import get_conn, get_voicebox
from service015.api.routers.identities import _summary
from service015.api.routers.voices import ENGINE
from service015.api.schemas import (
    AdminIdentity,
    AdminUser,
    AdminUserCreate,
    AdminUserPatch,
    AdminVoice,
    IdentityOwnerPatch,
    ServiceTokenCreate,
    ServiceTokenCreated,
    ServiceTokenInfo,
)
from service015.auth import Principal, require_principal
from service015.errors import ValidationFailed
from service015.voice import authz
from service015.voice.voicebox import VoiceboxClient

log = logging.getLogger("service015.api")
router = APIRouter(tags=["admin"])


def _user(row: sqlite3.Row) -> AdminUser:
    return AdminUser(
        user_id=row["user_id"], username=row["username"], role=row["role"],
        must_change_password=bool(row["must_change_password"]), disabled=bool(row["disabled"]),
        created_at=row["created_at"], updated_at=row["updated_at"], last_login_at=row["last_login_at"],
    )


def _token(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in ("token_id", "label", "role", "created_at", "last_used_at", "revoked_at")}


@router.get("/admin/users", response_model=list[AdminUser])
def list_users(principal: Principal = Depends(require_principal("admin")), conn: sqlite3.Connection = Depends(get_conn)):
    return [_user(r) for r in accounts.list_users(conn)]


@router.post("/admin/users", status_code=201, response_model=AdminUser)
def create_user(
    body: AdminUserCreate, principal: Principal = Depends(require_principal("admin")), conn: sqlite3.Connection = Depends(get_conn)
):
    """Creates an account with a temporary password; the user must change it at first sign-in."""
    row = accounts.admin_create_user(conn, body.username, body.temporary_password, body.role)
    log.info("admin created user=%s role=%s", row["user_id"], row["role"])
    return _user(row)


@router.patch("/admin/users/{user_id}", response_model=AdminUser)
def patch_user(
    user_id: str,
    body: AdminUserPatch,
    principal: Principal = Depends(require_principal("admin")),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Disable/enable, change role (user/viewer) and/or reset the temporary password. Sessions are revoked at once."""
    if not body.model_fields_set:
        raise ValidationFailed("empty_patch", "give at least one of disabled, role, temporary_password")
    row = accounts.admin_update_user(
        conn, user_id, disabled=body.disabled, role=body.role, temporary_password=body.temporary_password
    )
    log.info("admin updated user=%s fields=%s", user_id, sorted(body.model_fields_set))
    return _user(row)


@router.get("/admin/tokens", response_model=list[ServiceTokenInfo])
def list_tokens(principal: Principal = Depends(require_principal("admin")), conn: sqlite3.Connection = Depends(get_conn)):
    return [_token(r) for r in accounts.list_service_tokens(conn)]


@router.post("/admin/tokens", status_code=201, response_model=ServiceTokenCreated)
def create_token(
    body: ServiceTokenCreate, principal: Principal = Depends(require_principal("admin")), conn: sqlite3.Connection = Depends(get_conn)
):
    """The secret is in this response only; only its sha256 is stored."""
    row, secret = accounts.create_service_token(conn, body.label.strip(), body.role, principal.user_id)
    log.info("service token created token=%s role=%s", row["token_id"], row["role"])
    return {**_token(row), "token": secret}


@router.delete("/admin/tokens/{token_id}", status_code=204, response_class=Response)
def revoke_token(
    token_id: str, principal: Principal = Depends(require_principal("admin")), conn: sqlite3.Connection = Depends(get_conn)
):
    accounts.revoke_service_token(conn, token_id)
    log.info("service token revoked token=%s", token_id)
    return Response(status_code=204)


@router.get("/admin/identities", response_model=list[AdminIdentity])
def list_all_identities(
    include_archived: bool = Query(default=False),
    principal: Principal = Depends(require_principal("admin")),
    conn: sqlite3.Connection = Depends(get_conn),
):
    owners, archived = identities_repo.owner_map(conn), identities_repo.archived_map(conn)
    return [
        AdminIdentity(**_summary(r, owners.get(r.identity_id)).model_dump(), archived_at=archived.get(r.identity_id))
        for r in identities_repo.list_identities(conn, None, include_archived=include_archived)
    ]


@router.patch("/admin/identities/{identity_id}", response_model=AdminIdentity)
def assign_identity_owner(
    identity_id: str,
    body: IdentityOwnerPatch,
    principal: Principal = Depends(require_principal("admin")),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Hand a persona (also an archived or machine-created one) to a user; null makes it unowned."""
    if body.owner_user_id is not None:
        accounts.get_user(conn, body.owner_user_id)  # 404 user_not_found
    identities_repo.set_owner(conn, identity_id, body.owner_user_id)
    log.info("identity owner set identity=%s owner=%s", identity_id, body.owner_user_id)
    resolved = identities_repo.get_scoped(conn, identity_id, None, include_archived=True)
    return AdminIdentity(
        **_summary(resolved, body.owner_user_id).model_dump(),
        archived_at=identities_repo.archived_map(conn)[identity_id],
    )


@router.get("/admin/voices", response_model=list[AdminVoice])
async def list_all_voices(
    principal: Principal = Depends(require_principal("admin")),
    conn: sqlite3.Connection = Depends(get_conn),
    voicebox: VoiceboxClient = Depends(get_voicebox),
):
    """Every cloned Voicebox profile with owner, shared/disabled flags and the recorded consent."""
    index, consent = authz.voice_index(conn), authz.consent_index(conn)
    out = []
    for p in await voicebox.list_profiles():
        if p.get("voice_type") != "cloned":
            continue
        meta = index.get(p["id"])
        out.append(
            AdminVoice(
                voice_profile_id=p["id"], name=p["name"], language=p.get("language") or "en", engine=ENGINE,
                authorized=meta is not None,
                owner_user_id=meta["owner_user_id"] if meta else None,
                shared=meta["shared"] if meta else False,
                disabled=meta["disabled"] if meta else False,
                consent=consent.get(p["id"]),
            )
        )
    return out
