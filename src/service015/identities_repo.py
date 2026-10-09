"""Identity storage: id allocation, instantiation bound to a template version, patching, runtime status."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict

from service015.errors import CorruptIdentity, NotFound, ValidationFailed
from service015.resolver import ResolvedIdentity, canonical_json, compute_hash, resolve
from service015.templates_repo import get_template

PRESENCE_VALUES: tuple[str, ...] = ("READY", "STARTING", "BUSY", "ERROR", "OFFLINE")
_MAX_SLUG_LEN = 48


class RuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_id: str
    presence: str
    status_detail: str | None = None
    pid: int | None = None
    launched_at: str | None = None
    heartbeat_at: str | None = None
    last_error: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(name: str) -> str:
    """Lowercase ascii alphanumerics and '-', repeats collapsed; 'identity' if nothing is left."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:_MAX_SLUG_LEN].strip("-")
    return slug or "identity"


def allocate_identity_id(conn: sqlite3.Connection, name: str) -> str:
    """Next free `<slug>-NNN`. Callers that insert must hold the write lock (instantiate does)."""
    slug = slugify(name)
    pattern = re.compile(rf"^{re.escape(slug)}-(\d{{3,}})$")
    rows = conn.execute(
        "SELECT identity_id FROM identities WHERE identity_id LIKE ? ESCAPE '\\'",
        (slug.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "-%",),
    ).fetchall()
    used = [int(m.group(1)) for r in rows if (m := pattern.match(r["identity_id"]))]
    return f"{slug}-{(max(used) + 1 if used else 1):03d}"


def _row_to_resolved(row: sqlite3.Row) -> ResolvedIdentity:
    resolved = ResolvedIdentity.model_validate_json(row["resolved_json"])
    body = resolved.to_json_obj()
    body.pop("resolved_hash")
    if compute_hash(body) != resolved.resolved_hash or resolved.resolved_hash != row["resolved_hash"]:
        raise CorruptIdentity(
            "corrupt_identity",
            f"stored identity {row['identity_id']!r} does not match its resolved_hash",
            details={"identity_id": row["identity_id"]},
        )
    return resolved


def _store_json(resolved: ResolvedIdentity) -> str:
    return canonical_json(resolved.to_json_obj())


