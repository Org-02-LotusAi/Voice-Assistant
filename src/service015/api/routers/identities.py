"""Identity routes."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends

from service015 import identities_repo
from service015.api.deps import get_conn, get_supervisor, get_voicebox
from service015.api.schemas import IdentitySummary, ParticipantStatus, PatchIdentityRequest, SelfContext
from service015.auth import require_role
from service015.resolver import ResolvedIdentity
from service015.runtime.supervisor_protocol import RuntimeSupervisor
from service015.selfcontext import build_instructions, build_self_context
from service015.voice.authz import ensure_identity_voice_ready, ensure_voice_usable
from service015.voice.voicebox import VoiceboxClient

router = APIRouter(tags=["identities"])


def _summary(r: ResolvedIdentity) -> IdentitySummary:
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
    )


@router.get("/identities", response_model=list[IdentitySummary], dependencies=[Depends(require_role("viewer"))])
def list_identities(conn: sqlite3.Connection = Depends(get_conn)):
    return [_summary(r) for r in identities_repo.list_identities(conn)]


@router.get("/identities/{identity_id}", response_model=ResolvedIdentity, dependencies=[Depends(require_role("viewer"))])
def get_identity(identity_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    return identities_repo.get_identity(conn, identity_id)


@router.patch("/identities/{identity_id}", response_model=ResolvedIdentity, dependencies=[Depends(require_role("operator"))])
async def patch_identity(
    identity_id: str,
    body: PatchIdentityRequest,
    conn: sqlite3.Connection = Depends(get_conn),
    voicebox: VoiceboxClient = Depends(get_voicebox),
):
    before, after = identities_repo.patched_voice_profile_id(conn, identity_id, body.overrides)
    if after != before:  # an unchanged voice was validated when it was attached; unrelated patches don't need Voicebox
        await ensure_voice_usable(conn, voicebox, after)
    return identities_repo.patch_identity(conn, identity_id, body.overrides)


@router.get(
    "/identities/{identity_id}/self-context", response_model=SelfContext, dependencies=[Depends(require_role("viewer"))]
)
def get_self_context(identity_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    r = identities_repo.get_identity(conn, identity_id)
    return SelfContext(
        self_context=build_self_context(r), instructions=build_instructions(r), resolved_hash=r.resolved_hash
    )


@router.post(
    "/identities/{identity_id}/launch", response_model=ParticipantStatus, dependencies=[Depends(require_role("operator"))]
)
async def launch(
    identity_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    sup: RuntimeSupervisor = Depends(get_supervisor),
    voicebox: VoiceboxClient = Depends(get_voicebox),
) -> Any:
    identity = identities_repo.get_identity(conn, identity_id)  # 404 before touching the runtime
    await ensure_identity_voice_ready(conn, voicebox, identity)  # 422/503 before the supervisor is involved
    return await sup.launch(identity_id)


@router.post(
    "/identities/{identity_id}/stop", response_model=ParticipantStatus, dependencies=[Depends(require_role("operator"))]
)
async def stop(
    identity_id: str, conn: sqlite3.Connection = Depends(get_conn), sup: RuntimeSupervisor = Depends(get_supervisor)
) -> Any:
    identities_repo.get_identity(conn, identity_id)
    return await sup.stop(identity_id)


@router.get(
    "/identities/{identity_id}/status", response_model=ParticipantStatus, dependencies=[Depends(require_role("viewer"))]
)
async def status(
    identity_id: str, conn: sqlite3.Connection = Depends(get_conn), sup: RuntimeSupervisor = Depends(get_supervisor)
) -> Any:
    identities_repo.get_identity(conn, identity_id)
    return await sup.status(identity_id)
