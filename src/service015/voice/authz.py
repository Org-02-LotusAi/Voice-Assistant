"""Cloning authorizations (`voice_authorizations`) and the 'usable voice' rule for Identities."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from service015.errors import NotFound, ValidationFailed
from service015.scope import UNRESTRICTED, Visibility
from service015.voice import deepgram as dg
from service015.voice.voicebox import VoiceboxClient


def record_authorization(
    conn: sqlite3.Connection,
    voice_profile_id: str,
    authorized_by: str,
    subject_name: str,
    consent_statement: str,
    source: str,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO voice_authorizations (voice_profile_id, authorized_by, subject_name, consent_statement, source, created_at, "
        "owner_user_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (voice_profile_id, authorized_by, subject_name, consent_statement, source, created_at, owner_user_id),
    )
    conn.commit()
    return {
        "id": cur.lastrowid,
        "voice_profile_id": voice_profile_id,
        "authorized_by": authorized_by,
        "subject_name": subject_name,
        "consent_statement": consent_statement,
        "source": source,
        "created_at": created_at,
    }


def is_authorized(conn: sqlite3.Connection, voice_profile_id: str) -> bool:
    return conn.execute("SELECT 1 FROM voice_authorizations WHERE voice_profile_id = ? LIMIT 1", (voice_profile_id,)).fetchone() is not None


def authorized_ids(conn: sqlite3.Connection) -> set[str]:
    return {r["voice_profile_id"] for r in conn.execute("SELECT DISTINCT voice_profile_id FROM voice_authorizations")}


def voice_index(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """profile_id -> {owner_user_id, shared, disabled}. The first authorization row decides the owner; a voice is shared when
    unowned or owned by an admin (seed / admin voices), and disabled when any of its rows is disabled."""
    rows = conn.execute(
        "SELECT a.voice_profile_id, a.owner_user_id, a.disabled, u.role AS owner_role FROM voice_authorizations a "
        "LEFT JOIN users u ON u.user_id = a.owner_user_id ORDER BY a.id"
    ).fetchall()
    index: dict[str, dict[str, Any]] = {}
    for r in rows:
        meta = index.get(r["voice_profile_id"])
        if meta is None:
            index[r["voice_profile_id"]] = {
                "owner_user_id": r["owner_user_id"],
                "shared": r["owner_user_id"] is None or r["owner_role"] == "admin",
                "disabled": bool(r["disabled"]),
            }
        elif r["disabled"]:
            meta["disabled"] = True
    return index


def voice_visible(meta: dict[str, Any] | None, vis: Visibility) -> bool:
    return meta is not None and vis.allows(meta["owner_user_id"], meta["shared"])


def set_voice_disabled(conn: sqlite3.Connection, voice_profile_id: str, disabled: bool) -> None:
    cur = conn.execute("UPDATE voice_authorizations SET disabled = ? WHERE voice_profile_id = ?", (int(disabled), voice_profile_id))
    conn.commit()
    if cur.rowcount == 0:
        raise NotFound(
            "voice_profile_not_found", f"voice profile {voice_profile_id!r} has no authorization record",
            details={"voice_profile_id": voice_profile_id},
        )


def consent_index(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """profile_id -> the first (original) consent record: authorized_by, subject_name, consent_statement, source, created_at."""
    index: dict[str, dict[str, Any]] = {}
    for r in conn.execute(
        "SELECT voice_profile_id, authorized_by, subject_name, consent_statement, source, created_at "
        "FROM voice_authorizations ORDER BY id"
    ):
        index.setdefault(r["voice_profile_id"], {k: r[k] for k in r.keys() if k != "voice_profile_id"})
    return index


def set_voice_owner(conn: sqlite3.Connection, voice_profile_id: str, owner_user_id: str | None) -> None:
    """Hand the voice (every authorization row of it) to `owner_user_id`; None makes it unowned (shared)."""
    cur = conn.execute(
        "UPDATE voice_authorizations SET owner_user_id = ? WHERE voice_profile_id = ?", (owner_user_id, voice_profile_id)
    )
    conn.commit()
    if cur.rowcount == 0:
        raise NotFound(
            "voice_profile_not_found", f"voice profile {voice_profile_id!r} has no authorization record",
            details={"voice_profile_id": voice_profile_id},
        )


def owner_visibility(conn: sqlite3.Connection, owner_user_id: str | None) -> Visibility:
    """What an identity's owner may use: unowned (machine) and admin-owned personas are unrestricted, others see shared + own."""
    if owner_user_id is None:
        return UNRESTRICTED
    row = conn.execute("SELECT role FROM users WHERE user_id = ?", (owner_user_id,)).fetchone()
    if row is None or row["role"] == "admin":
        return UNRESTRICTED
    return Visibility(True, owner_user_id)


