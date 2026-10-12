"""Meeting-Manager-facing roster (metadata only)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from service015.api.deps import get_conn
from service015.api.schemas import ParticipantStatus
from service015.auth import Principal, require_principal
from service015.participants import roster

router = APIRouter(tags=["meeting"])


@router.get("/meeting/participants", response_model=list[ParticipantStatus])
def list_participants(principal: Principal = Depends(require_principal("viewer")), conn: sqlite3.Connection = Depends(get_conn)):
    return roster(conn, principal.owner_scope)
