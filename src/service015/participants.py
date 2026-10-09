"""ParticipantStatus (SOW section 14) built from DB runtime status + the resolved identity."""

from __future__ import annotations

import sqlite3
from typing import Any

from service015.identities_repo import get_status, list_identities
from service015.resolver import ResolvedIdentity


def participant_status(conn: sqlite3.Connection, resolved: ResolvedIdentity) -> dict[str, Any]:
    """Metadata only: no pid, prompts or engine internals."""
    st = get_status(conn, resolved.identity_id)
    return {
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
    }


def roster(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [participant_status(conn, r) for r in list_identities(conn)]