def instantiate(
    conn: sqlite3.Connection, template_id: str, overrides: dict[str, Any], template_version: int | None = None
) -> ResolvedIdentity:
    """Create an identity bound to `template_version` (latest when None) plus an OFFLINE status row."""
    template = get_template(conn, template_id, template_version)
    name = overrides.get("name")
    now = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        identity_id = allocate_identity_id(conn, name if isinstance(name, str) and name.strip() else template.name)
        resolved = resolve(template, overrides, identity_id)
        conn.execute(
            """
            INSERT INTO identities (identity_id, template_id, template_version, overrides_json, resolved_json,
                                    resolved_hash, participant_identity, default_room, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identity_id,
                template.template_id,
                template.version,
                canonical_json(overrides),
                _store_json(resolved),
                resolved.resolved_hash,
                resolved.participant.participant_identity,
                resolved.participant.default_room,
                now,
                now,
            ),
        )
        conn.execute("INSERT INTO runtime_status (identity_id, presence) VALUES (?, 'OFFLINE')", (identity_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return resolved


def _get_row(conn: sqlite3.Connection, identity_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM identities WHERE identity_id = ?", (identity_id,)).fetchone()
    if row is None:
        raise NotFound("identity_not_found", f"identity {identity_id!r} not found", details={"identity_id": identity_id})
    return row


def get_identity(conn: sqlite3.Connection, identity_id: str) -> ResolvedIdentity:
    return _row_to_resolved(_get_row(conn, identity_id))


def list_identities(conn: sqlite3.Connection) -> list[ResolvedIdentity]:
    rows = conn.execute("SELECT * FROM identities ORDER BY identity_id").fetchall()
    return [_row_to_resolved(r) for r in rows]


def patch_identity(conn: sqlite3.Connection, identity_id: str, overrides_patch: dict[str, Any]) -> ResolvedIdentity:
    """Merge `overrides_patch` into stored overrides; validate against the BOUND template version."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _get_row(conn, identity_id)
        template = get_template(conn, row["template_id"], row["template_version"])
        merged = {**json.loads(row["overrides_json"]), **overrides_patch}
        for key in [k for k, v in overrides_patch.items() if v is None]:
            merged.pop(key)  # a null patch value clears that override (back to the template default)
        resolved = resolve(template, merged, identity_id)
        conn.execute(
            "UPDATE identities SET overrides_json = ?, resolved_json = ?, resolved_hash = ?, updated_at = ? "
            "WHERE identity_id = ?",
            (canonical_json(merged), _store_json(resolved), resolved.resolved_hash, _now(), identity_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return resolved


def patched_voice_profile_id(
    conn: sqlite3.Connection, identity_id: str, overrides_patch: dict[str, Any]
) -> tuple[str | None, str | None]:
    """(current, after-patch) resolved `voice.profile_id`, computed with patch_identity's merge rules (read-only)."""
    row = _get_row(conn, identity_id)
    template = get_template(conn, row["template_id"], row["template_version"])
    current = json.loads(row["overrides_json"])
    merged = {**current, **overrides_patch}
    for key in [k for k, v in overrides_patch.items() if v is None]:
        merged.pop(key)
    default = template.voice.default_voice_profile_id
    return current.get("voice.profile_id", default), merged.get("voice.profile_id", default)


def get_status(conn: sqlite3.Connection, identity_id: str) -> RuntimeStatus:
    row = conn.execute(
        "SELECT identity_id, presence, status_detail, pid, launched_at, heartbeat_at, last_error "
        "FROM runtime_status WHERE identity_id = ?",
        (identity_id,),
    ).fetchone()
    if row is None:
        raise NotFound("identity_not_found", f"identity {identity_id!r} not found", details={"identity_id": identity_id})
    return RuntimeStatus(**dict(row))


def set_status(
    conn: sqlite3.Connection,
    identity_id: str,
    presence: str,
    detail: str | None = None,
    error: str | None = None,
    pid: int | None = None,
) -> RuntimeStatus:
    """Set presence. detail/error are overwritten; pid is kept when None (cleared on OFFLINE).

    STARTING stamps launched_at; OFFLINE clears launched_at, heartbeat_at and pid.
    """
    if presence not in PRESENCE_VALUES:
        raise ValidationFailed(
            "invalid_presence",
            f"presence {presence!r} is not one of {list(PRESENCE_VALUES)}",
            details={"presence": presence, "allowed": list(PRESENCE_VALUES)},
        )
    try:
        conn.execute("BEGIN IMMEDIATE")  # read-modify-write under the write lock: no lost heartbeat/pid updates
        current = get_status(conn, identity_id)
        now = _now()
        if presence == "OFFLINE":
            new_pid, launched, heartbeat = None, None, None
        else:
            new_pid = pid if pid is not None else current.pid
            launched = now if presence == "STARTING" else current.launched_at
            heartbeat = current.heartbeat_at
        conn.execute(
            "UPDATE runtime_status SET presence = ?, status_detail = ?, last_error = ?, pid = ?, launched_at = ?, "
            "heartbeat_at = ? WHERE identity_id = ?",
            (presence, detail, error, new_pid, launched, heartbeat, identity_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_status(conn, identity_id)


def heartbeat(conn: sqlite3.Connection, identity_id: str) -> RuntimeStatus:
    get_status(conn, identity_id)  # NotFound for unknown ids
    conn.execute("UPDATE runtime_status SET heartbeat_at = ? WHERE identity_id = ?", (_now(), identity_id))
    conn.commit()
    return get_status(conn, identity_id)
