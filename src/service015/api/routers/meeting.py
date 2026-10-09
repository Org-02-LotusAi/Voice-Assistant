"""Meeting-Manager-facing roster (metadata only)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from service015.api.deps import get_conn
from service015.api.schemas import ParticipantStatus
from service015.auth import require_role
from service015.participants import roster

router = APIRouter(tags=["meeting"])


@router.get("/meeting/participants", response_model=list[ParticipantStatus], dependencies=[Depends(require_role("viewer"))])
def list_participants(conn: sqlite3.Connection = Depends(get_conn)):
    return roster(conn)