def voice_usable_by(conn: sqlite3.Connection, voice_profile_id: str, owner_user_id: str | None) -> bool:
    """False when the voice has an authorization record that the owner may not use (private to someone else)."""
    meta = voice_index(conn).get(voice_profile_id)
    return meta is None or owner_visibility(conn, owner_user_id).allows(meta["owner_user_id"], meta["shared"])


def check_voice_provider(profile_id: str, tts_provider: str) -> bool:
    """True when `profile_id` is a Deepgram catalog voice that is usable now (tts_provider == deepgram).

    False means a local profile id under the local provider (the caller continues with the Voicebox checks). Raises
    ValidationFailed `voice_provider_mismatch` when the id belongs to the other provider, or `voice_profile_not_found` for an
    unknown `deepgram:` voice. Never touches Voicebox, so a mismatch is reported even while Voicebox is down.
    """
    deepgram_id = dg.is_deepgram_voice_id(profile_id)
    if tts_provider == "deepgram":
        if not deepgram_id:
            raise ValidationFailed(
                "voice_provider_mismatch",
                f"voice {profile_id!r} is a local (cloned) voice but this service speaks through Deepgram "
                "(SERVICE015_TTS_PROVIDER=deepgram); choose a Deepgram voice or switch the provider back to local",
                details={"voice_profile_id": profile_id, "tts_provider": tts_provider},
            )
        if dg.find_voice(profile_id) is None:
            raise ValidationFailed(
                "voice_profile_not_found", f"voice {profile_id!r} is not a known Deepgram Aura-2 voice",
                details={"voice_profile_id": profile_id},
            )
        return True
    if deepgram_id:
        raise ValidationFailed(
            "voice_provider_mismatch",
            f"voice {profile_id!r} is a Deepgram voice but this service uses the local TTS engine "
            "(SERVICE015_TTS_PROVIDER=local); choose a cloned voice or switch the provider to deepgram",
            details={"voice_profile_id": profile_id, "tts_provider": tts_provider},
        )
    return False


async def ensure_voice_usable(
    conn: sqlite3.Connection, voicebox: VoiceboxClient, profile_id: Any, vis: Visibility | None = None,
    tts_provider: str = "local",
) -> None:
    """None is fine (no voice). Otherwise the profile must exist in Voicebox AND carry an authorization row.

    Raises ValidationFailed `voice_profile_not_found` / `voice_not_authorized`, or DependencyUnavailable
    `voicebox_unavailable` (from the client) - always before anything is persisted. A disabled voice is `voice_disabled`;
    with a restricted `vis`, a voice that is neither shared nor owned by the caller is reported as `voice_profile_not_found`.
    A non-string value is left for the resolver's own override validation to reject.
    """
    if not isinstance(profile_id, str):
        return
    if check_voice_provider(profile_id, tts_provider):
        return  # a catalog Deepgram voice: stock voice, no cloning authorization, owner or Voicebox involved
    if await voicebox.get_profile(profile_id) is None:
        raise ValidationFailed(
            "voice_profile_not_found",
            f"voice profile {profile_id!r} does not exist in Voicebox",
            details={"voice_profile_id": profile_id},
        )
    meta = voice_index(conn).get(profile_id)
    if vis is not None and vis.restricted and not voice_visible(meta, vis):
        # same answer as a missing profile: a restricted caller cannot probe which ids exist or are authorized
        raise ValidationFailed(
            "voice_profile_not_found",
            f"voice profile {profile_id!r} does not exist in Voicebox",
            details={"voice_profile_id": profile_id},
        )
    if meta is None:
        raise ValidationFailed(
            "voice_not_authorized",
            f"voice profile {profile_id!r} has no recorded cloning authorization",
            details={"voice_profile_id": profile_id},
        )
    if meta is not None and meta["disabled"]:
        raise ValidationFailed(
            "voice_disabled", f"voice profile {profile_id!r} has been disabled by an administrator",
            details={"voice_profile_id": profile_id},
        )


async def ensure_identity_voice_ready(
    conn: sqlite3.Connection, voicebox: VoiceboxClient, resolved: Any, vis: Visibility | None = None,
    tts_provider: str = "local",
) -> None:
    """Launch-time preflight: the Identity must have a voice, and it must still exist and be authorized.

    Instantiate/PATCH may leave `voice.profile_id` null (a draft Identity); launching may not, so the runtime
    never falls back to a default voice. Raises 422 `voice_required` / `voice_profile_not_found` /
    `voice_not_authorized` / `voice_provider_mismatch`, or 503 `voicebox_unavailable`.
    """
    profile_id = resolved.voice.profile_id
    if profile_id is None:
        raise ValidationFailed(
            "voice_required",
            "this identity has no voice; attach an authorized voice (voice.profile_id) before launching",
            details={"identity_id": resolved.identity_id},
        )
    await ensure_voice_usable(conn, voicebox, profile_id, vis, tts_provider)
