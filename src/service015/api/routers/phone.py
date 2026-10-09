"""Phone (Service 014 telephony adapter) routes."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends

from service015 import identities_repo
from service015.api.deps import get_conn, get_supervisor, get_telephony
from service015.api.schemas import DialRequest, PhoneCall, PhoneInfo
from service015.auth import require_role
from service015.errors import Conflict
from service015.runtime.supervisor_protocol import RuntimeSupervisor
from service015.telephony.service014 import Service014Client

router = APIRouter(tags=["phone"])

DIALABLE = ("READY", "BUSY")


@router.post(
    "/identities/{identity_id}/phone/dial",
    status_code=202,
    response_model=PhoneCall,
    dependencies=[Depends(require_role("operator"))],
)
async def dial(
    identity_id: str,
    body: DialRequest,
    conn: sqlite3.Connection = Depends(get_conn),
    sup: RuntimeSupervisor = Depends(get_supervisor),
    tel: Service014Client = Depends(get_telephony),
) -> Any:
    identity = identities_repo.get_identity(conn, identity_id)  # 404 first
    tel.ensure_configured()  # 503 before touching the runtime
    presence = (await sup.status(identity_id))["presence"]
    if presence not in DIALABLE:
        raise Conflict(
            "identity_not_ready",
            f"identity presence is {presence}; it must be READY or BUSY to take a phone call",
            details={"presence": presence, "required": list(DIALABLE)},
        )
    return await tel.connect_phone(identity, body.to)


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
