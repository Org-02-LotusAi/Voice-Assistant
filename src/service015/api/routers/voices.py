"""Voice routes: Voicebox cloned profiles, cloning authorizations, previews."""

from __future__ import annotations

import logging
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from service015.api.deps import get_conn, get_voice_engine, get_voicebox
from service015.api.schemas import VoiceAuthorization, VoiceAuthorizationCreate, VoicePreviewRequest, VoiceProfile
from service015.auth import require_role
from service015.errors import NotFound, ValidationFailed
from service015.voice import authz
from service015.voice.engine import VoiceEngineClient
from service015.voice.voicebox import VoiceboxClient

log = logging.getLogger("service015.api")
router = APIRouter(tags=["voices"])

DEFAULT_PREVIEW_TEXT = "Hello, this is a preview of my voice."
MAX_SAMPLE_BYTES = 25 * 1024 * 1024
ENGINE = "chatterbox-turbo"

_Name = Annotated[str, Form(min_length=1, max_length=100, pattern=r"\S")]
_Short = Annotated[str, Form(min_length=1, max_length=200, pattern=r"\S")]
_Long = Annotated[str, Form(min_length=1, max_length=4000, pattern=r"\S")]


def _profile(p: dict[str, Any], authorized: bool) -> VoiceProfile:
    return VoiceProfile(
        voice_profile_id=p["id"], name=p["name"], language=p.get("language") or "en", engine=ENGINE, authorized=authorized
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


@router.get("/voices", response_model=list[VoiceProfile], dependencies=[Depends(require_role("viewer"))])
async def list_voices(conn: sqlite3.Connection = Depends(get_conn), voicebox: VoiceboxClient = Depends(get_voicebox)):
    authorized = authz.authorized_ids(conn)
    return [_profile(p, p["id"] in authorized) for p in await voicebox.list_profiles() if p.get("voice_type") == "cloned"]


@router.post("/voices", status_code=201, response_model=VoiceProfile, dependencies=[Depends(require_role("admin"))])
async def create_voice(
    name: _Name,
    reference_text: _Long,
    consent_statement: _Long,
    subject_name: _Short,
    authorized_by: _Short,
    file: UploadFile = File(...),
    language: Annotated[str, Form(min_length=2, max_length=20, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")] = "en",
    description: Annotated[str, Form(max_length=1000)] = "",
    conn: sqlite3.Connection = Depends(get_conn),
    voicebox: VoiceboxClient = Depends(get_voicebox),
):
    sample = await file.read(MAX_SAMPLE_BYTES + 1)
    if not sample:
        raise ValidationFailed("empty_sample", "the reference audio file is empty")
    if len(sample) > MAX_SAMPLE_BYTES:
        raise ValidationFailed(
            "sample_too_large", f"the reference audio exceeds {MAX_SAMPLE_BYTES // (1024 * 1024)} MiB", details={"max_bytes": MAX_SAMPLE_BYTES}
        )
    # Voicebox first: if it fails nothing is recorded (a failed sample upload also removes the new profile).
    profile = await voicebox.create_cloned_profile(
        name=name,
        language=language,
        description=description,
        sample_bytes=sample,
        filename=file.filename or "reference.wav",
        reference_text=reference_text,
    )
    try:
        authz.record_authorization(conn, profile["id"], authorized_by, subject_name, consent_statement, "created_via_api")
    except Exception:
        conn.rollback()
        await voicebox.delete_profile(profile["id"])  # never leave an unauthorized cloned voice behind
        raise
    log.info("voice created profile=%s authorized_by=%s", profile["id"], authorized_by)
    return _profile(profile, True)


@router.post(
    "/voices/{voice_profile_id}/authorizations",
    status_code=201,
    response_model=VoiceAuthorization,
    dependencies=[Depends(require_role("admin"))],
)
async def authorize_voice(
    voice_profile_id: str,
    body: VoiceAuthorizationCreate,
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
    row = authz.record_authorization(
        conn, voice_profile_id, body.authorized_by, body.subject_name, body.consent_statement, body.source
    )
    log.info("voice authorization recorded profile=%s authorized_by=%s", voice_profile_id, body.authorized_by)
    return row


@router.post(
    "/voices/{voice_profile_id}/preview",
    response_class=Response,
    responses={200: {"content": {"audio/wav": {}}}},
    dependencies=[Depends(require_role("operator"))],
)
async def preview_voice(
    voice_profile_id: str,
    body: VoicePreviewRequest | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
    voicebox: VoiceboxClient = Depends(get_voicebox),
    engine: VoiceEngineClient = Depends(get_voice_engine),
):
    await _existing_profile(voicebox, voice_profile_id)
    if not authz.is_authorized(conn, voice_profile_id):
        raise ValidationFailed(
            "voice_not_authorized",
            f"voice profile {voice_profile_id!r} has no recorded cloning authorization",
            details={"voice_profile_id": voice_profile_id},
        )
    text = (body.text if body is not None and body.text else None) or DEFAULT_PREVIEW_TEXT
    wav = await engine.preview(voice_profile_id, text)
    return Response(wav, media_type="audio/wav")
