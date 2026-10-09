"""ParticipantStatus (SOW section 14) built from DB runtime status + the resolved identity."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from typing import Any

from service015.identities_repo import get_status, list_identities
from service015.resolver import ResolvedIdentity


HEARTBEAT_STALE_S = 15.0
STARTING_STALE_S = 120.0


def _ts(value: str | None) -> float | None:
    return datetime.fromisoformat(value).timestamp() if value else None


def apply_staleness(st: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Report a dead runtime honestly: READY/BUSY with a heartbeat older than 15 s -> ERROR `heartbeat_lost`; STARTING for
    more than 120 s -> ERROR `start_timeout`. Pure and read-only (nothing is written), shared by every status reader so the
    per-identity status, the roster and the supervisor always agree."""
    now = time.time() if now is None else now
    presence = st["presence"]
    if presence in ("READY", "BUSY"):
        hb = _ts(st["heartbeat_at"])
        if hb is None or now - hb > HEARTBEAT_STALE_S:
            return {**st, "presence": "ERROR", "status_detail": "heartbeat_lost",
                    "last_error": f"no heartbeat for more than {int(HEARTBEAT_STALE_S)} s"}
    elif presence == "STARTING":
        launched = _ts(st["launched_at"])
        if launched is not None and now - launched > STARTING_STALE_S:
            return {**st, "presence": "ERROR", "status_detail": "start_timeout",
                    "last_error": f"still STARTING after {int(STARTING_STALE_S)} s"}
    return st


def participant_status(conn: sqlite3.Connection, resolved: ResolvedIdentity) -> dict[str, Any]:
    """Metadata only: no pid, prompts or engine internals."""
    st = get_status(conn, resolved.identity_id)
    return apply_staleness({
        "identity_id": resolved.identity_id,
        "participant_identity": resolved.participant.participant_identity,
        "default_room": resolved.participant.default_room,
        "presence": st.presence,
        "status_detail": st.status_detail,
        "last_error": st.last_error,
        "capabilities": {
            "audio_receive": True,
            "audio_publish": True,
            "barge_in": resolved.conversation.barge_in,
            "video_publish": False,
        },
        "launched_at": st.launched_at,
        "heartbeat_at": st.heartbeat_at,
    })


def roster(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [participant_status(conn, r) for r in list_identities(conn)]
