"""Phone (Service 014 telephony adapter) routes."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request

from service015 import identities_repo
from service015.api.deps import get_conn, get_supervisor, get_telephony
from service015.api.schemas import DialRequest, PhoneCall, PhoneInfo
from service015.auth import require_role
from service015.config import is_local_public_url
from service015.errors import Conflict, DomainError
from service015.runtime.supervisor_protocol import RuntimeSupervisor
from service015.telephony.service014 import Service014Client

router = APIRouter(tags=["phone"])

DIALABLE = ("READY", "BUSY")


def check_destination_allowed(settings: Any, to: str) -> None:
    """Allowlist of E.164 prefixes. Empty list: allow everything on localhost, deny everything on a public deployment."""
    prefixes = settings.service014_allowed_destinations
    if not prefixes:
        if is_local_public_url(settings.public_url):
            return
        raise DomainError(
            "destination_not_allowed",
            "SERVICE014_ALLOWED_DESTINATIONS is empty; phone dialing is disabled on a public deployment until prefixes are listed",
            status=403,
        )
    if not any(to.startswith(p) for p in prefixes):
        raise DomainError("destination_not_allowed", "the destination is not in SERVICE014_ALLOWED_DESTINATIONS", status=403)


@router.post(
    "/identities/{identity_id}/phone/dial",
    status_code=202,
    response_model=PhoneCall,
    dependencies=[Depends(require_role("operator"))],
)
async def dial(
    request: Request,
    identity_id: str,
    body: DialRequest,
    conn: sqlite3.Connection = Depends(get_conn),
    sup: RuntimeSupervisor = Depends(get_supervisor),
    tel: Service014Client = Depends(get_telephony),
) -> Any:
    identity = identities_repo.get_identity(conn, identity_id)  # 404 first
    tel.ensure_configured()  # 503 before touching the runtime
    check_destination_allowed(request.app.state.settings, body.to)
    presence = (await sup.status(identity_id))["presence"]
    if presence not in DIALABLE:
        raise Conflict(
            "identity_not_ready",
            f"identity presence is {presence}; it must be READY or BUSY to take a phone call",
            details={"presence": presence, "required": list(DIALABLE)},
        )
    dialing: set[str] = request.app.state.__dict__.setdefault("dialing", set())
    if identity_id in dialing:  # a dial blocks until answered (up to 60 s); a second one would ring twice
        raise Conflict("dial_in_progress", "a phone call for this identity is already being placed", details={"identity_id": identity_id})
    dialing.add(identity_id)
    try:
        return await tel.connect_phone(identity, body.to)
    finally:
        dialing.discard(identity_id)


@router.get(
    "/identities/{identity_id}/phone", response_model=PhoneInfo, dependencies=[Depends(require_role("viewer"))]
)
def phone_info(
    identity_id: str, conn: sqlite3.Connection = Depends(get_conn), tel: Service014Client = Depends(get_telephony)
) -> Any:
    identity = identities_repo.get_identity(conn, identity_id)
    return {
        "mode": tel.mode,
        "default_room": identity.participant.default_room,
        "inbound": tel.inbound_route(identity),
    }
