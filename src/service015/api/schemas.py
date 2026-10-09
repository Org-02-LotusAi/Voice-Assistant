"""API-only request/response models (mirror components/schemas in openapi.yaml)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(_Strict):
    status: Literal["ok", "degraded"]
    version: str
    dependencies: dict[str, str]


class InstantiateRequest(_Strict):
    overrides: dict[str, Any] = Field(default_factory=dict)
    template_version: int | None = Field(default=None, ge=1)


class PatchIdentityRequest(_Strict):
    overrides: dict[str, Any]


class IdentitySummary(_Strict):
    identity_id: str
    name: str
    template_id: str
    template_version: int
    company: str | None
    role: str | None
    language: str
    voice_profile_id: str | None
    participant_identity: str
    default_room: str
    resolved_hash: str


class SelfContext(_Strict):
    self_context: str
    instructions: str
    resolved_hash: str


class Capabilities(_Strict):
    audio_receive: bool
    audio_publish: bool
    barge_in: bool
    video_publish: bool


class ParticipantStatus(_Strict):
    identity_id: str
    participant_identity: str
    default_room: str
    presence: Literal["READY", "STARTING", "BUSY", "ERROR", "OFFLINE"]
    status_detail: str | None
    last_error: str | None
    capabilities: Capabilities
    launched_at: str | None
    heartbeat_at: str | None


class DialRequest(_Strict):
    to: str = Field(pattern=r"^\+[1-9][0-9]{1,14}$")


class PhoneCall(_Strict):
    call_id: str
    room: str
    status: str


class InboundRoute(_Strict):
    kind: str
    room_name: str


class PhoneInfo(_Strict):
    mode: Literal["disabled", "http", "livekit_sip"]
    default_room: str
    inbound: InboundRoute | None


class ExperienceCreate(_Strict):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    source: str = Field(min_length=1, max_length=500)
    repo: str | None = Field(default=None, min_length=1, max_length=500)
    branch: str | None = Field(default=None, min_length=1, max_length=200)
    sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,40}$")
    min_role: Literal["viewer", "operator", "admin"] = "viewer"


class ExpertKnowledgeItem(_Strict):
    id: int
    kind: Literal["document", "verified_experience"]
    title: str
    source: str
    repo: str
    branch: str
    sha: str
    min_role: Literal["viewer", "operator", "admin"]
    created_at: str
    dirty: bool


class ExpertKnowledgeList(_Strict):
    items: list[ExpertKnowledgeItem]
    missing_required: list[str]


class ExpertSession(_Strict):
    session_id: str


class AskRequest(_Strict):
    question: str = Field(min_length=1, max_length=2000)


class Citation(_Strict):
    id: int
    kind: Literal["document", "verified_experience"]
    title: str
    source: str
    repo: str
    branch: str
    sha: str


class AskResponse(_Strict):
    answer: str
    citations: list[Citation]


class VoiceProfile(_Strict):
    voice_profile_id: str
    name: str
    language: str
    engine: Literal["chatterbox-turbo"]
    authorized: bool


class VoiceAuthorizationCreate(_Strict):
    consent_statement: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    subject_name: str = Field(min_length=1, max_length=200, pattern=r"\S")
    authorized_by: str = Field(min_length=1, max_length=200, pattern=r"\S")
    source: str = Field(default="voicebox_direct", min_length=1, max_length=200, pattern=r"\S")


class VoiceAuthorization(_Strict):
    id: int
    voice_profile_id: str
    authorized_by: str
    subject_name: str
    consent_statement: str
    source: str
    created_at: str


class VoicePreviewRequest(_Strict):
    text: str | None = Field(default=None, min_length=1, max_length=500, pattern=r"\S")
