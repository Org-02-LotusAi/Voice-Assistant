"""Voice routes: Voicebox cloned profiles, cloning authorizations, previews."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from service015.api.deps import get_conn, get_voice_engine, get_voicebox
from service015.api.schemas import VoiceAuthorization, VoiceAuthorizationCreate, VoicePatch, VoicePreviewRequest, VoiceProfile
from service015 import accounts
from service015.auth import Principal, require_author, require_principal
from service015.errors import DomainError, NotFound, ValidationFailed
from service015.voice import authz
from service015.voice.engine import VoiceEngineClient
from service015.voice.voicebox import VoiceboxClient

log = logging.getLogger("service015.api")
router = APIRouter(tags=["voices"])

DEFAULT_PREVIEW_TEXT = "Hello, this is a preview of my voice."
MAX_SAMPLE_BYTES = 25 * 1024 * 1024
MAX_BODY_BYTES = MAX_SAMPLE_BYTES + 256 * 1024  # sample plus the text form fields; enforced by middleware on Content-Length
# application/octet-stream is what curl and many clients send for any file; the magic bytes decide for those.
ALLOWED_TYPES = {
    "audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave", "audio/mpeg", "audio/mp3", "audio/mp4", "audio/x-m4a",
    "audio/m4a", "audio/aac", "audio/ogg", "audio/flac", "audio/x-flac", "audio/webm", "video/webm", "application/octet-stream",
}
ENGINE = "chatterbox-turbo"

_Name = Annotated[str, Form(min_length=1, max_length=100, pattern=r"\S")]
_Short = Annotated[str, Form(min_length=1, max_length=200, pattern=r"\S")]
_Long = Annotated[str, Form(min_length=1, max_length=4000, pattern=r"\S")]


def sniff_audio(head: bytes) -> str | None:
    """File extension for a wav/mp3/m4a/ogg/flac/webm container judged by its magic bytes, else None."""
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == bytes.fromhex("1a45dfa3"):
        return "webm"
    return None


def safe_filename(name: str | None, ext: str) -> str:
    """Basename only, [A-Za-z0-9._-], at most 64 chars; falls back to reference.<ext>."""
    base = re.sub(r"[^A-Za-z0-9._-]", "_", (name or "").replace("\\", "/").rsplit("/", 1)[-1]).lstrip(".")[:64]
    return base or f"reference.{ext}"


def _profile(p: dict[str, Any], authorized: bool, meta: dict[str, Any] | None = None, user_id: str | None = None) -> VoiceProfile:
    """`shared` / `owned_by_me` come from the authorization record (`meta`); a profile without one is neither."""
    return VoiceProfile(
        voice_profile_id=p["id"], name=p["name"], language=p.get("language") or "en", engine=ENGINE, authorized=authorized,
        shared=bool(meta and meta["shared"]),
        owned_by_me=bool(meta and user_id is not None and meta["owner_user_id"] == user_id),
    )


async def _existing_profile(voicebox: VoiceboxClient, voice_profile_id: str) -> dict[str, Any]:
    profile = await voicebox.get_profile(voice_profile_id)
    if profile is None:
        raise NotFound(
            "voice_profile_not_found",
            f"voice profile {voice_profile_id!r} does not exist in Voicebox",
            details={"voice_profile_id": voice_profile_id},
        )
    return profile


@router.get("/voices", response_model=list[VoiceProfile])
async def list_voices(
    principal: Principal = Depends(require_principal("viewer")),
    conn: sqlite3.Connection = Depends(get_conn),
    voicebox: VoiceboxClient = Depends(get_voicebox),
):
    """Admin, tokens and env keys see every cloned profile; a human `user` / `viewer` sees shared voices plus their own
    (profiles without a recorded authorization are visible only to the unrestricted)."""
    index = authz.voice_index(conn)
    vis = principal.visibility
    return [
        _profile(p, p["id"] in index, index.get(p["id"]), principal.user_id)
        for p in await voicebox.list_profiles()
        if p.get("voice_type") == "cloned" and (not vis.restricted or authz.voice_visible(index.get(p["id"]), vis))
    ]


@router.post("/voices", status_code=201, response_model=VoiceProfile)
async def create_voice(
    name: _Name,
    reference_text: _Long,
    consent_statement: _Long,
    subject_name: _Short,
    authorized_by: _Short,
    file: UploadFile = File(...),
    principal: Principal = Depends(require_author()),
    language: Annotated[str, Form(min_length=2, max_length=20, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")] = "en",
    description: Annotated[str, Form(max_length=1000)] = "",
    conn: sqlite3.Connection = Depends(get_conn),
    voicebox: VoiceboxClient = Depends(get_voicebox),
):
    """Admin: a shared voice. User account: a voice owned by (and usable only for the personas of) the uploader. Consent
    statement, subject name and authorizing person are mandatory in both cases."""
    sample = await file.read(MAX_SAMPLE_BYTES + 1)
    if not sample:
        raise ValidationFailed("empty_sample", "the reference audio file is empty")
    if len(sample) > MAX_SAMPLE_BYTES:
        raise DomainError(
            "sample_too_large",
            f"the reference audio exceeds {MAX_SAMPLE_BYTES // (1024 * 1024)} MiB",
            status=413,
            details={"max_bytes": MAX_SAMPLE_BYTES},
        )
    ctype = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    ext = sniff_audio(sample[:16])
    if ctype not in ALLOWED_TYPES or ext is None:
        raise DomainError(
            "unsupported_media_type",
            "the reference audio must be wav, mp3, m4a, ogg, flac or webm",
            status=415,
            details={"content_type": ctype},
        )
    # Voicebox first: if it fails nothing is recorded (a failed sample upload also removes the new profile).
    profile = await voicebox.create_cloned_profile(
        name=name,
        language=language,
        description=description,
        sample_bytes=sample,
        filename=safe_filename(file.filename, ext),
        reference_text=reference_text,
    )
    try:
        authz.record_authorization(
            conn, profile["id"], authorized_by, subject_name, consent_statement, "created_via_api", principal.owner_id_for_new
        )
    except BaseException:  # includes CancelledError (client disconnect) - compensate, then re-raise
        conn.rollback()
        await asyncio.shield(voicebox.delete_profile(profile["id"]))  # never leave an unauthorized cloned voice behind
        raise
    log.info("voice created profile=%s authorized_by=%s", profile["id"], authorized_by)
    return _profile(profile, True, authz.voice_index(conn).get(profile["id"]), principal.user_id)


@router.post(
    "/voices/{voice_profile_id}/authorizations",
    status_code=201,
    response_model=VoiceAuthorization,
)
async def authorize_voice(
    voice_profile_id: str,
    body: VoiceAuthorizationCreate,
    principal: Principal = Depends(require_principal("admin")),
    conn: sqlite3.Connection = Depends(get_conn),
    voicebox: VoiceboxClient = Depends(get_voicebox),
):
    profile = await _existing_profile(voicebox, voice_profile_id)
    if profile.get("voice_type") != "cloned":
        raise ValidationFailed(
            "voice_profile_not_cloned",
            "only cloned voice profiles need (and can receive) a cloning authorization",
            details={"voice_profile_id": voice_profile_id},
        )
    existing = authz.voice_index(conn).get(voice_profile_id)  # a later authorization keeps the voice's original owner
    owner = existing["owner_user_id"] if existing is not None else principal.owner_id_for_new
    row = authz.record_authorization(
        conn, voice_profile_id, body.authorized_by, body.subject_name, body.consent_statement, body.source, owner
    )
    log.info("voice authorization recorded profile=%s authorized_by=%s", voice_profile_id, body.authorized_by)
    return row


@router.post(
    "/voices/{voice_profile_id}/preview",
    response_class=Response,
    responses={200: {"content": {"audio/wav": {}}}},
)
async def preview_voice(
    voice_profile_id: str,
    principal: Principal = Depends(require_principal("operator")),
    body: VoicePreviewRequest | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
    voicebox: VoiceboxClient = Depends(get_voicebox),
    engine: VoiceEngineClient = Depends(get_voice_engine),
):
    await _existing_profile(voicebox, voice_profile_id)
    meta = authz.voice_index(conn).get(voice_profile_id)
    if principal.visibility.restricted and not authz.voice_visible(meta, principal.visibility):
        raise NotFound(  # same answer as a missing profile: no probing of other users' voice ids
            "voice_profile_not_found",
            f"voice profile {voice_profile_id!r} does not exist in Voicebox",
            details={"voice_profile_id": voice_profile_id},
        )
    if meta is None:
        raise ValidationFailed(
            "voice_not_authorized",
            f"voice profile {voice_profile_id!r} has no recorded cloning authorization",
            details={"voice_profile_id": voice_profile_id},
        )
    if meta is not None and meta["disabled"]:
        raise ValidationFailed(
            "voice_disabled", f"voice profile {voice_profile_id!r} has been disabled by an administrator",
            details={"voice_profile_id": voice_profile_id},
        )
    text = (body.text if body is not None and body.text else None) or DEFAULT_PREVIEW_TEXT
    wav = await engine.preview(voice_profile_id, text)
    return Response(wav, media_type="audio/wav")


@router.patch("/voices/{voice_profile_id}", response_model=VoiceProfile)
async def patch_voice(
    voice_profile_id: str,
    body: VoicePatch,
    principal: Principal = Depends(require_principal("admin")),
    conn: sqlite3.Connection = Depends(get_conn),
    voicebox: VoiceboxClient = Depends(get_voicebox),
):
    """Admin: disable/enable (blocks new attachment and launch; running personas are not interrupted) or hand the voice to a user."""
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if not changes:
        raise ValidationFailed("empty_patch", "give at least one of disabled, owner_user_id")
    if "disabled" in changes and changes["disabled"] is None:
        raise ValidationFailed("invalid_request", "disabled must be true or false", details={"field": "disabled"})
    profile = await _existing_profile(voicebox, voice_profile_id)
    if changes.get("owner_user_id") is not None:
        accounts.require_assignable_owner(conn, changes["owner_user_id"])  # 404 user_not_found / 422 invalid_owner
    if "owner_user_id" in changes:
        authz.set_voice_owner(conn, voice_profile_id, changes["owner_user_id"])
    if "disabled" in changes:
        authz.set_voice_disabled(conn, voice_profile_id, changes["disabled"])
    log.info("voice updated profile=%s fields=%s", voice_profile_id, sorted(changes))
    return _profile(profile, True, authz.voice_index(conn).get(voice_profile_id), principal.user_id)
