"""Identity routes."""

from __future__ import annotations

import sqlite3
from typing import Any

import datetime
import secrets

from fastapi import APIRouter, Depends, Query, Request
from livekit import api as lkapi

from service015 import identities_repo
from service015.api.deps import get_conn, get_supervisor, get_voicebox
from service015.api.schemas import (
    ArchivedIdentity,
    IdentitySummary,
    JoinToken,
    JoinTokenRequest,
    LatencyReport,
    ParticipantStatus,
    PatchIdentityRequest,
    SelfContext,
)
from service015.auth import Principal, require_principal
from service015.resolver import ResolvedIdentity
from service015.runtime import latency
from service015.runtime.supervisor_protocol import RuntimeSupervisor
from service015.selfcontext import build_instructions, build_self_context
from service015.voice import authz
from service015.voice.authz import ensure_identity_voice_ready, ensure_voice_usable
from service015.voice.voicebox import VoiceboxClient

router = APIRouter(tags=["identities"])


def _summary(r: ResolvedIdentity, owner_user_id: str | None = None) -> IdentitySummary:
    return IdentitySummary(
        identity_id=r.identity_id,
        name=r.self_.name,
        template_id=r.template_id,
        template_version=r.template_version,
        company=r.self_.company,
        role=r.self_.role,
        language=r.self_.language,
        voice_profile_id=r.voice.profile_id,
        participant_identity=r.participant.participant_identity,
        default_room=r.participant.default_room,
        resolved_hash=r.resolved_hash,
        owner_user_id=owner_user_id,
    )


def scoped_identity(conn: sqlite3.Connection, principal: Principal, identity_id: str) -> ResolvedIdentity:
    """The identity if it exists, is not archived and the principal may act on it; otherwise 404 `identity_not_found`
    (a `user` asking for someone else's persona gets the same answer as for a missing one)."""
    return identities_repo.get_scoped(conn, identity_id, principal.owner_scope)


@router.get("/identities", response_model=list[IdentitySummary])
def list_identities(principal: Principal = Depends(require_principal("viewer")), conn: sqlite3.Connection = Depends(get_conn)):
    owners = identities_repo.owner_map(conn)
    return [_summary(r, owners.get(r.identity_id)) for r in identities_repo.list_identities(conn, principal.owner_scope)]


@router.get("/identities/{identity_id}", response_model=ResolvedIdentity)
def get_identity(
    identity_id: str, principal: Principal = Depends(require_principal("viewer")), conn: sqlite3.Connection = Depends(get_conn)
):
    return scoped_identity(conn, principal, identity_id)


@router.patch("/identities/{identity_id}", response_model=ResolvedIdentity)
async def patch_identity(
    identity_id: str,
    body: PatchIdentityRequest,
    request: Request,
    principal: Principal = Depends(require_principal("operator")),
    conn: sqlite3.Connection = Depends(get_conn),
    voicebox: VoiceboxClient = Depends(get_voicebox),
):
    scoped_identity(conn, principal, identity_id)
    before, after = identities_repo.patched_voice_profile_id(conn, identity_id, body.overrides)
    if after != before:  # an unchanged voice was validated when it was attached; unrelated patches don't need Voicebox
        await ensure_voice_usable(conn, voicebox, after, principal.visibility, request.app.state.settings.tts_provider)
    return identities_repo.patch_identity(conn, identity_id, body.overrides)


@router.delete("/identities/{identity_id}", response_model=ArchivedIdentity)
async def archive_identity(
    identity_id: str,
    principal: Principal = Depends(require_principal("operator")),
    conn: sqlite3.Connection = Depends(get_conn),
    sup: RuntimeSupervisor = Depends(get_supervisor),
) -> Any:
    """Archive: stop the runtime if it is not OFFLINE, hide the identity everywhere, keep the record. No hard delete."""
    scoped_identity(conn, principal, identity_id)
    if identities_repo.get_status(conn, identity_id).presence != "OFFLINE":
        await sup.stop(identity_id)  # a failure to stop propagates: never archive an identity that may still be running
    archived_at = identities_repo.archive_identity(conn, identity_id)
    return ArchivedIdentity(identity_id=identity_id, archived_at=archived_at)


@router.get("/identities/{identity_id}/self-context", response_model=SelfContext)
def get_self_context(
    identity_id: str, principal: Principal = Depends(require_principal("viewer")), conn: sqlite3.Connection = Depends(get_conn)
):
    r = scoped_identity(conn, principal, identity_id)
    return SelfContext(
        self_context=build_self_context(r), instructions=build_instructions(r), resolved_hash=r.resolved_hash
    )


@router.post("/identities/{identity_id}/launch", response_model=ParticipantStatus)
async def launch(
    identity_id: str,
    request: Request,
    principal: Principal = Depends(require_principal("operator")),
    conn: sqlite3.Connection = Depends(get_conn),
    sup: RuntimeSupervisor = Depends(get_supervisor),
    voicebox: VoiceboxClient = Depends(get_voicebox),
) -> Any:
    identity = scoped_identity(conn, principal, identity_id)  # 404 before touching the runtime
    owner = identities_repo.owner_map(conn).get(identity_id)
    # 422/503 before the supervisor is involved; the voice must still be usable by the persona's OWNER
    await ensure_identity_voice_ready(
        conn, voicebox, identity, authz.owner_visibility(conn, owner), request.app.state.settings.tts_provider
    )
    return await sup.launch(identity_id)


@router.post("/identities/{identity_id}/stop", response_model=ParticipantStatus)
async def stop(
    identity_id: str,
    principal: Principal = Depends(require_principal("operator")),
    conn: sqlite3.Connection = Depends(get_conn),
    sup: RuntimeSupervisor = Depends(get_supervisor),
) -> Any:
    scoped_identity(conn, principal, identity_id)
    return await sup.stop(identity_id)


@router.get("/identities/{identity_id}/status", response_model=ParticipantStatus)
async def status(
    identity_id: str,
    principal: Principal = Depends(require_principal("viewer")),
    conn: sqlite3.Connection = Depends(get_conn),
    sup: RuntimeSupervisor = Depends(get_supervisor),
) -> Any:
    scoped_identity(conn, principal, identity_id)
    return await sup.status(identity_id)


JOIN_TOKEN_TTL = datetime.timedelta(hours=1)


@router.post("/identities/{identity_id}/join-token", response_model=JoinToken)
def join_token(
    identity_id: str,
    request: Request,
    principal: Principal = Depends(require_principal("operator")),
    body: JoinTokenRequest | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> Any:
    """A human join token for the Identity's Default Room (identity `human-<random8>`, never the agent's identity)."""
    resolved = scoped_identity(conn, principal, identity_id)
    settings = request.app.state.settings
    participant_identity = "human-" + secrets.token_hex(4)
    room = resolved.participant.default_room
    name = body.participant_name if body is not None and body.participant_name else participant_identity
    token = (
        lkapi.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(participant_identity)
        .with_name(name)
        .with_ttl(JOIN_TOKEN_TTL)
        .with_grants(
            lkapi.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True, can_publish_data=True)
        )
        .to_jwt()
    )
    return JoinToken(livekit_url=settings.livekit_client_url, room=room, token=token, participant_identity=participant_identity)


@router.get("/identities/{identity_id}/latency", response_model=LatencyReport)
def get_latency(
    identity_id: str,
    session_id: str | None = Query(default=None),
    principal: Principal = Depends(require_principal("viewer")),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Any:
    scoped_identity(conn, principal, identity_id)
    return latency.report(conn, identity_id, session_id)
